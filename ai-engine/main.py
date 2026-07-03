"""
AI D&D Gamemaster Engine — Main Application

FastAPI server that:
1. Connects to FoundryVTT relay via WebSocket
2. Listens for player chat messages
3. Processes them through an LLM
4. Executes GM actions in Foundry
5. Serves the admin web panel
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

# Add the ai-engine directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from foundry.client import FoundryClient
from foundry.chat_listener import ChatListener
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
from actions.executors import ExecutionError, _require
from state.tracker import GameStateTracker
from state.models import GameState, GameMode, CombatState
from persistence.db import Database
from campaign.vault import CampaignStore, CampaignNotFound
from context.loader import CampaignLoader
from context.window_manager import ContextWindowManager
from context.reinforcement_manager import ContextReinforcementManager
from combat.loop import CombatLoop
from scene.awareness import SceneAwareness
from relay_proc.manager import RelayManager
from utils.path_safety import sanitize_filename
from utils.tasks import spawn
from tts.service import TTSService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ai-gm.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ai-gm")


# --- Pydantic Models for API ---

class GMSettings(BaseModel):
    model: str = settings.model
    llm_base_url: str = ""
    llm_api_key: str = ""
    temperature: float = settings.temperature
    ai_name: str = settings.ai_name
    ai_tone: str = settings.ai_tone
    relay_url: str = settings.relay_url
    relay_api_key: str = ""
    comfyui_url: str = settings.comfyui_url


class CampaignCreate(BaseModel):
    name: str
    vault_files: List[str] = []
    description: str = ""


class SessionInfo(BaseModel):
    session_id: str
    active: bool = True
    campaign: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class EventEntry(BaseModel):
    description: str
    timestamp: Optional[str] = None


class StateUpdate(BaseModel):
    mode: Optional[str] = None
    scene: Optional[str] = None
    session: Optional[int] = None
    campaign: Optional[str] = None


# AppState, get_app_state, ErrorResponse, ApiError, require_foundry now live in
# api/deps.py so routers can import them without a circular import on main.
from api.deps import AppState, get_app_state, ErrorResponse, ApiError, require_foundry  # noqa: E402


# --- Context Manager ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    # Initialize AppState for dependency injection
    app.state = AppState()

    logger.info("Initializing AI Gamemaster Engine...")

    # 0. Launch the embedded relay (must be up before the Foundry client connects)
    from relay_proc import RelayManager
    relay_manager = RelayManager()
    app.state.relay_manager = relay_manager
    if settings.relay_managed:
        await relay_manager.start()
        await relay_manager.ensure_api_key()
        await relay_manager.ensure_rest_scoped_key()
        # Headless session setup must never crash startup — if Chrome/Foundry is
        # not ready, the engine should still come up and reconnect in the
        # background rather than exiting (which left an orphaned relay holding
        # the GM seat, wedging every subsequent launch).
        try:
            headless_client_id = await relay_manager.ensure_headless_session()
        except Exception as e:
            logger.warning(
                f"Headless session setup failed ({type(e).__name__}: {e}) — "
                "continuing without it; Foundry will reconnect in the background"
            )
            headless_client_id = None
        if headless_client_id:
            settings.relay_headless_client_id = headless_client_id
        logger.info("Relay ready")

    # 1. Initialize database
    db = Database(settings.sqlite_db)
    app.state.db = db
    await db.init()
    logger.info("Database initialized")
    # Apply retention policy to clean up old data on startup
    await db.apply_retention_policy()

    # 2. Initialize campaign loader and load default campaign
    campaign_loader = CampaignLoader()
    app.state.campaign_loader = campaign_loader
    await campaign_loader.load(settings.default_campaign)
    logger.info("Campaign context loaded")

    # 2b. Initialize NPC personality system (Tier 3)
    from npc.registry import NPCRegistry
    from npc.personality import PersonalityEngine
    npc_registry = NPCRegistry()
    personality_engine = PersonalityEngine()
    app.state.npc_registry = npc_registry
    app.state.personality_engine = personality_engine
    logger.info("NPC personality system initialized")

    # 2c. Initialize TTS service (optional — disabled unless tts_enabled=true)
    tts_service = None
    if settings.tts_enabled and settings.tts_engine == "browser":
        # Browser TTS: no server. Deploy the aigm-tts Foundry module so every
        # client speaks via the Web Speech API.
        from actions.executors import configure_tts
        from foundry.module_deploy import deploy_aigm_tts
        deployed = deploy_aigm_tts(settings.foundry_modules_path)
        configure_tts(None, npc_registry, volume=settings.tts_volume, engine="browser")
        logger.info(
            f"TTS enabled — engine=browser (Web Speech API), "
            f"narrator_voice={settings.tts_narrator_voice}, "
            f"module_deployed={deployed} "
            f"{'(enable the aigm-tts module in your world)' if deployed else '(set FOUNDRY_MODULES_PATH)'}"
        )
    elif settings.tts_enabled:
        from actions.executors import configure_tts
        engine_host = settings.tts_engine_host or f"http://localhost:{settings.admin_port}"
        tts_audio_dir = Path(__file__).parent / settings.tts_audio_dir
        tts_service = TTSService(
            base_url=settings.tts_url,
            api_key=settings.tts_api_key,
            model=settings.tts_model,
            narrator_voice=settings.tts_narrator_voice,
            audio_dir=tts_audio_dir,
            engine_base_url=engine_host,
            fmt=settings.tts_format,
            max_cached_files=settings.tts_max_cached,
        )
        configure_tts(tts_service, npc_registry, volume=settings.tts_volume, engine="server")
        logger.info(f"TTS enabled — engine=server model={settings.tts_model} narrator_voice={settings.tts_narrator_voice}")
    else:
        logger.info("TTS disabled (set TTS_ENABLED=true to enable)")
    app.state.tts_service = tts_service

    # 3. Initialize LLM manager (pass loader for context access)
    llm_manager = LLMManager(campaign_loader=campaign_loader)
    app.state.llm_manager = llm_manager
    logger.info("LLM Manager initialized")

    # 4. Initialize Foundry client and connect
    foundry_client = FoundryClient()
    app.state.foundry_client = foundry_client
    # Self-heal hook: relaunch the headless Foundry session if the relay loses
    # its Foundry client (headless tab died / module dropped).
    if settings.relay_managed and settings.relay_allow_headless:
        foundry_client._relaunch_headless = relay_manager.restart_headless_session
    await foundry_client.connect(max_retries=2)
    if foundry_client.is_connected:
        logger.info("FoundryVTT connected")
    else:
        logger.warning("Failed to connect to FoundryVTT — will retry in background")

    # 4b. Auto-detect which campaign matches the loaded Foundry world.
    # Only runs when Foundry is reachable; skips gracefully otherwise.
    if foundry_client.is_connected and not settings.default_campaign:
        try:
            from campaign.obsidian_sync import find_campaign_by_world
            _wjs = (
                "return {title: game.world?.title ?? '', id: game.world?.id ?? ''};"
            )
            _wres = await foundry_client.execute_js(_wjs)
            _wtitle = (_wres.get("result") or {}).get("title", "") if isinstance(_wres, dict) else ""
            _wid = (_wres.get("result") or {}).get("id", "") if isinstance(_wres, dict) else ""
            if _wtitle or _wid:
                _matched = find_campaign_by_world(_wtitle, _wid)
                if _matched:
                    logger.info(
                        f"[WorldMatch] Auto-loading campaign {_matched!r} "
                        f"(world: {_wtitle!r})"
                    )
                    await campaign_loader.load(_matched)
                    campaign_loader.register_vault_npcs(npc_registry)
                else:
                    logger.info(
                        f"[WorldMatch] World {_wtitle!r} / {_wid!r} has no matching campaign "
                        f"in vault — starting with empty context"
                    )

            # Scan active modules and feed into LLM system prompt
            try:
                _scan = await foundry_client.scan_world()
                _modules = [
                    m.get("title") or m.get("name") or m.get("id")
                    for m in (_scan.get("modules") or [])
                    if m.get("active") or m.get("enabled")
                ]
                _modules = [m for m in _modules if m]
                if _modules and llm_manager:
                    llm_manager.set_active_modules(_modules)
                    logger.info(f"[Modules] {len(_modules)} active modules injected into system prompt")
            except Exception as _me:
                logger.warning(f"[Modules] Module scan failed (non-fatal): {_me}")
        except Exception as _e:
            logger.warning(f"[WorldMatch] World detection failed (non-fatal): {_e}")

    async def _reconnect_loop():
        """Periodically reconnect to the relay when disconnected."""
        while True:
            await asyncio.sleep(10)
            if not foundry_client.is_connected:
                logger.info("Relay disconnected — attempting reconnect…")
                await foundry_client.ensure_connected()

    reconnect_task = asyncio.create_task(_reconnect_loop())
    app.state._reconnect_task = reconnect_task

    # 5. Initialize action dispatcher (pass app_state for access to all managers)
    action_dispatcher = ActionDispatcher(foundry_client, app_state=app.state)
    app.state.action_dispatcher = action_dispatcher
    logger.info("Action dispatcher initialized")

    # 6. Initialize state tracker
    state_tracker = GameStateTracker(db)
    app.state.state_tracker = state_tracker
    await state_tracker.load()
    logger.info("State tracker initialized")

    # 7. Close any stale session left over from a previous process run.
    # A session marked active in the DB at startup was never cleanly ended,
    # so treat it as stale rather than resuming it — the user must explicitly
    # start a new session to ensure correct campaign selection and monitoring.
    stale_session = await db.get_active_session()
    if stale_session:
        await db.close_session(stale_session)
        logger.info(f"Closed stale session from previous run: {stale_session}")

    # 8. Set up context window manager
    from context.window_manager import ContextWindowManager
    context_manager = ContextWindowManager(
        max_tokens=settings.max_context_tokens,
        keep_system=True,
        keep_recent=20
    )
    app.state.context_manager = context_manager
    logger.info("Context window manager initialized")

    # 9. Initialize scene awareness
    scene_awareness = SceneAwareness(
        foundry=foundry_client,
        state_tracker=state_tracker,
        campaign_loader=campaign_loader,
        llm_manager=llm_manager,
    )
    app.state.scene_awareness = scene_awareness
    logger.info("Scene awareness initialized")

    # 9b. Initialize immersion managers (Tier 6)
    from immersion.ambient import AmbientManager
    from immersion.effects import EffectsManager
    from immersion.vision import VisionManager
    from immersion.macros import MacroManager
    from immersion.items import ItemManager
    from immersion.particles import ParticleManager
    ambient_manager = AmbientManager()
    effects_manager = EffectsManager()
    vision_manager = VisionManager()
    macro_manager = MacroManager()
    item_manager = ItemManager()
    particle_manager = ParticleManager()
    app.state.ambient_manager = ambient_manager
    app.state.effects_manager = effects_manager
    app.state.vision_manager = vision_manager
    app.state.macro_manager = macro_manager
    app.state.item_manager = item_manager
    app.state.particle_manager = particle_manager
    logger.info("Immersion managers initialized")

    # 10. Initialize combat loop
    combat_loop = CombatLoop(
        foundry=foundry_client,
        llm=llm_manager,
        dispatcher=action_dispatcher,
        state_tracker=state_tracker,
        db=db,
        campaign_loader=campaign_loader,
        npc_registry=npc_registry,
    )
    app.state.combat_loop = combat_loop

    # Set up combat loop callbacks
    async def on_combat_turn_start(data):
        await broadcast_state_update(data)

    async def on_combat_turn_complete(data):
        await broadcast_state_update(data)

    combat_loop.set_turn_start_callback(on_combat_turn_start)
    combat_loop.set_turn_complete_callback(on_combat_turn_complete)
    logger.info("Combat loop initialized")

    # 11. Initialize chat listener (pass campaign_loader for NPC context)
    # 11.5. Initialize context reinforcement manager
    from context.reinforcement_manager import ContextReinforcementManager
    global reinforcement_mgr
    reinforcement_mgr = ContextReinforcementManager(
        llm_manager=llm_manager,
        state_tracker=state_tracker,
        foundry_client=foundry_client,
        scene_awareness=scene_awareness,
        campaign_loader=campaign_loader,
        db=db,
        reinforce_interval=settings.context_reinforce_interval or 5,
        summarize_interval=settings.context_summarize_interval or 10,
        summarize_timer=settings.context_summarize_timer or 300,
    )
    app.state.reinforcement_mgr = reinforcement_mgr
    await reinforcement_mgr.start()
    logger.info("Context reinforcement manager initialized")

    # 12. Initialize chat listener
    chat_listener = ChatListener(
        foundry=foundry_client,
        llm=llm_manager,
        dispatcher=action_dispatcher,
        state_tracker=state_tracker,
        db=db,
        campaign_loader=campaign_loader,
        combat_loop=combat_loop,
        scene_awareness=scene_awareness,
        reinforcement_mgr=reinforcement_mgr,
        npc_registry=app.state.npc_registry,
        personality_engine=app.state.personality_engine,
        ambient_manager=app.state.ambient_manager,
        effects_manager=app.state.effects_manager,
        vision_manager=app.state.vision_manager,
    )
    app.state.chat_listener = chat_listener

    # Wire reinforcement events for combat
    async def on_combat_start_event(tokens):
        await reinforcement_mgr.on_combat_start(tokens)

    async def on_combat_end_event():
        await reinforcement_mgr.on_combat_end()

    combat_loop.set_combat_start_callback(on_combat_start_event)
    combat_loop.set_combat_end_callback(on_combat_end_event)

    # Set up callback for admin panel
    async def notify_admin(results):
        await broadcast_state_update({
            "type": "actions_executed",
            "actions": results
        })

    chat_listener.set_results_callback(notify_admin)

    from actions.executors import set_chat_listener
    set_chat_listener(chat_listener)

    await chat_listener.start()
    logger.info("AI Gamemaster Engine is RUNNING")

    yield

    # Shutdown
    logger.info("Shutting down AI Gamemaster Engine...")
    await chat_listener.stop()
    if combat_loop:
        await combat_loop.stop()
    if reinforcement_mgr:
        await reinforcement_mgr.stop()
    if foundry_client:
        await foundry_client.disconnect()
    if db:
        await db.close()
    # Close LLM manager to release HTTP connections
    if llm_manager:
        try:
            await llm_manager.close()
        except Exception:
            pass
    # Cancel background reconnect task if running
    task = getattr(app.state, '_reconnect_task', None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if tts_service:
        await tts_service.close()
    if relay_manager and settings.relay_managed:
        await relay_manager.stop()
    logger.info("Shutdown complete")


# --- WebSocket broadcast for admin panel ---

websocket_clients: List[WebSocket] = []
_admin_ws_rate: Dict[WebSocket, float] = {}


async def broadcast_state_update(data: dict):
    """Broadcast state updates to all connected admin WebSocket clients."""
    msg = json.dumps(data)
    for ws in list(websocket_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            # Guard the remove: a concurrent broadcast may have already pruned
            # this dead socket, and an unguarded list.remove() would raise
            # ValueError that escapes into the combat-turn callbacks.
            if ws in websocket_clients:
                websocket_clients.remove(ws)


# --- FastAPI App ---

app = FastAPI(
    title="Sage - AI D&D Gamemaster",
    description="AI D&D 5e Gamemaster integrated with FoundryVTT",
    version="0.1.0",
    lifespan=lifespan
)

# CORS — Foundry runs on a different origin (e.g. localhost:30000) than this
# engine (localhost:18080). Foundry's AudioHelper decodes TTS audio via the Web
# Audio API, which silently fails on cross-origin responses without these
# headers. Allow all origins (local-only service).
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers extracted from main.py (Phase 1 of the modular architecture split,
# docs/ARCHITECTURE_REFACTOR.md). More domains move here incrementally.
from api.routes import combat as combat_routes  # noqa: E402
from api.routes import immersion as immersion_routes  # noqa: E402
from api.routes import npc as npc_routes  # noqa: E402
from api.routes import procedural as procedural_routes  # noqa: E402
from api.routes import rules as rules_routes  # noqa: E402
from api.routes import scene as scene_routes  # noqa: E402

app.include_router(combat_routes.router)
app.include_router(immersion_routes.router)
app.include_router(npc_routes.router)
app.include_router(procedural_routes.router)
app.include_router(rules_routes.router)
app.include_router(scene_routes.router)


@app.exception_handler(ApiError)
async def api_error_handler(request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status,
        content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump(),
    )


@app.exception_handler(CampaignNotFound)
async def campaign_not_found_handler(request, exc: CampaignNotFound):
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(status="error", error=str(exc), code="CAMPAIGN_NOT_FOUND").model_dump(),
    )

# Mount admin panel — prefer the Vite build output (dist/) when available,
# otherwise fall back to the standalone index.html at the panel root.
_panel_root = Path(__file__).parent / "admin-panel"
_panel_dist = _panel_root / "dist"
_admin_serve = _panel_dist if _panel_dist.exists() else _panel_root
if _admin_serve.exists():
    app.mount("/admin", StaticFiles(directory=str(_admin_serve), html=True), name="admin")

# Mount TTS audio directory so Foundry can fetch generated MP3s
_tts_audio_dir = Path(__file__).parent / settings.tts_audio_dir
_tts_audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(_tts_audio_dir)), name="audio")


@app.get("/")
async def admin_redirect(state: AppState = Depends(get_app_state)):

    """Redirect to admin panel."""
    return RedirectResponse(url="/admin/index.html")


# --- Admin API Endpoints ---

@app.get("/api/status")
async def get_status(state: AppState = Depends(get_app_state)):

    """Get current engine status."""
    return {
        "connected": state.foundry_client.is_connected if state.foundry_client else False,
        "ai_running": state.chat_listener._running if state.chat_listener else False,
        "model": state.llm_manager.model if state.llm_manager else settings.model,
        "campaign": state.state_tracker.state.campaign if state.state_tracker else "",
        "session": state.state_tracker.state.session_number if state.state_tracker else 0,
        "scene": state.state_tracker.state.current_scene if state.state_tracker else "",
        "mode": state.state_tracker.state.mode.value if state.state_tracker else "exploration",
        "conversation_length": len(state.llm_manager.conversation_history) if state.llm_manager else 0,
        "reinforcement_turns": state.reinforcement_mgr._turn_count if state.reinforcement_mgr else 0,
        "reinforcement_active": state.reinforcement_mgr._running if state.reinforcement_mgr else False,
        "relay": {
            **(state.relay_manager.status() if state.relay_manager else {"managed": False, "running": False}),
            "headless_client_id": settings.relay_headless_client_id or None,
        },
    }


@app.get("/api/relay/status")
async def relay_status(state: AppState = Depends(get_app_state)):
    """Get relay service status."""
    if not state.relay_manager:
        return {"managed": False, "running": False, "error": "No relay manager"}
    return state.relay_manager.status()


@app.get("/api/relay/logs")
async def relay_logs(lines: int = 200, state: AppState = Depends(get_app_state)):
    """Return the last N lines from the relay log file."""
    if not state.relay_manager:
        return JSONResponse({"error": "No relay manager"}, status_code=503)
    log_path = state.relay_manager.data_dir / "relay.log"
    if not log_path.exists():
        return {"lines": [], "path": str(log_path), "error": "Log file not found"}
    try:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
        return {"lines": all_lines[-lines:], "total": len(all_lines), "path": str(log_path)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/relay/start")
async def relay_start(state: AppState = Depends(get_app_state)):
    """Start the relay service."""
    if not state.relay_manager:
        return JSONResponse({"error": "No relay manager"}, status_code=503)
    if not settings.relay_managed:
        return JSONResponse({"error": "Relay is not managed by this engine"}, status_code=400)
    try:
        await state.relay_manager.start()
        return {"status": "started", **state.relay_manager.status()}
    except Exception as e:
        logger.exception("Failed to start relay")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/relay/stop")
async def relay_stop(state: AppState = Depends(get_app_state)):
    """Stop the relay service."""
    if not state.relay_manager:
        return JSONResponse({"error": "No relay manager"}, status_code=503)
    if not settings.relay_managed:
        return JSONResponse({"error": "Relay is not managed by this engine"}, status_code=400)
    try:
        await state.relay_manager.stop()
        return {"status": "stopped"}
    except Exception as e:
        logger.exception("Failed to stop relay")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/relay/restart")
async def relay_restart(state: AppState = Depends(get_app_state)):
    """Restart the relay service."""
    if not state.relay_manager:
        return JSONResponse({"error": "No relay manager"}, status_code=503)
    if not settings.relay_managed:
        return JSONResponse({"error": "Relay is not managed by this engine"}, status_code=400)
    try:
        await state.relay_manager.restart()
        return {"status": "restarted", **state.relay_manager.status()}
    except Exception as e:
        logger.exception("Failed to restart relay")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/health")
async def health_check(state: AppState = Depends(get_app_state)):

    """Health check endpoint for load balancers and orchestrators."""
    return {
        "status": "healthy",
        "components": {
            "state.db": state.db is not None,
            "foundry": state.foundry_client is not None and state.foundry_client.is_connected,
            "llm": state.llm_manager is not None,
            "state.chat_listener": state.chat_listener is not None and state.chat_listener._running,
            "state.combat_loop": state.combat_loop is not None,
        },
    }


@app.get("/api/context/reinforcement")
async def get_reinforcement_status(state: AppState = Depends(get_app_state)):

    """Get context reinforcement status."""
    if not state.reinforcement_mgr:
        return {"active": False, "turns": 0, "messages": 0, "last_reinforcement": None}
    return {
        "active": state.reinforcement_mgr._running,
        "turns": state.reinforcement_mgr._turn_count,
        "message_count": state.reinforcement_mgr._message_count,
        "last_reinforcement": state.reinforcement_mgr._last_reinforcement_time,
        "status": state.reinforcement_mgr._status,
        "world_summary": state.reinforcement_mgr._world_summary,
        "anchors": state.reinforcement_mgr._get_anchor_facts(),
    }


@app.post("/api/context/reinforce")
async def trigger_reinforcement(state: AppState = Depends(get_app_state)):

    """Manually trigger a context reinforcement pass."""
    if not state.reinforcement_mgr:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Reinforcement manager not initialized",
                code="REINFORCEMENT_NOT_READY"
            ).model_dump()
        )
    try:
        summary = await state.reinforcement_mgr.reinforce_context()
        return {
            "status": "ok",
            "message": "Context reinforced",
            "summary_length": len(summary) if summary else 0,
        }
    except Exception as e:
        logger.error(f"Reinforcement error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Reinforcement failed: {str(e)}",
                code="REINFORCEMENT_FAILED"
            ).model_dump()
        )


@app.post("/api/context/summarize")
async def trigger_summarization(state: AppState = Depends(get_app_state)):

    """Manually trigger a context summarization pass."""
    if not state.reinforcement_mgr:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Reinforcement manager not initialized",
                code="REINFORCEMENT_NOT_READY"
            ).model_dump()
        )
    try:
        summary = await state.reinforcement_mgr.summarize_context()
        return {
            "status": "ok",
            "summary_length": len(summary) if summary else 0,
        }
    except Exception as e:
        logger.error(f"Summarization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Summarization failed: {str(e)}",
                code="SUMMARIZATION_FAILED"
            ).model_dump()
        )


@app.post("/api/context/world_summary")
async def update_world_summary(state: AppState = Depends(get_app_state)):

    """Update the world summary with current game state."""
    if not state.reinforcement_mgr:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Reinforcement manager not initialized",
                code="REINFORCEMENT_NOT_READY"
            ).model_dump()
        )
    try:
        # Gather current state from all sources
        state_dict = state.state_tracker.state.model_dump() if state.state_tracker else {}
        scene_data = state.scene_awareness.get_context_summary() if state.scene_awareness else ""
        await state.reinforcement_mgr.update_world_summary(state_dict, scene_data)
        return {"status": "ok", "message": "World summary updated"}
    except Exception as e:
        logger.error(f"World summary update error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"World summary update failed: {str(e)}",
                code="WORLD_SUMMARY_UPDATE_FAILED"
            ).model_dump()
        )


@app.get("/api/state")
async def get_state(state: AppState = Depends(get_app_state)):

    """Get full game state."""
    if state.state_tracker:
        return state.state_tracker.state.model_dump()
    return {}


@app.get("/api/settings", response_model=GMSettings)
async def get_settings(state: AppState = Depends(get_app_state)):

    """Return current AI GM settings from config (secrets never returned in GET)."""
    # Never return API keys in GET responses — prevents credential disclosure
    return GMSettings(
        model=settings.model,
        llm_base_url=settings.llm_base_url,
        llm_api_key="",  # Never return actual key
        temperature=settings.temperature,
        ai_name=settings.ai_name,
        ai_tone=settings.ai_tone,
        relay_url=settings.relay_url,
        relay_api_key="",  # Never return actual key
        comfyui_url=settings.comfyui_url,
    )


@app.post("/api/settings", response_model=GMSettings)
async def update_settings(settings_data: GMSettings, state: AppState = Depends(get_app_state)):

    """Update AI GM settings (runtime-only, not persisted to disk).

    Settings changes apply to the running instance only and are lost on restart.
    To persist settings, modify the .env file directly.
    Note: LLM base_url/api_key changes require a restart to take effect.
    """
    if state.llm_manager:
        state.llm_manager._temperature = settings_data.temperature
        state.llm_manager._ai_tone = settings_data.ai_tone
    if state.foundry_client:
        state.foundry_client.set_ai_name(settings_data.ai_name)
    
    # Apply non-secret runtime changes immediately
    if settings_data.comfyui_url:
        settings.comfyui_url = settings_data.comfyui_url
    if settings_data.model:
        settings.model = settings_data.model
        if state.llm_manager:
            state.llm_manager.model = settings_data.model
    if settings_data.ai_tone:
        settings.ai_tone = settings_data.ai_tone
        if state.llm_manager:
            state.llm_manager._ai_tone = settings_data.ai_tone
    if settings_data.ai_name:
        settings.ai_name = settings_data.ai_name
    if settings_data.temperature is not None:
        settings.temperature = settings_data.temperature
    if settings_data.relay_url:
        settings.relay_url = settings_data.relay_url
    
    return settings_data


@app.post("/api/state/update", response_model=dict)
async def update_game_state(state_data: StateUpdate, state: AppState = Depends(get_app_state)):

    """Update game state manually."""
    if state_data.mode:
        await state.state_tracker.set_mode(GameMode(state_data.mode))
    if state_data.scene:
        await state.state_tracker.set_scene(state_data.scene)
    if state_data.session:
        state.state_tracker.state.session_number = state_data.session
    if state_data.campaign:
        await state.state_tracker.set_campaign(state_data.campaign)
    await state.state_tracker.save()
    return {"status": "ok", "state": state.state_tracker.state.model_dump()}


@app.post("/api/campaign/load", response_model=dict)
async def load_campaign(campaign: CampaignCreate, state: AppState = Depends(get_app_state)):

    """Load or create a new campaign with its own vault subfolder."""
    if not state.campaign_loader:
        return {
            "status": "error",
            "error": "Campaign loader not initialized",
            "name": campaign.name,
            "folder": "",
            "loaded_files": [],
        }

    result = await state.campaign_loader.load_custom_campaign(
        campaign.name, campaign.vault_files
    )
    return {
        "status": "ok",
        "name": campaign.name,
        "folder": result.get("folder", ""),
        "loaded_files": result.get("linked_files", []),
    }


@app.get("/api/session/active")
async def get_active_session(state: AppState = Depends(get_app_state)):
    """Get active session info including campaign name."""
    info = await state.db.get_active_session_info()
    if info:
        return {
            "session_id": info["session_id"],
            "campaign_name": info["campaign"] or "",
            "active": True,
            "status": "started",
        }
    return {"session_id": None, "campaign_name": "", "active": False, "status": "none"}


@app.post("/api/session/new", response_model=SessionInfo)
async def create_session(campaign: str = None, state: AppState = Depends(get_app_state)):

    """Create a new game session."""
    if campaign is None:
        campaign = settings.default_campaign
    session_id = str(uuid.uuid4())[:8]
    await state.db.create_session(session_id, campaign)
    await state.state_tracker.set_campaign(campaign)
    return SessionInfo(
        session_id=session_id,
        campaign=campaign,
        started_at="now"
    )


@app.get("/api/session/events", response_model=List[EventEntry])
async def get_session_events(limit: int = 50, state: AppState = Depends(get_app_state)):

    """Get session event history."""
    session_id = await state.db.get_active_session()
    if session_id:
        events = await state.db.get_events(session_id, limit)
        return events
    return []


class ChatTestRequest(BaseModel):
    message: str
    speaker: str = "Selmor"


class GMChatRequest(BaseModel):
    message: str


@app.post("/api/chat/test")
async def test_chat(request: ChatTestRequest, state: AppState = Depends(get_app_state)):

    """Test the AI with a manual chat message."""
    if not state.chat_listener or not state.llm_manager:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Engine not initialized",
                code="ENGINE_NOT_READY"
            ).model_dump()
        )

    game_state = state.state_tracker.get_snapshot() if state.state_tracker else ""
    npc_context = await state.campaign_loader.get_npc_context() if state.campaign_loader else ""

    try:
        result = await state.llm_manager.generate(
            user_message=f"[{request.speaker}]: {request.message}",
            game_state_summary=game_state,
            extra_context=npc_context
        )
        actions = result.get("actions", [])
        return {"actions": actions}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=str(e),
                code="CHAT_GENERATION_FAILED"
            ).model_dump()
        )


@app.post("/api/chat/gm")
async def gm_direct_chat(request: GMChatRequest, state: AppState = Depends(get_app_state)):

    """Direct chat with the AI GM outside of a game session."""
    if not state.llm_manager:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Engine not initialized",
                code="ENGINE_NOT_READY"
            ).model_dump()
        )

    try:
        # Get game state context if available
        game_state = state.state_tracker.get_snapshot() if state.state_tracker else ""
        npc_context = await state.campaign_loader.get_npc_context() if state.campaign_loader else ""

        # Fetch the current scene so the GM can give scene-specific advice
        scene_info = ""
        if state.foundry_client and state.foundry_client.is_connected:
            try:
                scene_details = await state.foundry_client.get_scene_details()
                if scene_details:
                    data = scene_details.get("data", scene_details)
                    grid_size = data.get("grid", {}).get("size", 100) if isinstance(data.get("grid"), dict) else data.get("gridSize", 100)
                    w = data.get("width", "?")
                    h = data.get("height", "?")
                    wall_count = len(data.get("walls", []))
                    light_count = len(data.get("lights", []))
                    token_count = len(data.get("tokens", []))
                    scene_info = (
                        f"Active scene: {data.get('name','?')} "
                        f"({w}×{h}px, grid={grid_size}px/sq) — "
                        f"{wall_count} walls, {light_count} lights, {token_count} tokens"
                    )
            except Exception:
                pass

        gm_system_prompt = (
            "You are an AI Game Master assistant for a FoundryVTT D&D 5e campaign. "
            "Answer the human GM's questions directly and conversationally.\n\n"
            "## What You Can Do in Foundry\n"
            "The AI GM engine supports these real Foundry operations (sent as JSON actions during play):\n"
            "- **setup_scene**: Place walls, ambient lights, sounds, tokens, and configure darkness/fog all at once\n"
            "- **place_walls**: Draw wall segments that block vision and movement\n"
            "- **place_lights**: Place ambient light sources (torches, windows, magical glows)\n"
            "- **place_sounds**: Place ambient sound emitters (fire crackling, dripping water)\n"
            "- **place_token**: Place an actor's token at specific coordinates\n"
            "- **configure_scene**: Set darkness level, fog of war, global illumination, token vision\n"
            "- **generate_map**: Generate an AI dungeon/battle map via ComfyUI SDXL and create a Foundry scene\n"
            "- **switch_scene**: Move players to a different scene\n"
            "- **execute_js**: Run arbitrary Foundry JavaScript for anything else\n"
            "- **move_token**, **update_hp**, **apply_condition**, **start_encounter**, **roll**, etc.\n\n"
            "## What Requires Manual Setup in Foundry\n"
            "- Uploading custom art assets (players manually upload, or AI generates via generate_map)\n"
            "- Module configuration (e.g. Dynamic Active Effects, Midi-QOL settings)\n"
            "- Compendium imports and world building outside of scenes\n\n"
            "## Coordinate System\n"
            "- Pixels from top-left. Default grid: 100px = 1 square = 5ft.\n"
            "- Walls: `{\"c\":[x0,y0,x1,y1], \"move\":20, \"sense\":20, \"door\":0}`\n"
            "- move/sense 20=normal, 0=none, 10=limited. door: 0=wall, 1=door, 2=secret\n"
            "- Lights: `{\"x\":500,\"y\":300,\"config\":{\"bright\":30,\"dim\":60,\"color\":\"#ff6600\"}}`\n\n"
            "Give specific, actionable answers. When the GM asks how to do something, "
            "show the exact action JSON they need or explain which action to use."
        )

        # Generate a plain-text conversational response (no action JSON / tool execution)
        context_parts = []
        if scene_info:
            context_parts.append(f"CURRENT SCENE: {scene_info}")
        if game_state:
            context_parts.append(f"GAME STATE:\n{game_state}")
        if npc_context:
            context_parts.append(f"NPC CONTEXT:\n{npc_context}")
        context = "\n\n".join(context_parts)

        response_text = await state.llm_manager.generate_text(
            user_message=request.message,
            system_prompt=gm_system_prompt,
            context=context,
        )

        # Record the chat exchange
        if state.db:
            try:
                session_id = await state.db.get_active_session()
                if session_id:
                    await state.db.save_conversation(session_id, "user", f"[GM Chat] {request.message}")
                    await state.db.save_conversation(session_id, "assistant", response_text)
            except Exception as e:
                logger.warning(f"Failed to record GM chat: {e}")

        return {"response": response_text}
    except Exception as e:
        logger.error(f"GM chat error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=str(e),
                code="GM_CHAT_FAILED"
            ).model_dump()
        )



@app.post("/api/foundry/js", response_model=dict)
async def run_foundry_js_endpoint(code: str = Body(..., embed=True), state: AppState = Depends(get_app_state)):
    """Run arbitrary JavaScript in the Foundry headless session."""
    # Same gate as the LLM execute_js action: this endpoint is unauthenticated,
    # so without the check it silently bypassed the allow_execute_js setting.
    if not getattr(settings, "allow_execute_js", False):
        return JSONResponse(
            status_code=403,
            content={"error": "execute_js is disabled. Set ALLOW_EXECUTE_JS=true to enable arbitrary Foundry JavaScript."},
        )
    if not state.foundry_client:
        return JSONResponse(status_code=503, content={"error": "Not connected to Foundry"})
    if not code or not code.strip():
        return JSONResponse(status_code=400, content={"error": "Empty JavaScript code"})
    if len(code) > 10000:
        return JSONResponse(status_code=400, content={"error": "JavaScript code too long (max 10000 chars)"})
    try:
        result = await state.foundry_client.execute_js(code)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"[JS] execute_js failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "JavaScript execution failed"})




# --- Campaign Builder API Endpoints ---

# --- Campaign Wizard Models ---

class CampaignScanRequest(BaseModel):
    """Request body for scanning a FoundryVTT world."""
    world_name: Optional[str] = None


class CampaignScanResponse(BaseModel):
    status: str
    scan_id: str
    world: Dict[str, Any] = {}
    scenes: List[Dict[str, Any]] = []
    actors: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    journal: List[Dict[str, Any]] = []
    quests: List[Dict[str, Any]] = []
    modules: List[Dict[str, Any]] = []
    capabilities: Dict[str, Any] = {}
    error: Optional[str] = None


class CampaignBuildRequest(BaseModel):
    """Request body for building a new campaign."""
    name: str
    description: str = ""
    theme: str = ""
    seed_ideas: str = ""
    scale: str = ""
    level_range: str = "1-5"
    vault_files: List[str] = []


class CampaignExtendRequest(BaseModel):
    """Request body for extending an existing campaign with a new arc."""
    campaign_name: str
    current_level: int = 1


class CampaignTeardownRequest(BaseModel):
    """Request body for removing campaign content from FoundryVTT."""
    campaign_name: str


class CampaignTeardownResponse(BaseModel):
    status: str
    campaign_name: str
    deleted: Dict[str, Any] = {}
    errors: List[str] = []


class CampaignExtendResponse(BaseModel):
    status: str
    campaign_name: str
    arc_number: int = 0
    arc_title: str = ""
    steps_completed: List[Dict[str, Any]] = []
    arc_data: Optional[Dict[str, Any]] = None
    assets: Dict[str, Any] = {}
    error: Optional[str] = None


class CampaignBuildResponse(BaseModel):
    status: str
    campaign_id: str
    campaign_name: str
    steps_completed: List[Dict[str, Any]] = []
    scan_data: Optional[Dict[str, Any]] = None
    generated_data: Optional[Dict[str, Any]] = None
    maps_generated: Dict[str, Any] = {}
    progress: int = 0
    total_steps: int = 0
    error: Optional[str] = None
    ready_to_start: bool = False


class CampaignStartRequest(BaseModel):
    """Request body for starting/continuing a campaign session."""
    campaign_name: str
    continue_from_last: bool = False


class CampaignStartResponse(BaseModel):
    status: str
    session_id: str
    campaign_name: str
    current_scene: str = ""
    active_actors: int = 0
    message: str = ""
    error: Optional[str] = None


class SessionEndRequest(BaseModel):
    """Request body for ending a session prematurely."""
    reason: str = "GM ended session"


class SessionEndResponse(BaseModel):
    status: str
    session_id: str
    campaign_name: str
    summary: str = ""
    error: Optional[str] = None


class CampaignListResponse(BaseModel):
    campaigns: List[Dict[str, Any]] = []
    error: Optional[str] = None


class CampaignDeployRequest(BaseModel):
    """Request to deploy an existing campaign to FoundryVTT."""
    campaign_name: str


class CampaignDeployResponse(BaseModel):
    """Response from campaign deployment."""
    status: str
    campaign_name: str
    scenes_deployed: int = 0
    npcs_deployed: int = 0
    journal_entries_deployed: int = 0
    quest_logs_deployed: int = 0
    loot_tables_deployed: int = 0
    error: Optional[str] = None


class CampaignRegenerateAssetsRequest(BaseModel):
    """Request to regenerate maps/portraits for an existing campaign."""
    campaign_name: str
    attach_to_foundry: bool = True


class CampaignRegenerateAssetsResponse(BaseModel):
    """Response from asset regeneration."""
    status: str
    campaign_name: str
    maps_generated: int = 0
    portraits_generated: int = 0
    scenes_attached: int = 0
    portraits_attached: int = 0
    errors: List[str] = []
    error: Optional[str] = None


# --- Campaign Wizard Endpoints ---

@app.post("/api/campaign/scan", response_model=CampaignScanResponse)
async def scan_world_endpoint(request: CampaignScanRequest, state: AppState = Depends(get_app_state)):

    """Scan the connected FoundryVTT world and catalog all resources.

    This endpoint performs a comprehensive scan of:
    - World structure and metadata
    - All scenes (maps) with token counts and lighting
    - All actors (NPCs, monsters, PCs)
    - All items/equipment
    - Journal entries
    - Active quests/encounters
    - Available modules/add-ons and their capabilities
    """
    if not state.foundry_client or not state.foundry_client.is_connected:
        return CampaignScanResponse(
            status="error",
            scan_id=f"scan-{uuid.uuid4().hex[:8]}",
            error="Not connected to FoundryVTT",
        )

    try:
        logger.info(f"Scanning FoundryVTT world: {request.world_name or 'unknown'}")

        # Step 1: Run full world scan
        scan_data = await state.foundry_client.scan_world()

        # Step 2: Analyze capabilities from scan
        capabilities = await state.foundry_client.discover_addon_capabilities(scan_data)

        response = CampaignScanResponse(
            status="ok",
            scan_id=f"scan-{uuid.uuid4().hex[:8]}",
            world=scan_data.get("world", {}),
            scenes=scan_data.get("scenes", []),
            actors=scan_data.get("actors", []),
            items=scan_data.get("items", []),
            journal=scan_data.get("journal", []),
            quests=scan_data.get("quests", []),
            modules=scan_data.get("modules", []),
            capabilities=capabilities,
        )

        logger.info(
            f"World scan complete: {len(response.scenes)} scenes, "
            f"{len(response.actors)} actors, {len(response.items)} items, "
            f"{len(response.modules)} modules"
        )
        return response

    except Exception as e:
        logger.exception("World scan failed")
        return CampaignScanResponse(
            status="error",
            scan_id=f"scan-{uuid.uuid4().hex[:8]}",
            error=str(e),
        )


@app.post("/api/campaign/build", response_model=CampaignBuildResponse)
async def build_campaign_endpoint(request: CampaignBuildRequest, state: AppState = Depends(get_app_state)):

    """Generate a new campaign from structured campaign info.

    Pipeline:
    1. Construct prompt from name, description, theme, seed_ideas, scale
    2. LLM generates structured campaign data (NPCs, locations, quests, arcs)
    3. Campaign saved to Obsidian vault
    4. ComfyUI generates map images for locations
    5. Returns full campaign structure and manifest
    6. Scan FoundryVTT world for existing resources
    7. Generate maps via oMLX Z-Image-Turbo (fallback ComfyUI)
    """
    from campaign.orchestrator import CampaignOrchestrator
    import httpx

    llm_client = httpx.AsyncClient(timeout=300)
    try:

        # Resolve paths
        vault_path = settings.campaign_vault_path

        # Build the full prompt from all user inputs
        full_prompt = f"Create a D&D 5e campaign named '{request.name}'."
        if request.description:
            full_prompt += f"\n\nTheme: {request.description}"
        if request.theme:
            full_prompt += f"\n\nTheme setting: {request.theme}"
        if request.seed_ideas:
            full_prompt += f"\n\nSeed ideas from user: {request.seed_ideas}"
        if request.scale:
            full_prompt += f"\n\nCampaign scale: {request.scale}"
        if request.level_range and request.level_range != "1-5":
            full_prompt += f"\n\nLevel range: {request.level_range}"

        orch = CampaignOrchestrator()

        result = await orch.build_campaign(
            prompt=full_prompt,
            campaign_name=request.name,
            llm_client=llm_client,
            foundry_client=state.foundry_client if state.foundry_client and state.foundry_client.is_connected else None,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=getattr(settings, "omlx_base_url", None) or getattr(settings, "omlx_url", None),
            omlx_model=getattr(settings, "omlx_model", "Z-Image-Turbo"),
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=None,
            level_range=request.level_range or "1-5",
        )

        # Map orchestrator result to our response model
        assets = result.get("assets") or {}
        return CampaignBuildResponse(
            status=result.get("status", "error"),
            campaign_id=result.get("campaign_id", f"campaign-{uuid.uuid4().hex[:8]}"),
            campaign_name=request.name,
            steps_completed=result.get("steps_completed", []),
            scan_data=result.get("scan_data"),
            generated_data=result.get("generated_data"),
            maps_generated=assets,
            progress=result.get("progress", 0),
            total_steps=result.get("total_steps", 5),
            error=result.get("error"),
            ready_to_start=result.get("ready_to_start", result.get("status") in ("success", "complete")),
        )
    except Exception as e:
        logger.exception("Campaign build failed")
        return CampaignBuildResponse(
            status="error",
            campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
            campaign_name=request.name,
            error=str(e),
            ready_to_start=False,
        )
    finally:
        await llm_client.aclose()


@app.post("/api/campaign/extend", response_model=CampaignExtendResponse)
async def extend_campaign_endpoint(request: CampaignExtendRequest, state: AppState = Depends(get_app_state)):
    """Extend an existing campaign with a new story arc.

    Loads the existing campaign, generates the next arc's scenes/NPCs/encounters
    via LLM (scaled to the party's current level), and deploys the new content
    into FoundryVTT alongside what already exists.
    """
    from campaign.orchestrator import CampaignOrchestrator
    import httpx

    llm_client = httpx.AsyncClient(timeout=300)
    try:
        orch = CampaignOrchestrator()
        result = await orch.extend_campaign_arc(
            campaign_name=request.campaign_name,
            current_level=request.current_level,
            llm_client=llm_client,
            foundry_client=state.foundry_client if state.foundry_client and state.foundry_client.is_connected else None,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=getattr(settings, "omlx_base_url", None) or getattr(settings, "omlx_url", None),
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=None,
        )
        return CampaignExtendResponse(
            status=result.get("status", "error"),
            campaign_name=request.campaign_name,
            arc_number=result.get("arc_number", 0),
            arc_title=result.get("arc_title", ""),
            steps_completed=result.get("steps", []),
            arc_data=result.get("arc_data"),
            assets=result.get("assets", {}),
            error=result.get("error"),
        )
    except Exception as e:
        logger.exception("Campaign arc extension failed")
        return CampaignExtendResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )
    finally:
        await llm_client.aclose()


@app.post("/api/campaign/teardown", response_model=CampaignTeardownResponse)
async def teardown_campaign_endpoint(request: CampaignTeardownRequest, state: AppState = Depends(get_app_state)):
    """Remove all AI-GM-created content for a campaign from the connected FoundryVTT world.

    Deletes every Scene, Actor, JournalEntry, RollTable, and Playlist that
    has a flags["ai-gm"] marker (set by the deployment pipeline), plus a
    UUID-based fallback pass using the stored deployment state.

    The Obsidian vault and local campaign_assets files are NOT touched.
    """
    from campaign.orchestrator import CampaignOrchestrator

    if not state.foundry_client or not state.foundry_client.is_connected:
        return CampaignTeardownResponse(
            status="error",
            campaign_name=request.campaign_name,
            errors=["Not connected to FoundryVTT — open the world in Foundry first"],
        )

    try:
        orch = CampaignOrchestrator()
        result = await orch.teardown_campaign(
            campaign_name=request.campaign_name,
            foundry_client=state.foundry_client,
        )
        return CampaignTeardownResponse(
            status=result.get("status", "ok"),
            campaign_name=request.campaign_name,
            deleted=result.get("deleted", {}),
            errors=result.get("errors", []),
        )
    except Exception as e:
        logger.exception("Campaign teardown failed")
        return CampaignTeardownResponse(
            status="error",
            campaign_name=request.campaign_name,
            errors=[str(e)],
        )


async def _deploy_campaign_to_world(campaign_name: str, state: AppState) -> Dict[str, Any]:
    """Deploy a vault campaign to the connected world with assets and walls restored.

    Full redeploy pipeline shared by the deploy and restart endpoints:
    1. Re-upload local maps/portraits so background_src/portrait_src resolve
       (idempotent: same Foundry path + filename each time).
    2. Scan the world so module-specific flags (Item Piles, Midi QOL, patrol,
       soundscapes, …) are applied — previously skipped on redeploy.
    3. Deploy all entities.
    4. Enrich scenes: walls, doors, lights, sounds, fog/vision config.
    5. Persist deployment state and updated asset references.

    Raises FileNotFoundError if the campaign isn't in the vault.
    """
    from campaign.orchestrator import CampaignOrchestrator

    store = CampaignStore(campaign_name)
    campaign_data = await store.load()

    orch = CampaignOrchestrator()
    safe_name = store.safe_name
    asset_output_dir = store.maps_dir

    # ── 1. Restore assets ──
    if asset_output_dir.exists():
        map_upload = await orch.upload_maps_to_foundry(
            campaign_data, state.foundry_client, asset_output_dir, safe_name
        )
        portrait_upload = await orch.upload_portraits_to_foundry(
            campaign_data, state.foundry_client, asset_output_dir, safe_name
        )
        logger.info(
            f"[Deploy] Asset restore: {map_upload.get('uploaded', 0)} maps, "
            f"{portrait_upload.get('uploaded', 0)} portraits re-uploaded"
        )

    # ── 2. Scan for active modules so addon flags apply on redeploy too ──
    scan_result = None
    try:
        scan_result = await orch.scan_foundry_world(state.foundry_client)
    except Exception as e:
        logger.warning(f"[Deploy] World scan failed (module flags skipped): {e}")

    # ── 3. Deploy entities ──
    deployment_result = await orch.deploy_to_foundry(
        campaign_data,
        state.foundry_client,
        {"maps": [], "portraits": []},
        scan_result=scan_result,
    )

    # ── 4. Walls, lights, sounds, scene config ──
    try:
        enrich_summary = await orch.enrich_scenes(
            campaign_data, state.foundry_client, deployment_result
        )
        deployment_result["scene_enrichment"] = enrich_summary
        logger.info(f"[Deploy] Enrichment: {enrich_summary}")
    except Exception as e:
        logger.warning(f"[Deploy] Scene enrichment failed: {e}")

    # ── 5. Persist deployment state + asset references ──
    try:
        await store.save_deployment(deployment_result)
        # background_src/portrait_src may have been (re)computed — keep them.
        await store.save(campaign_data)
    except Exception as e:
        logger.warning(f"[Deploy] Failed to persist deployment state: {e}")

    return deployment_result


@app.post("/api/campaign/deploy", response_model=CampaignDeployResponse)
async def deploy_campaign_endpoint(request: CampaignDeployRequest, state: AppState = Depends(get_app_state)):

    """Deploy an existing campaign from the vault to FoundryVTT.

    Loads the campaign JSON from the Obsidian vault, restores maps and
    portraits, deploys all entities, and places walls/lights/sounds.
    """
    try:
        logger.info(f"Deploying campaign: {request.campaign_name}")

        if not state.foundry_client or not state.foundry_client.is_connected:
            return CampaignDeployResponse(
                status="error",
                campaign_name=request.campaign_name,
                error="Not connected to FoundryVTT",
            )

        # WARNING: Clicking "Start" multiple times will create duplicates.
        # Use "Continue" after the first deployment to avoid re-deploying.
        # To start fresh, use /api/campaign/restart.
        deployment_result = await _deploy_campaign_to_world(request.campaign_name, state)

        return CampaignDeployResponse(
            status=deployment_result.get("status", "error"),
            campaign_name=request.campaign_name,
            scenes_deployed=len(deployment_result.get("scenes", [])),
            npcs_deployed=len(deployment_result.get("npcs", [])),
            journal_entries_deployed=len(deployment_result.get("journal_entries", [])),
            quest_logs_deployed=len(deployment_result.get("quest_logs", [])),
            loot_tables_deployed=len(deployment_result.get("loot_tables", [])),
        )

    except FileNotFoundError as e:
        return CampaignDeployResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )
    except Exception as e:
        logger.exception(f"Campaign deployment failed: {request.campaign_name}")
        return CampaignDeployResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )


@app.post("/api/campaign/regenerate-assets", response_model=CampaignRegenerateAssetsResponse)
async def regenerate_assets_endpoint(
    request: CampaignRegenerateAssetsRequest, state: AppState = Depends(get_app_state)
):
    """Regenerate maps/portraits for an existing campaign without re-running the LLM.

    Generates fresh images with the current (improved) SDXL workflow, persists them
    to the vault, and — when Foundry is connected — uploads each map and attaches it
    as the background of the matching scene (updating existing scenes by name).
    """
    from campaign.orchestrator import CampaignOrchestrator

    try:
        logger.info(f"Regenerating assets for campaign: {request.campaign_name}")
        orch = CampaignOrchestrator()
        result = await orch.regenerate_assets_for_campaign(
            campaign_name=request.campaign_name,
            foundry_client=state.foundry_client,
            attach_to_foundry=request.attach_to_foundry,
        )
        return CampaignRegenerateAssetsResponse(
            status=result.get("status", "error"),
            campaign_name=request.campaign_name,
            maps_generated=result.get("maps_generated", 0),
            portraits_generated=result.get("portraits_generated", 0),
            scenes_attached=result.get("scenes_attached", 0),
            portraits_attached=result.get("portraits_attached", 0),
            errors=result.get("errors", []),
        )
    except Exception as e:
        logger.exception(f"Asset regeneration failed: {request.campaign_name}")
        return CampaignRegenerateAssetsResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )


class CampaignRestartRequest(BaseModel):
    """Request to fully restart a campaign from the beginning."""
    campaign_name: str


@app.post("/api/campaign/restart", response_model=dict)
async def restart_campaign_endpoint(request: CampaignRestartRequest, state: AppState = Depends(get_app_state)):
    """Completely restart a campaign from the beginning.

    Wipes all session history (conversations, events, sessions) for the
    campaign, removes its content from the FoundryVTT world, resets the game
    state tracker, and redeploys everything fresh from the vault. Use
    /api/campaign/start afterwards to begin the new first session.
    """
    from campaign.orchestrator import CampaignOrchestrator

    require_foundry(state)
    try:
        logger.info(f"Restarting campaign from scratch: {request.campaign_name}")

        # 1. Stop the AI listener and close any active session
        if state.chat_listener and state.chat_listener._running:
            state.chat_listener._running = False
        active = await state.db.get_active_session()
        if active:
            await state.db.close_session(active)

        # 2. Wipe all session history for this campaign
        sessions_deleted = await state.db.delete_campaign_history(request.campaign_name)

        # 3. Reset persisted game state (scene, combat, contexts)
        if state.state_tracker:
            await state.state_tracker.reset(campaign=request.campaign_name)

        # 4. Remove existing world content
        orch = CampaignOrchestrator()
        teardown = await orch.teardown_campaign(request.campaign_name, state.foundry_client)

        # 5. Redeploy fresh (assets restored, walls placed)
        deployment = await _deploy_campaign_to_world(request.campaign_name, state)

        await broadcast_state_update({
            "type": "campaign_restarted",
            "campaign_name": request.campaign_name,
        })

        return {
            "status": "restarted",
            "campaign_name": request.campaign_name,
            "sessions_deleted": sessions_deleted,
            "teardown": teardown.get("deleted", {}),
            "scenes_deployed": len(deployment.get("scenes", [])),
            "npcs_deployed": len(deployment.get("npcs", [])),
            "enrichment": deployment.get("scene_enrichment", {}),
            "message": "Campaign restarted — use Start Session to begin from the opening scene.",
        }
    except FileNotFoundError as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(status="error", error=str(e), code="CAMPAIGN_NOT_FOUND").model_dump(),
        )
    except Exception as e:
        logger.exception(f"Campaign restart failed: {request.campaign_name}")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(status="error", error=str(e), code="RESTART_FAILED").model_dump(),
        )


@app.post("/api/campaign/start", response_model=CampaignStartResponse)
async def start_campaign_endpoint(request: CampaignStartRequest, state: AppState = Depends(get_app_state)):

    """Start (or continue) a campaign session.

    If continue_from_last is True, loads the previous session's state.
    Otherwise, creates a fresh session and loads the campaign context.
    """
    global state_tracker, chat_listener, llm_manager, db

    try:
        # Get active session
        active_session = await state.db.get_active_session()

        if request.continue_from_last and active_session:
            # Continue from last session
            logger.info(f"Continuing session: {active_session}")
            session_id = active_session
        else:
            # Create new session
            session_id = str(uuid.uuid4())[:8]
            await state.db.create_session(session_id, request.campaign_name)
            logger.info(f"Created new session: {session_id}")

        # Update state tracker
        await state.state_tracker.set_campaign(request.campaign_name)
        await state.state_tracker.set_mode(GameMode.EXPLORATION)
        await state.state_tracker.save()

        # Load campaign vault files into the AI context
        if state.campaign_loader:
            await state.campaign_loader.load(request.campaign_name)
            logger.info(f"Loaded campaign context for '{request.campaign_name}'")
            if state.npc_registry:
                state.campaign_loader.register_vault_npcs(state.npc_registry)

        # Persist the world↔campaign association so future startups can auto-load.
        if request.campaign_name and state.foundry_client:
            try:
                from campaign.obsidian_sync import link_world_to_campaign
                _wjs = "return {title: game.world?.title ?? '', id: game.world?.id ?? ''};"
                _wres = await state.foundry_client.execute_js(_wjs)
                _wtitle = (_wres.get("result") or {}).get("title", "") if isinstance(_wres, dict) else ""
                if _wtitle:
                    link_world_to_campaign(request.campaign_name, _wtitle)
            except Exception as _le:
                logger.debug(f"[WorldMatch] Could not link world to campaign: {_le}")

        # Refresh active-modules list so new campaign prompt reflects current Foundry setup
        if state.foundry_client and state.llm_manager:
            try:
                _mscan = await state.foundry_client.scan_world()
                _mods = [
                    m.get("title") or m.get("name") or m.get("id")
                    for m in (_mscan.get("modules") or [])
                    if m.get("active") or m.get("enabled")
                ]
                state.llm_manager.set_active_modules([m for m in _mods if m])
            except Exception as _me:
                logger.debug(f"[Modules] Module refresh failed: {_me}")

        # Invalidate cached system prompt so the LLM picks up the new campaign context
        if state.llm_manager and hasattr(state.llm_manager, 'invalidate_system_prompt'):
            state.llm_manager.invalidate_system_prompt()
        if state.chat_listener and hasattr(state.chat_listener, 'reload_system_prompt'):
            await state.chat_listener.reload_system_prompt()
        elif state.chat_listener and hasattr(state.chat_listener, '_build_system_prompt'):
            state.chat_listener._build_system_prompt()

        # Reset message ID for clean conversation
        if state.foundry_client:
            state.foundry_client.reset_message_id()

        # Ensure the world is unpaused and the AI is running before the opening
        # narration fires. A world left paused from a previous session would
        # otherwise trigger our pauseGame hook and suppress the session_start.
        if state.foundry_client:
            try:
                await state.foundry_client.execute_js(
                    "if(game.paused){game.togglePause(false,true);}"
                )
            except Exception as _pe:
                logger.warning(f"Could not unpause Foundry on campaign start: {_pe}")

        # Reset idle timer and fire a session_start opening so the AI sets up
        # the scene and places tokens rather than waiting for the first player message.
        if state.chat_listener:
            state.chat_listener._running = True
            state.chat_listener._reset_idle_timer()
            spawn(state.chat_listener._process_proactive_action(reason="session_start"))

        # Broadcast session start so dashboard updates
        await broadcast_state_update({
            "type": "session_started",
            "session_id": session_id,
            "campaign_name": request.campaign_name,
        })

        return CampaignStartResponse(
            status="started",
            session_id=session_id,
            campaign_name=request.campaign_name,
            message=f"Session {session_id} started for campaign '{request.campaign_name}'.",
        )
    except Exception as e:
        logger.exception("Failed to start campaign")
        return CampaignStartResponse(
            status="error",
            session_id="",
            campaign_name=request.campaign_name,
            error=str(e),
        )


@app.post("/api/session/end", response_model=SessionEndResponse)
async def end_session_endpoint(request: SessionEndRequest, state: AppState = Depends(get_app_state)):

    """End the current session prematurely.

    This generates a session summary and marks the session as ended.
    Players can use this at any time during gameplay.
    """
    try:
        # Pause the chat listener if running
        if state.chat_listener and state.chat_listener._running:
            state.chat_listener._running = False
            await broadcast_state_update({"type": "ai_paused", "reason": request.reason})

        # Get active session
        session_id = await state.db.get_active_session()
        if not session_id:
            return SessionEndResponse(
                status="no_active_session",
                session_id="",
                campaign_name="",
                message="No active session to end.",
            )

        # Get current state for summary
        state_snapshot = state.state_tracker.state.model_dump() if state.state_tracker else {}

        # Generate a brief summary using LLM if available
        summary_text = ""
        if state.llm_manager:
            try:
                summary_text = await state.llm_manager.generate(
                    user_message=f"Summarize this D&D session ending. Current state: {json.dumps(state_snapshot, default=str)}. Keep it brief (2-3 sentences) and note any important plot points, unresolved quests, or character moments.",
                )
                summary_text = json.dumps(summary_text, default=str)
            except Exception:
                summary_text = json.dumps(state_snapshot, default=str)

        # End the session
        await state.db.close_session(session_id)

        # Broadcast end event
        await broadcast_state_update({
            "type": "session_ended",
            "session_id": session_id,
            "reason": request.reason,
            "summary": summary_text,
        })

        return SessionEndResponse(
            status="ended",
            session_id=session_id,
            campaign_name=state.state_tracker.state.campaign if state.state_tracker else "",
            summary=summary_text,
            message=f"Session {session_id} ended. {request.reason}",
        )
    except Exception as e:
        logger.exception("Failed to end session")
        return SessionEndResponse(
            status="error",
            session_id="",
            campaign_name="",
            error=str(e),
        )


@app.get("/api/campaign/list", response_model=CampaignListResponse)
async def list_campaigns_endpoint(state: AppState = Depends(get_app_state)):

    """List all generated campaigns in the vault."""
    try:
        from campaign.obsidian_sync import list_campaigns
        campaigns = list_campaigns()
        return CampaignListResponse(campaigns=campaigns)
    except Exception as e:
        return CampaignListResponse(
            campaigns=[],
            error=str(e),
        )


@app.get("/api/campaign/get/{campaign_name}")
async def get_campaign_endpoint(campaign_name: str, state: AppState = Depends(get_app_state)):

    """Get a specific campaign's data."""
    try:
        from campaign.obsidian_sync import get_campaign_manifest, get_campaign_folder, resolve_vault_path

        vault = resolve_vault_path(settings.campaign_vault_path)
        folder = get_campaign_folder(vault, campaign_name)

        manifest = get_campaign_manifest(folder)
        if manifest:
            # Also load the campaign JSON
            import json
            campaign_file = folder / "campaign.json"
            if campaign_file.exists():
                with open(campaign_file) as f:
                    data = json.load(f)
                manifest["data"] = data

            # Add computed counts for frontend display
            manifest["npc_count"] = len(manifest.get("npcs", []))
            manifest["location_count"] = len(manifest.get("locations", [])) or len(manifest.get("locations_list", [])) or 0
            manifest["quest_count"] = len(manifest.get("quests", [])) or len(manifest.get("quest_logs", [])) or 0
            manifest["journal_entries"] = len(manifest.get("journal_entries", []))

            return manifest
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                status="error",
                error=f"Campaign '{campaign_name}' not found",
                code="CAMPAIGN_NOT_FOUND"
            ).model_dump()
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Failed to load campaign: {str(e)}",
                code="CAMPAIGN_LOAD_FAILED"
            ).model_dump()
        )


