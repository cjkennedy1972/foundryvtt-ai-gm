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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from typing import Dict

# Add the ai-engine directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from foundry.client import FoundryClient
from foundry.chat_listener import ChatListener
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
from actions.executors import reset_action_caches
from state.tracker import GameStateTracker
from persistence.db import Database
from campaign.vault import CampaignNotFound
from context.loader import CampaignLoader
from context.window_manager import ContextWindowManager
from context.reinforcement_manager import ContextReinforcementManager
from combat.loop import CombatLoop
from scene.awareness import SceneAwareness
from relay_proc.manager import RelayManager
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


# GMSettings, SessionInfo, EventEntry, StateUpdate moved to api/routes/session.py
# (the only module that uses them).


# AppState, get_app_state, ErrorResponse, ApiError, require_foundry now live in
# api/deps.py so routers can import them without a circular import on main.
from api.deps import (  # noqa: E402
    ApiError,
    AppState,
    ErrorResponse,
    broadcast_state_update,
    get_app_state,
    require_foundry,
    websocket_clients,
)


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
        from tts.playback import configure as configure_tts
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
        from tts.playback import configure as configure_tts
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

            # Scan active modules and feed into LLM system prompt. Use the
            # dedicated active-module helper so startup doesn't repeat a full
            # world scan just to discover module state.
            try:
                _info = await foundry_client.get_active_modules_info(include_world=True)
                _modules = [
                    m.get("title") or m.get("name") or m.get("id")
                    for m in (_info.get("modules") or [])
                    if m.get("active") or m.get("enabled")
                ]
                if _modules and llm_manager:
                    llm_manager.set_active_modules(_modules)
                    logger.info(f"[Modules] {len(_modules)} active modules injected into system prompt")
            except Exception as _me:
                logger.warning(f"[Modules] Module scan failed (non-fatal): {_me}")

            reset_action_caches()

        except Exception as _e:
            logger.warning(f"[WorldMatch] World detection failed (non-fatal): {_e}")

    async def _reconnect_loop():
        """Periodically reconnect to the relay when disconnected."""
        delay = 5.0
        while True:
            await asyncio.sleep(delay)
            if not foundry_client.is_connected:
                logger.info(f"Relay disconnected — attempting reconnect (delay={delay:.1f}s)…")
                try:
                    await foundry_client.ensure_connected()
                    if foundry_client.is_connected:
                        delay = 5.0
                        continue
                except Exception as e:
                    logger.warning(f"Relay reconnect attempt failed: {e}")
                delay = min(delay * 1.5, 30.0)
            else:
                delay = 10.0

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

    # Set up callback for admin panel (must be before chat_listener.start() so
    # the first player message can trigger the callback)
    async def notify_admin(results):
        await broadcast_state_update({
            "type": "actions_executed",
            "actions": results
        })

    chat_listener.set_results_callback(notify_admin)

    from tts.playback import set_chat_listener
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

_admin_ws_rate: Dict[WebSocket, float] = {}




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
from api.routes import campaign as campaign_routes  # noqa: E402
from api.routes import combat as combat_routes  # noqa: E402
from api.routes import immersion as immersion_routes  # noqa: E402
from api.routes import npc as npc_routes  # noqa: E402
from api.routes import procedural as procedural_routes  # noqa: E402
from api.routes import rules as rules_routes  # noqa: E402
from api.routes import scene as scene_routes  # noqa: E402
from api.routes import session as session_routes  # noqa: E402
from api.routes import system as system_routes  # noqa: E402

app.include_router(campaign_routes.router)
app.include_router(combat_routes.router)
app.include_router(immersion_routes.router)
app.include_router(npc_routes.router)
app.include_router(procedural_routes.router)
app.include_router(rules_routes.router)
app.include_router(scene_routes.router)
app.include_router(session_routes.router)
app.include_router(system_routes.router)


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
