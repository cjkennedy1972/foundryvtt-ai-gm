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
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

# Add the ai-engine directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from foundry.client import FoundryClient
from foundry.chat_listener import ChatListener
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
from state.tracker import GameStateTracker
from state.models import GameState, GameMode, CombatState
from persistence.db import Database
from context.loader import CampaignLoader
from context.window_manager import ContextWindowManager
from context.reinforcement_manager import ContextReinforcementManager
from combat.loop import CombatLoop
from scene.awareness import SceneAwareness
from relay_proc.manager import RelayManager

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


# --- Global State ---

db: Database = None
foundry_client: FoundryClient = None
llm_manager: LLMManager = None
action_dispatcher: ActionDispatcher = None
state_tracker: GameStateTracker = None
chat_listener: ChatListener = None
campaign_loader: CampaignLoader = None
context_manager: ContextWindowManager = None
combat_loop: CombatLoop = None
scene_awareness: SceneAwareness = None
reinforcement_mgr: Optional[ContextReinforcementManager] = None
relay_manager: Optional[RelayManager] = None


# --- Context Manager ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    global db, foundry_client, llm_manager, action_dispatcher
    global state_tracker, chat_listener, campaign_loader
    global context_manager, combat_loop, scene_awareness
    global relay_manager

    logger.info("Initializing AI Gamemaster Engine...")

    # 0. Launch the embedded relay (must be up before the Foundry client connects)
    from relay_proc import RelayManager
    relay_manager = RelayManager()
    if settings.relay_managed:
        await relay_manager.start()
        await relay_manager.ensure_api_key()
        headless_client_id = await relay_manager.ensure_headless_session()
        if headless_client_id:
            settings.relay_headless_client_id = headless_client_id
        logger.info("Relay ready")

    # 1. Initialize database
    db = Database(settings.sqlite_db)
    await db.init()
    logger.info("Database initialized")

    # 2. Initialize campaign loader and load default campaign
    campaign_loader = CampaignLoader()
    await campaign_loader.load(settings.default_campaign)
    logger.info("Campaign context loaded")

    # 3. Initialize LLM manager (pass loader for context access)
    llm_manager = LLMManager(campaign_loader=campaign_loader)
    logger.info("LLM Manager initialized")

    # 4. Initialize Foundry client and connect
    foundry_client = FoundryClient()
    await foundry_client.connect()
    if foundry_client.is_connected:
        logger.info("FoundryVTT connected")
    else:
        logger.warning("Failed to connect to FoundryVTT — AI will not receive messages")

    # 5. Initialize action dispatcher
    action_dispatcher = ActionDispatcher(foundry_client)
    logger.info("Action dispatcher initialized")

    # 6. Initialize state tracker
    state_tracker = GameStateTracker(db)
    await state_tracker.load()
    logger.info("State tracker initialized")

    # 7. Auto-create session if none active
    if await db.get_active_session() is None:
        session_id = str(uuid.uuid4())[:8]
        await db.create_session(session_id, settings.default_campaign)
        state_tracker.set_campaign(settings.default_campaign)
        logger.info(f"Auto-created session: {session_id}")

    # 8. Set up context window manager
    from context.window_manager import ContextWindowManager
    context_manager = ContextWindowManager(
        max_tokens=settings.max_context_tokens,
        keep_system=True,
        keep_recent=20
    )
    logger.info("Context window manager initialized")

    # 9. Initialize scene awareness
    scene_awareness = SceneAwareness(
        foundry=foundry_client,
        state_tracker=state_tracker,
        campaign_loader=campaign_loader
    )
    logger.info("Scene awareness initialized")

    # 10. Initialize combat loop
    combat_loop = CombatLoop(
        foundry=foundry_client,
        llm=llm_manager,
        dispatcher=action_dispatcher,
        state_tracker=state_tracker,
        db=db,
        campaign_loader=campaign_loader
    )

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
    )
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
        reinforcement_mgr=reinforcement_mgr
    )

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
            websocket_clients.remove(ws)