class CampaignDeleteRequest(BaseModel):
    """Request body for deleting a campaign."""
    name: str


@app.post("/api/campaign/delete", response_model=dict)
async def delete_campaign_endpoint(request: CampaignDeleteRequest, state: AppState = Depends(get_app_state)):
    """Delete a campaign from the vault."""
    try:
        from campaign.obsidian_sync import delete_campaign
        deleted = await delete_campaign(request.name)
        return {"status": "deleted" if deleted else "not_found"}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Failed to delete campaign: {str(e)}",
                code="CAMPAIGN_DELETE_FAILED"
            ).model_dump()
        )


@app.post("/api/campaign/enrich-scenes")
async def enrich_scenes_endpoint(state: AppState = Depends(get_app_state)):
    """Manually trigger enrichment (place walls, lights, sounds) on deployed scenes."""
    require_foundry(state)
    if not state.campaign_loader or not state.campaign_loader.current_campaign_name:
        raise ApiError("No campaign currently loaded", "NO_CAMPAIGN_LOADED", 400)

    campaign_name = state.campaign_loader.current_campaign_name
    campaign_data = await state.campaign_loader.load_campaign(campaign_name)
    deployment = await CampaignStore(campaign_name).load_deployment()
    if not deployment:
        raise ApiError(f"Deployment state not found for {campaign_name}", "DEPLOYMENT_NOT_FOUND", 404)

    try:
        # Run enrichment
        orchestrator = CampaignOrchestrator()
        result = await orchestrator.enrich_scenes(
            campaign_data=campaign_data,
            foundry_client=state.foundry_client,
            deployment=deployment,
            on_progress=lambda msg, **kw: logger.info(f"[Enrich] {msg}")
        )

        return {
            "status": "ok",
            "message": "Enrichment complete",
            "enriched": result.get("enriched", 0),
            "skipped": result.get("skipped", 0),
            "errors": result.get("errors", [])
        }
    except Exception as e:
        logger.error(f"Enrichment error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Enrichment failed: {str(e)}",
                code="ENRICHMENT_FAILED"
            ).model_dump()
        )


