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
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from typing import Dict

# Add the ai-engine directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from foundry.client import FoundryClient
from foundry.chat_listener import ChatListener
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
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

    # 0. Create the relay manager, but defer the relay process and Foundry
    # connection until the GM explicitly starts the relay or starts a campaign.
    from relay_proc import RelayManager
    relay_manager = RelayManager()
    app.state.relay_manager = relay_manager
    logger.info("Relay and Foundry connection deferred until campaign start")

    # 1. Initialize database
    db = Database(settings.sqlite_db)
    app.state.db = db
    await db.init()
    logger.info("Database initialized")
    # Apply retention policy to clean up old data on startup
    await db.apply_retention_policy()

    # 2. Initialize semantic indexer (P2b: Vault RAG)
    semantic_indexer = None
    if settings.vault_embeddings_enabled:
        from vault.embeddings import LocalEmbeddings, OllamaEmbeddings, OpenAIEmbeddings, CachedEmbeddings
        from vault.indexer import SemanticIndexer

        try:
            # Create embedding provider
            if settings.vault_embeddings_provider == "local":
                embeddings = LocalEmbeddings(model=settings.vault_embeddings_model)
            elif settings.vault_embeddings_provider == "openai":
                if not settings.llm_api_key:
                    raise ValueError("OpenAI embeddings require LLM_API_KEY")
                embeddings = OpenAIEmbeddings(api_key=settings.llm_api_key, model=settings.vault_embeddings_model)
            elif settings.vault_embeddings_provider == "ollama":
                embeddings = OllamaEmbeddings(model=settings.vault_embeddings_model)
            else:
                raise ValueError(f"Unknown embeddings provider: {settings.vault_embeddings_provider}")

            # Wrap with caching
            cached_embeddings = CachedEmbeddings(embeddings, cache_dir=settings.vault_embeddings_cache_dir)

            # Create indexer with query caching
            semantic_indexer = SemanticIndexer(
                cached_embeddings,
                index_path=settings.vault_index_path,
                cache_enabled=settings.vault_query_cache_enabled,
                cache_size=settings.vault_query_cache_size,
                cache_ttl_seconds=settings.vault_query_cache_ttl_seconds
            )
            app.state.semantic_indexer = semantic_indexer
            logger.info(
                f"Semantic indexer initialized (provider={settings.vault_embeddings_provider}, "
                f"embedding_cache={settings.vault_embeddings_cache_dir}, "
                f"query_cache={settings.vault_query_cache_enabled})"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize semantic indexer: {e}. Falling back to keyword search.")

    # Initialize semantic RAG (P2b: Context injection)
    semantic_rag = None
    if semantic_indexer:
        from vault.vault_semantic_rag import SemanticRAG
        semantic_rag = SemanticRAG(semantic_indexer, debounce_seconds=30.0)
        logger.info("Semantic RAG initialized for context injection")

    # 2a. Initialize campaign loader with semantic indexer
    campaign_loader = CampaignLoader(semantic_indexer=semantic_indexer)
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

    # 3b. Optional second, cheaper model for NPC self-initiated turns
    # (npc/agent.py via llm/router.py's ModelRouter). Unset by default —
    # NPC turns route through llm_manager like everything else until a
    # distinct model is actually configured.
    npc_llm_manager = None
    if settings.npc_agent_model:
        npc_llm_manager = LLMManager(campaign_loader=campaign_loader, model=settings.npc_agent_model)
        app.state.npc_llm_manager = npc_llm_manager
        logger.info(f"NPC-tier LLM Manager initialized (model={settings.npc_agent_model})")

    # 4. Initialize the Foundry client. Connection is campaign-gated so the
    # Admin UI can be used to select/build a campaign while the relay is down.
    foundry_client = FoundryClient()
    app.state.foundry_client = foundry_client
    # Self-heal hook: relaunch the headless Foundry session if the relay loses
    # its Foundry client (headless tab died / module dropped).
    if settings.relay_managed and settings.relay_allow_headless:
        foundry_client._relaunch_headless = relay_manager.restart_headless_session
    logger.info("FoundryVTT connection deferred until campaign start")

    # 5. Initialize action dispatcher (pass app_state for access to all managers)
    from actions.approval import ApprovalWorkflow
    approval_workflow = ApprovalWorkflow(
        mode=settings.approval_mode,
        timeout_seconds=settings.approval_timeout_seconds
    )
    action_dispatcher = ActionDispatcher(foundry_client, app_state=app.state, approval_workflow=approval_workflow)
    app.state.action_dispatcher = action_dispatcher
    app.state.approval_workflow = approval_workflow
    logger.info("Action dispatcher initialized with approval gate")

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
        npc_llm=npc_llm_manager,
        semantic_rag=semantic_rag,
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

    # Wire approval workflow to chat listener (initialized earlier with dispatcher)
    chat_listener._approval_workflow = app.state.approval_workflow

    # Include state-dependent routers after app.state is fully initialized
    from api.routes import approval as approval_routes
    from api.routes import session_control as session_control_routes
    app.include_router(approval_routes.create_approval_router(app.state))
    app.include_router(session_control_routes.create_session_control_router(app.state))
    logger.info("Approval and session control routers registered")

    logger.info("AI Gamemaster Engine is RUNNING — ready for campaign selection")

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
    # Close LLM manager(s) to release HTTP connections
    if llm_manager:
        try:
            await llm_manager.close()
        except Exception:
            pass
    if npc_llm_manager:
        try:
            await npc_llm_manager.close()
        except Exception:
            pass
    if tts_service:
        await tts_service.close()
    if relay_manager and settings.relay_managed:
        await relay_manager.stop()
    logger.info("Shutdown complete")


# --- WebSocket broadcast for admin panel ---

_admin_ws_rate: Dict[WebSocket, float] = {}
_api_rate: Dict[str, list[float]] = {}
_api_rate_lock = asyncio.Lock()
_API_RATE_MAX_CLIENTS = 10_000




# --- FastAPI App ---

app = FastAPI(
    title="Sage - AI D&D Gamemaster",
    description="AI D&D 5e Gamemaster integrated with FoundryVTT",
    version="0.1.0",
    lifespan=lifespan
)


@app.middleware("http")
async def protect_api_resources(request: Request, call_next):
    """Apply size/rate limits and, when ADMIN_TOKEN is set, require it on /api/*."""
    if request.url.path.startswith("/api/"):
        if settings.admin_token:
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not secrets.compare_digest(supplied, settings.admin_token):
                return JSONResponse(status_code=401, content={"error": "Authentication required"})
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > settings.max_request_body_bytes
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse(status_code=413, content={"error": "Request body too large"})
        now = time.time()
        client = request.client.host if request.client else "unknown"
        async with _api_rate_lock:
            bucket = [t for t in _api_rate.get(client, []) if now - t < 60]
            if len(_api_rate) > _API_RATE_MAX_CLIENTS:
                # Remove inactive buckets before admitting another client. This
                # keeps the LAN limiter bounded when client IPs rotate frequently.
                cutoff = now - 60
                _api_rate.update({ip: times for ip, times in _api_rate.items() if times and times[-1] >= cutoff})
            if len(bucket) >= settings.api_requests_per_minute:
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
            bucket.append(now)
            _api_rate[client] = bucket
    return await call_next(request)

# CORS — Foundry runs on a different origin (e.g. localhost:30000) than this
# engine (localhost:18080). Foundry's AudioHelper decodes TTS audio via the Web
# Audio API, which silently fails on cross-origin responses without these
# headers. Allow all origins (local-only service).
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers extracted from main.py (Phase 1 of the modular architecture split,
# docs/ARCHITECTURE_REFACTOR.md). More domains move here incrementally.
from api.routes import campaign as campaign_routes  # noqa: E402
from api.routes import canon as canon_routes  # noqa: E402
from api.routes import control as control_routes  # noqa: E402
from api.routes import combat as combat_routes  # noqa: E402
from api.routes import immersion as immersion_routes  # noqa: E402
from api.routes import npc as npc_routes  # noqa: E402
from api.routes import procedural as procedural_routes  # noqa: E402
from api.routes import rules as rules_routes  # noqa: E402
from api.routes import scene as scene_routes  # noqa: E402
from api.routes import session as session_routes  # noqa: E402
from api.routes import system as system_routes  # noqa: E402

app.include_router(campaign_routes.router)
app.include_router(canon_routes.router)
app.include_router(control_routes.router)
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
    logger.info("Campaign lookup failed: %s", exc)
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(
            status="error",
            error="Campaign not found",
            code="CAMPAIGN_NOT_FOUND",
        ).model_dump(),
    )

# Mount admin panel — prefer the Vite build output (dist/) when available,
# otherwise fall back to the standalone index.html at the panel root.
_panel_root = Path(__file__).parent / "admin-panel"
_panel_dist = _panel_root / "dist"
_admin_serve = _panel_dist if _panel_dist.exists() else _panel_root
if _admin_serve.exists():
    app.mount("/admin", StaticFiles(directory=str(_admin_serve), html=True), name="admin")

# Serve generated TTS audio.
_tts_audio_dir = Path(__file__).parent / settings.tts_audio_dir
_tts_audio_dir.mkdir(parents=True, exist_ok=True)


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    audio_root = _tts_audio_dir.resolve()
    audio_path = (audio_root / filename).resolve()
    if Path(filename).name != filename or audio_path.parent != audio_root or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(audio_path)


@app.websocket("/api/ws")
async def admin_websocket(websocket: WebSocket):

    """WebSocket endpoint for admin panel real-time updates."""
    if len(websocket_clients) >= settings.ws_max_connections:
        await websocket.close(code=1013, reason="Too many connections")
        return
    await websocket.accept()
    if settings.admin_token:
        # Authenticate in-band so the token never appears in a URL.
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=5))
            ok = first.get("type") == "auth" and secrets.compare_digest(
                str(first.get("token") or ""), settings.admin_token
            )
        except (asyncio.TimeoutError, json.JSONDecodeError, TypeError, AttributeError):
            ok = False
        if not ok:
            await websocket.close(code=1008, reason="Authentication required")
            return
    websocket_clients.append(websocket)
    if not hasattr(websocket.app, 'state'):
        await websocket.close(code=1011, reason="Server not properly initialized")
        websocket_clients.remove(websocket)
        return
    state = websocket.app.state
    logger.info(f"Admin panel connected (total: {len(websocket_clients)})")

    try:
        while True:
            # Read messages from admin panel (for commands)
            data = await websocket.receive_text()
            if len(data.encode("utf-8")) > settings.ws_max_message_bytes:
                await websocket.close(code=1009, reason="Message too large")
                return
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
                await state.chat_listener.pause()
                if state.foundry_client:
                    try:
                        await state.foundry_client.execute_js(
                            "if(!game.paused){game.togglePause(true,true);}"
                        )
                    except Exception as _e:
                        logger.warning(f"Admin pause: Foundry togglePause failed: {_e}")
                await broadcast_state_update({"type": "ai_paused"})
            elif msg.get("type") == "resume":
                await state.chat_listener.resume()
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
    # Loopback-only by default; set ADMIN_HOST=0.0.0.0 (and ADMIN_TOKEN) in .env
    # to expose the admin API on the LAN.
    uvicorn.run(
        "main:app",
        host=settings.admin_host,
        port=settings.admin_port,
        log_level="info",
        reload=False,
        lifespan="on",
    )