# --- FastAPI App ---

app = FastAPI(
    title="Aethelwyrd AI GM Engine",
    description="AI D&D 5e Gamemaster integrated with FoundryVTT",
    version="0.1.0",
    lifespan=lifespan
)

# Mount admin panel — prefer the Vite build output (dist/) when available,
# otherwise fall back to the standalone index.html at the panel root.
_panel_root = Path(__file__).parent / "admin-panel"
_panel_dist = _panel_root / "dist"
_admin_serve = _panel_dist if _panel_dist.exists() else _panel_root
if _admin_serve.exists():
    app.mount("/admin", StaticFiles(directory=str(_admin_serve), html=True), name="admin")


@app.get("/")
async def admin_redirect():
    """Redirect to admin panel."""
    return RedirectResponse(url="/admin/index.html")


# --- Admin API Endpoints ---

@app.get("/api/status")
async def get_status():
    """Get current engine status."""
    return {
        "connected": foundry_client.is_connected if foundry_client else False,
        "ai_running": chat_listener._running if chat_listener else False,
        "model": llm_manager.model if llm_manager else settings.model,
        "campaign": state_tracker.state.campaign if state_tracker else "",
        "session": state_tracker.state.session_number if state_tracker else 0,
        "scene": state_tracker.state.current_scene if state_tracker else "",
        "mode": state_tracker.state.mode.value if state_tracker else "exploration",
        "conversation_length": len(llm_manager.conversation_history) if llm_manager else 0,
        "reinforcement_turns": reinforcement_mgr._turn_count if reinforcement_mgr else 0,
        "reinforcement_active": reinforcement_mgr._running if reinforcement_mgr else False,
        "relay": {
            **(relay_manager.status() if relay_manager else {"managed": False, "running": False}),
            "headless_client_id": settings.relay_headless_client_id or None,
        },
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers and orchestrators."""
    return {
        "status": "healthy",
        "components": {
            "db": db is not None,
            "foundry": foundry_client is not None and foundry_client.is_connected,
            "llm": llm_manager is not None,
            "chat_listener": chat_listener is not None and chat_listener._running,
            "combat_loop": combat_loop is not None,
        },
    }


@app.get("/api/context/reinforcement")
async def get_reinforcement_status():
    """Get context reinforcement status."""
    if not reinforcement_mgr:
        return {"active": False, "turns": 0, "messages": 0, "last_reinforcement": None}
    return {
        "active": reinforcement_mgr._running,
        "turns": reinforcement_mgr._turn_count,
        "message_count": reinforcement_mgr._message_count,
        "last_reinforcement": reinforcement_mgr._last_reinforcement_time,
        "status": reinforcement_mgr._status,
        "world_summary": reinforcement_mgr._world_summary,
        "anchors": reinforcement_mgr._get_anchor_facts(),
    }


@app.post("/api/context/reinforce")
async def trigger_reinforcement():
    """Manually trigger a context reinforcement pass."""
    if not reinforcement_mgr:
        return {"status": "error", "message": "Reinforcement manager not initialized"}
    try:
        summary = await reinforcement_mgr.reinforce_context()
        return {
            "status": "ok",
            "message": "Context reinforced",
            "summary_length": len(summary) if summary else 0,
        }
    except Exception as e:
        logger.error(f"Reinforcement error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.post("/api/context/summarize")
async def trigger_summarization():
    """Manually trigger a context summarization pass."""
    if not reinforcement_mgr:
        return {"status": "error", "message": "Reinforcement manager not initialized"}
    try:
        summary = await reinforcement_mgr.summarize_context()
        return {
            "status": "ok",
            "summary_length": len(summary) if summary else 0,
        }
    except Exception as e:
        logger.error(f"Summarization error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.post("/api/context/world_summary")
async def update_world_summary():
    """Update the world summary with current game state."""
    if not reinforcement_mgr:
        return {"status": "error", "message": "Reinforcement manager not initialized"}
    try:
        # Gather current state from all sources
        state_dict = state_tracker.state.model_dump() if state_tracker else {}
        scene_data = scene_awareness.get_context_summary() if scene_awareness else ""
        await reinforcement_mgr.update_world_summary(state_dict, scene_data)
        return {"status": "ok", "message": "World summary updated"}
    except Exception as e:
        logger.error(f"World summary update error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/api/state")
async def get_state():
    """Get full game state."""
    if state_tracker:
        return state_tracker.state.model_dump()
    return {}


@app.get("/api/settings", response_model=GMSettings)
async def get_settings():
    """Return current AI GM settings from config."""
    return GMSettings(
        model=settings.model,
        llm_base_url=settings.llm_base_url,
        llm_api_key=settings.llm_api_key,
        temperature=settings.temperature,
        ai_name=settings.ai_name,
        ai_tone=settings.ai_tone,
        relay_url=settings.relay_url,
        relay_api_key=settings.relay_api_key,
        comfyui_url=settings.comfyui_url,
    )


@app.post("/api/settings", response_model=GMSettings)
async def update_settings(settings_data: GMSettings):
    """Update AI GM settings.
    
    Note: LLM base_url/api_key changes require a restart to take effect.
    """
    if llm_manager:
        llm_manager._temperature = settings_data.temperature
        llm_manager._ai_tone = settings_data.ai_tone
    if foundry_client:
        foundry_client.set_ai_name(settings_data.ai_name)
    
    # Apply non-secret runtime changes immediately
    if settings_data.comfyui_url:
        settings.comfyui_url = settings_data.comfyui_url
    if settings_data.model:
        settings.model = settings_data.model
        if llm_manager:
            llm_manager.model = settings_data.model
    if settings_data.ai_tone:
        settings.ai_tone = settings_data.ai_tone
        if llm_manager:
            llm_manager._ai_tone = settings_data.ai_tone
    if settings_data.ai_name:
        settings.ai_name = settings_data.ai_name
    if settings_data.temperature is not None:
        settings.temperature = settings_data.temperature
    if settings_data.relay_url:
        settings.relay_url = settings_data.relay_url
    
    return settings_data


@app.post("/api/state/update", response_model=dict)
async def update_game_state(state_data: StateUpdate):
    """Update game state manually."""
    if state_data.mode:
        state_tracker.set_mode(GameMode(state_data.mode))
    if state_data.scene:
        state_tracker.set_scene(state_data.scene)
    if state_data.session:
        state_tracker.state.session_number = state_data.session
    if state_data.campaign:
        state_tracker.set_campaign(state_data.campaign)
    await state_tracker.save()
    return {"status": "ok", "state": state_tracker.state.model_dump()}


@app.post("/api/campaign/load", response_model=dict)
async def load_campaign(campaign: CampaignCreate):
    """Load or create a new campaign with its own vault subfolder."""
    if campaign_loader:
        result = await campaign_loader.load_custom_campaign(
            campaign.name, campaign.vault_files
        )
        return {
            "status": "ok",
            "name": campaign.name,
            "folder": result.get("folder", ""),
            "loaded_files": result.get("linked_files", []),
        }


@app.get("/api/session/active")
async def get_active_session():
    """Get active session info."""
    session_id = await db.get_active_session()
    if session_id:
        return {"session_id": session_id, "active": True}
    return {"session_id": None, "active": False}


@app.post("/api/session/new", response_model=SessionInfo)
async def create_session(campaign: str = None):
    """Create a new game session."""
    if campaign is None:
        campaign = settings.default_campaign
    import uuid
    session_id = str(uuid.uuid4())[:8]
    await db.create_session(session_id, campaign)
    state_tracker.set_campaign(campaign)
    return SessionInfo(
        session_id=session_id,
        campaign=campaign,
        started_at="now"
    )


@app.get("/api/session/events", response_model=List[EventEntry])
async def get_session_events(limit: int = 50):
    """Get session event history."""
    session_id = await db.get_active_session()
    if session_id:
        events = await db.get_events(session_id, limit)
        return events
    return []


class ChatTestRequest(BaseModel):
    message: str
    speaker: str = "Selmor"


@app.post("/api/chat/test")
async def test_chat(request: ChatTestRequest):
    """Test the AI with a manual chat message."""
    if not chat_listener or not llm_manager:
        return {"error": "Engine not initialized"}

    game_state = state_tracker.get_snapshot() if state_tracker else ""
    npc_context = await campaign_loader.get_npc_context() if campaign_loader else ""

    try:
        result = await llm_manager.generate(
            user_message=f"[{request.speaker}]: {request.message}",
            game_state_summary=game_state,
            extra_context=npc_context
        )
        actions = result.get("actions", [])
        return {"actions": actions}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/npcs")
async def list_npcs():
    """List all NPC actors in Foundry."""
    if foundry_client and foundry_client.is_connected:
        try:
            actors = await foundry_client.get_actors(world_only=True)
            return {"npcs": actors}
        except Exception as e:
            return {"error": str(e)}
    return {"npcs": []}


@app.get("/api/srd/search")
async def search_srd(query: str, max_results: int = 3):
    """Search the SRD for rules reference."""
    if campaign_loader:
        results = await campaign_loader.search_srd(query, max_results)
        return {"results": results}
    return {"results": ""}


@app.post("/api/combat/start", response_model=dict)
async def start_combat_endpoint():
    """Start combat loop with tokens from current scene."""
    if not combat_loop:
        return {"error": "Combat loop not initialized"}
    if not foundry_client:
        return {"error": "Not connected to Foundry"}
    try:
        tokens = await foundry_client.get_scene_tokens()
        if not tokens:
            return {"error": "No tokens found on current scene"}
        await combat_loop.start_combat_loop(tokens)
        return {"status": "started", "tokens": len(tokens)}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/combat/stop", response_model=dict)
async def stop_combat_endpoint():
    """Stop the combat loop."""
    if combat_loop:
        await combat_loop.stop()
        return {"status": "stopped"}
    return {"status": "not running"}


@app.get("/api/combat/status", response_model=dict)
async def get_combat_status_endpoint():
    """Get combat loop status."""
    if combat_loop:
        return {
            "running": combat_loop.is_running,
            "round": combat_loop.current_round,
            "turn": combat_loop.current_turn,
            "turn_order": combat_loop.turn_order,
        }
    return {"running": False}


@app.post("/api/scene/switch", response_model=dict)
async def switch_scene_endpoint(scene_name: str = ""):
    """Switch to a different scene."""
    if not foundry_client:
        return {"error": "Not connected to Foundry"}
    try:
        await foundry_client.set_active_scene(scene_name)
        if scene_awareness:
            await scene_awareness.on_scene_change(scene_name)
        return {"status": "switched", "scene": scene_name}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/scenes/list", response_model=dict)
async def list_scenes_endpoint():
    """List all available scenes."""
    if foundry_client and foundry_client.is_connected:
        try:
            scenes = await foundry_client.get_scenes()
            return {"scenes": scenes}
        except Exception as e:
            return {"error": str(e)}
    return {"scenes": []}


@app.get("/api/scene/current", response_model=dict)
async def get_current_scene_endpoint():
    """Get current scene details."""
    if foundry_client and foundry_client.is_connected:
        try:
            scene_name = state_tracker.state.current_scene or ""
            details = await foundry_client.get_scene_details(scene_name)
            tokens = await foundry_client.get_scene_tokens(scene_name)
            return {"name": scene_name, "details": details, "tokens": tokens}
        except Exception as e:
            return {"error": str(e)}
    return {"name": ""}


@app.get("/api/npc_context", response_model=dict)
async def get_npc_context_endpoint():
    """Get current NPC context for debugging."""
    if state_tracker:
        return {"context": state_tracker.state.npc_context}
    return {"context": ""}


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
    vault_files: List[str] = []


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


# --- Campaign Wizard Endpoints ---

@app.post("/api/campaign/scan", response_model=CampaignScanResponse)
async def scan_world_endpoint(request: CampaignScanRequest):
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
    global foundry_client

    if not foundry_client or not foundry_client.is_connected:
        return CampaignScanResponse(
            status="error",
            scan_id=f"scan-{uuid.uuid4().hex[:8]}",
            error="Not connected to FoundryVTT",
        )

    try:
        logger.info(f"Scanning FoundryVTT world: {request.world_name or 'unknown'}")

        # Step 1: Run full world scan
        scan_data = await foundry_client.scan_world()

        # Step 2: Analyze capabilities from scan
        capabilities = await foundry_client.discover_addon_capabilities(scan_data)

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
async def build_campaign_endpoint(request: CampaignBuildRequest):
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
        from campaign.orchestrator import build_campaign
        from campaign.map_generator import MapGenerator

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

        orch = CampaignOrchestrator()

        result = await orch.build_campaign(
            prompt=full_prompt,
            campaign_name=request.name,
            llm_client=llm_client,
            foundry_client=foundry_client if foundry_client and foundry_client.is_connected else None,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=getattr(settings, "omlx_base_url", None) or getattr(settings, "omlx_url", None),
            omlx_model=getattr(settings, "omlx_model", "Z-Image-Turbo"),
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=None,
        )

        # Map orchestrator result to our response model
        return CampaignBuildResponse(
            status=result.get("status", "error"),
            campaign_id=result.get("campaign_id", f"campaign-{uuid.uuid4().hex[:8]}"),
            campaign_name=request.name,
            steps_completed=result.get("steps_completed", []),
            scan_data=result.get("scan_data"),
            generated_data=result.get("generated_data"),
            maps_generated=result.get("assets", {}).get("maps", []),
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


@app.post("/api/campaign/start", response_model=CampaignStartResponse)
async def start_campaign_endpoint(request: CampaignStartRequest):
    """Start (or continue) a campaign session.

    If continue_from_last is True, loads the previous session's state.
    Otherwise, creates a fresh session and loads the campaign context.
    """
    global state_tracker, chat_listener, llm_manager, db

    try:
        # Get active session
        active_session = await db.get_active_session()

        if request.continue_from_last and active_session:
            # Continue from last session
            logger.info(f"Continuing session: {active_session}")
            session_id = active_session
        else:
            # Create new session
            session_id = str(uuid.uuid4())[:8]
            await db.create_session(session_id, request.campaign_name)
            logger.info(f"Created new session: {session_id}")

        # Update state tracker
        state_tracker.set_campaign(request.campaign_name)
        state_tracker.set_mode(GameMode.exploration)
        await state_tracker.save()

        # Reset message ID for clean conversation
        if foundry_client:
            foundry_client.reset_message_id()

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
async def end_session_endpoint(request: SessionEndRequest):
    """End the current session prematurely.

    This generates a session summary and marks the session as ended.
    Players can use this at any time during gameplay.
    """
    global state_tracker, chat_listener, db

    try:
        # Pause the chat listener if running
        if chat_listener and chat_listener._running:
            chat_listener._running = False
            await broadcast_state_update({"type": "ai_paused", "reason": request.reason})

        # Get active session
        session_id = await db.get_active_session()
        if not session_id:
            return SessionEndResponse(
                status="no_active_session",
                session_id="",
                campaign_name="",
                message="No active session to end.",
            )

        # Get current state for summary
        state_snapshot = state_tracker.state.model_dump() if state_tracker else {}

        # Generate a brief summary using LLM if available
        summary_text = ""
        if llm_manager:
            try:
                summary_text = await llm_manager.generate(
                    user_message=f"Summarize this D&D session ending. Current state: {json.dumps(state_snapshot, default=str)}. Keep it brief (2-3 sentences) and note any important plot points, unresolved quests, or character moments.",
                )
                summary_text = json.dumps(summary_text, default=str)
            except Exception:
                summary_text = json.dumps(state_snapshot, default=str)

        # End the session
        await db.end_session(session_id)

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
            campaign_name=state_tracker.state.campaign if state_tracker else "",
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
async def list_campaigns_endpoint():
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
async def get_campaign_endpoint(campaign_name: str):
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
        return {"error": f"Campaign '{campaign_name}' not found"}
    except Exception as e:
        return {"error": str(e)}


class CampaignDeleteRequest(BaseModel):
    """Request body for deleting a campaign."""
    name: str


@app.post("/api/campaign/delete", response_model=dict)
async def delete_campaign_endpoint(request: CampaignDeleteRequest):
    """Delete a campaign from the vault."""
    try:
        from campaign.obsidian_sync import delete_campaign
        deleted = delete_campaign(request.name)
        return {"status": "deleted" if deleted else "not_found"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/comfyui/health")
async def check_comfyui_health():
    """Check if ComfyUI is available."""
    try:
        from campaign.map_generator import MapGenerator
        mg = MapGenerator(settings.comfyui_url, checkpoint_name=settings.comfyui_checkpoint)
        healthy = await mg.health_check()
        await mg.close()
        return {"healthy": healthy, "url": settings.comfyui_url}
    except Exception as e:
        return {"healthy": False, "error": str(e)}


@app.get("/api/comfyui/models")
async def list_comfyui_models():
    """List available ComfyUI models."""
    try:
        from campaign.map_generator import MapGenerator
        mg = MapGenerator(settings.comfyui_url, checkpoint_name=settings.comfyui_checkpoint)
        models = await mg.get_models()
        await mg.close()
        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/roll")
async def roll_dice(request: Request):
    """Roll dice in FoundryVTT and return the result."""
    try:
        data = await request.json()
        result = await foundry_client.roll(
            data.get("formula", "1d20"),
            speaker=data.get("speaker", "GM"),
            flavor=data.get("flavor", "")
        )
        return result or {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@app.websocket("/api/ws")
async def admin_websocket(websocket: WebSocket):
    """WebSocket endpoint for admin panel real-time updates."""
    await websocket.accept()
    websocket_clients.append(websocket)
    logger.info(f"Admin panel connected (total: {len(websocket_clients)})")

    try:
        last_msg_time = 0.0
        while True:
            # Rate limit: max 5 messages per second per connection
            import time as _time_now
            now = _time_now.time()
            if websocket in _admin_ws_rate and now - _admin_ws_rate[websocket] < 0.2:
                await websocket.send_text(json.dumps({"type": "rate_limited"}))
                continue
            _admin_ws_rate[websocket] = now

            # Read messages from admin panel (for commands)
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif msg.get("type") == "pause":
                chat_listener._running = False
                await broadcast_state_update({"type": "ai_paused"})
            elif msg.get("type") == "resume":
                chat_listener._running = True
                await broadcast_state_update({"type": "ai_resumed"})
            elif msg.get("type") == "roll_command":
                formula = msg.get("formula", "1d20")
                speaker = msg.get("speaker", "GM")
                flavor = msg.get("flavor", "")
                await foundry_client.roll(formula, speaker=speaker, flavor=flavor)
    except WebSocketDisconnect:
        logger.info("Admin panel disconnected")
    except Exception as e:
        logger.error(f"Admin WebSocket error: {e}")
    finally:
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)
        _admin_ws_rate.pop(websocket, None)


# --- Entry Point ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.admin_port,
        log_level="info"
    )