class OptimizeCampaignRequest(BaseModel):
    """Request to analyze and optimize a campaign."""
    campaign_name: str


@app.post("/api/campaign/analyze-and-optimize")
async def analyze_and_optimize_campaign(request: OptimizeCampaignRequest, state: AppState = Depends(get_app_state)):
    """Analyze campaign and generate module-based optimization recommendations."""
    require_foundry(state)
    if not request.campaign_name:
        raise ApiError("Campaign name required", "NO_CAMPAIGN_PROVIDED", 400)

    store = CampaignStore(request.campaign_name)
    campaign_data = await store.load()

    try:
        # Analyze locally (fast, no LLM) and APPLY the enhancements directly:
        # walls, doors, lights, sounds, and fog/vision config on deployed
        # scenes. The previous implementation spent 30+ minutes of LLM calls
        # (module discovery, synergy mapping, story enrichment) producing a
        # report that was never applied to Foundry.
        from campaign.analyzer import CampaignAnalyzer
        from campaign.orchestrator import CampaignOrchestrator

        analyzer = CampaignAnalyzer()
        analysis = await analyzer.analyze_campaign(campaign_data)

        orch = CampaignOrchestrator()
        scan = await orch.scan_foundry_world(state.foundry_client)
        active_modules = scan.get("active_modules", {})

        # Apply scene enrichment to everything this campaign has deployed
        enrich_summary = {"enriched": 0, "skipped": 0, "errors": ["not deployed yet — deploy the campaign first"]}
        deployment = await store.load_deployment()
        if deployment:
            enrich_summary = await orch.enrich_scenes(
                campaign_data, state.foundry_client, deployment
            )

        recommendations = [{
            "priority": "high",
            "category": "Applied",
            "count": enrich_summary.get("enriched", 0),
            "action": (
                f"Enriched {enrich_summary.get('enriched', 0)} scene(s) with walls, "
                f"lights, sounds, and fog/vision config ({enrich_summary.get('skipped', 0)} skipped)"
            ),
            "details": enrich_summary.get("errors", [])[:3],
        }]
        for gap in analysis.get("immersion_gaps", [])[:3]:
            recommendations.append({
                "priority": "medium",
                "category": "Immersion Gap",
                "count": 1,
                "action": gap if isinstance(gap, str) else str(gap),
                "details": [],
            })

        return {
            "status": "complete",
            "campaign_name": request.campaign_name,
            "analysis": {
                "scene_count": len(analysis.get("scenes", [])),
                "encounter_count": len(analysis.get("encounters", [])),
                "npc_count": len(analysis.get("npcs", [])),
                "narrative_arcs": len(analysis.get("narrative_arcs", [])),
                "drama_analysis": analysis.get("pacing", {}),
                "immersion_gaps_identified": len(analysis.get("immersion_gaps", [])),
            },
            "modules": {
                "total_installed": len(active_modules),
                "enabled": len(active_modules),
                "modules_list": [
                    {
                        "id": mid,
                        "name": info.get("title", mid),
                        "enabled": True,
                        "capabilities": [],
                        "narrative_uses": [],
                    }
                    for mid, info in active_modules.items()
                ],
            },
            "applied": {
                "scenes_enriched": enrich_summary.get("enriched", 0),
                "scenes_skipped": enrich_summary.get("skipped", 0),
                "errors": enrich_summary.get("errors", []),
            },
            "synergies": {
                "scene_synergies": enrich_summary.get("enriched", 0),
                "encounter_synergies": 0,
                "npc_synergies": 0,
                "immersion_gap_fills": 0,
                "details": {},
            },
            "enhancements": {"applied": True},
            "recommendations": recommendations,
        }

    except Exception as e:
        logger.error(f"Campaign optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Campaign optimization failed: {str(e)}",
                code="OPTIMIZATION_FAILED"
            ).model_dump()
        )


class OptimizeSceneRequest(BaseModel):
    """Request to optimize a newly created scene."""
    scene: dict
    campaign_name: Optional[str] = None


class OptimizeEncounterRequest(BaseModel):
    """Request to optimize a newly created encounter."""
    encounter: dict
    campaign_name: Optional[str] = None


class OptimizeQuestRequest(BaseModel):
    """Request to optimize a newly created quest."""
    quest: dict
    campaign_name: Optional[str] = None


@app.post("/api/campaign/auto-optimize-scene")
async def auto_optimize_scene(request: OptimizeSceneRequest, state: AppState = Depends(get_app_state)):
    """Auto-optimize a newly created scene with module enhancements."""
    if not state.campaign_loader or not state.foundry_client or not state.foundry_client.is_connected:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Foundry not connected or campaign loader not ready",
                code="AUTO_OPTIMIZE_NOT_READY"
            ).model_dump()
        )

    try:
        from campaign.auto_optimizer import AutoOptimizer

        campaign_name = request.campaign_name or state.campaign_loader.current_campaign_name
        if not campaign_name:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    status="error",
                    error="No campaign name provided or loaded",
                    code="NO_CAMPAIGN"
                ).model_dump()
            )

        campaign_data = await state.campaign_loader.load_campaign(campaign_name)

        optimizer = AutoOptimizer(
            llm_manager=state.llm_manager,
            foundry_client=state.foundry_client
        )
        result = await optimizer.optimize_new_scene(request.scene, campaign_data)

        return {
            "status": "optimized",
            "scene": request.scene.get("name"),
            "enhancements": result,
        }

    except Exception as e:
        logger.error(f"Scene auto-optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Scene auto-optimization failed: {str(e)}",
                code="SCENE_OPTIMIZE_FAILED"
            ).model_dump()
        )


@app.post("/api/campaign/auto-optimize-encounter")
async def auto_optimize_encounter(request: OptimizeEncounterRequest, state: AppState = Depends(get_app_state)):
    """Auto-optimize a newly created encounter with module enhancements."""
    if not state.campaign_loader or not state.foundry_client or not state.foundry_client.is_connected:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Foundry not connected or campaign loader not ready",
                code="AUTO_OPTIMIZE_NOT_READY"
            ).model_dump()
        )

    try:
        from campaign.auto_optimizer import AutoOptimizer

        campaign_name = request.campaign_name or state.campaign_loader.current_campaign_name
        if not campaign_name:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    status="error",
                    error="No campaign name provided or loaded",
                    code="NO_CAMPAIGN"
                ).model_dump()
            )

        campaign_data = await state.campaign_loader.load_campaign(campaign_name)

        optimizer = AutoOptimizer(
            llm_manager=state.llm_manager,
            foundry_client=state.foundry_client
        )
        result = await optimizer.optimize_new_encounter(request.encounter, campaign_data)

        return {
            "status": "optimized",
            "encounter": request.encounter.get("name"),
            "enhancements": result,
        }

    except Exception as e:
        logger.error(f"Encounter auto-optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Encounter auto-optimization failed: {str(e)}",
                code="ENCOUNTER_OPTIMIZE_FAILED"
            ).model_dump()
        )


@app.post("/api/campaign/auto-optimize-quest")
async def auto_optimize_quest(request: OptimizeQuestRequest, state: AppState = Depends(get_app_state)):
    """Auto-optimize a newly created quest with narrative enhancements."""
    if not state.campaign_loader or not state.foundry_client or not state.foundry_client.is_connected:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Foundry not connected or campaign loader not ready",
                code="AUTO_OPTIMIZE_NOT_READY"
            ).model_dump()
        )

    try:
        from campaign.auto_optimizer import AutoOptimizer

        campaign_name = request.campaign_name or state.campaign_loader.current_campaign_name
        if not campaign_name:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    status="error",
                    error="No campaign name provided or loaded",
                    code="NO_CAMPAIGN"
                ).model_dump()
            )

        campaign_data = await state.campaign_loader.load_campaign(campaign_name)

        optimizer = AutoOptimizer(
            llm_manager=state.llm_manager,
            foundry_client=state.foundry_client
        )
        result = await optimizer.optimize_new_quest(request.quest, campaign_data)

        return {
            "status": "optimized",
            "quest": request.quest.get("title"),
            "enhancements": result,
        }

    except Exception as e:
        logger.error(f"Quest auto-optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Quest auto-optimization failed: {str(e)}",
                code="QUEST_OPTIMIZE_FAILED"
            ).model_dump()
        )


@app.get("/api/comfyui/health")
async def check_comfyui_health(state: AppState = Depends(get_app_state)):
    """Check if ComfyUI is available."""
    try:
        from campaign.map_generator import MapGenerator
        mg = MapGenerator(settings.comfyui_url, checkpoint_name=settings.comfyui_checkpoint)
        healthy = await mg.health_check()
        await mg.close()
        return {"healthy": healthy, "url": settings.comfyui_url}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error=f"ComfyUI health check failed: {str(e)}",
                code="COMFYUI_HEALTH_CHECK_FAILED"
            ).model_dump()
        )


@app.get("/api/comfyui/models")
async def list_comfyui_models(state: AppState = Depends(get_app_state)):
    """List available ComfyUI models."""
    try:
        from campaign.map_generator import MapGenerator
        mg = MapGenerator(settings.comfyui_url, checkpoint_name=settings.comfyui_checkpoint)
        models = await mg.get_models()
        await mg.close()
        return {"models": models}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error=f"Failed to list ComfyUI models: {str(e)}",
                code="COMFYUI_MODELS_FAILED"
            ).model_dump()
        )


@app.post("/api/roll")
async def roll_dice(request: Request, state: AppState = Depends(get_app_state)):
    """Roll dice in FoundryVTT and return the result."""
    try:
        data = await request.json()
        result = await state.foundry_client.roll(
            data.get("formula", "1d20"),
            speaker=data.get("speaker", "GM"),
            flavor=data.get("flavor", "")
        )
        return result or {"ok": True}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Dice roll failed: {str(e)}",
                code="DICE_ROLL_FAILED"
            ).model_dump()
        )


@app.websocket("/api/ws")
async def admin_websocket(websocket: WebSocket):

    """WebSocket endpoint for admin panel real-time updates."""
    await websocket.accept()
    websocket_clients.append(websocket)
    state = websocket.app.state
    logger.info(f"Admin panel connected (total: {len(websocket_clients)})")

    try:
        while True:
            # Read messages from admin panel (for commands)
            data = await websocket.receive_text()
            msg = json.loads(data)

            # Rate limit: max 5 messages per second per connection
            # Check rate limiting AFTER receiving (not before busy-spinning)
            now = time.time()
            if websocket in _admin_ws_rate and now - _admin_ws_rate[websocket] < 0.2:
                await websocket.send_text(json.dumps({"type": "rate_limited"}))
                continue
            _admin_ws_rate[websocket] = now

            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif msg.get("type") == "pause":
                state.chat_listener._running = False
                if state.foundry_client:
                    try:
                        await state.foundry_client.execute_js(
                            "if(!game.paused){game.togglePause(true,true);}"
                        )
                    except Exception as _e:
                        logger.warning(f"Admin pause: Foundry togglePause failed: {_e}")
                await broadcast_state_update({"type": "ai_paused"})
            elif msg.get("type") == "resume":
                state.chat_listener._running = True
                if state.foundry_client:
                    try:
                        await state.foundry_client.execute_js(
                            "if(game.paused){game.togglePause(false,true);}"
                        )
                    except Exception as _e:
                        logger.warning(f"Admin resume: Foundry togglePause failed: {_e}")
                if state.chat_listener:
                    state.chat_listener._reset_idle_timer()
                await broadcast_state_update({"type": "ai_resumed"})
            elif msg.get("type") == "roll_command":
                formula = msg.get("formula", "1d20")
                speaker = msg.get("speaker", "GM")
                flavor = msg.get("flavor", "")
                await state.foundry_client.roll(formula, speaker=speaker, flavor=flavor)
    except WebSocketDisconnect:
        logger.info("Admin panel disconnected")
    except Exception as e:
        logger.error(f"Admin WebSocket error: {e}", exc_info=True)
    finally:
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)
        _admin_ws_rate.pop(websocket, None)


# --- Entry Point ---

if __name__ == "__main__":
    import uvicorn
    # Default to localhost for security; override with ADMIN_HOST env var if needed
    admin_host = os.getenv("ADMIN_HOST", "127.0.0.1")
    uvicorn.run(
        "main:app",
        host=admin_host,
        port=settings.admin_port,
        log_level="info"
    )
