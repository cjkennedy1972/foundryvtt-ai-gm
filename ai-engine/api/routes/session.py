"""Session/settings/chat endpoints: state, settings, sessions, GM chat, dice, execute_js."""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import AppState, ErrorResponse, get_app_state
from config import settings
from state.models import GameMode

logger = logging.getLogger("ai-gm")

router = APIRouter(tags=["session"])


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
    # None means the caller did not request a budget change. A default of
    # settings.llm_token_budget would restore the startup value on every
    # unrelated settings update, discarding runtime budget changes.
    llm_token_budget: Optional[int] = None


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


class ChatTestRequest(BaseModel):
    message: str
    speaker: str = "Selmor"


class GMChatRequest(BaseModel):
    message: str


@router.get("/api/state")
async def get_state(state: AppState = Depends(get_app_state)):

    """Get full game state."""
    if state.state_tracker:
        return state.state_tracker.state.model_dump()
    return {}


@router.get("/api/settings", response_model=GMSettings)
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
        llm_token_budget=settings.llm_token_budget,
    )


@router.post("/api/settings", response_model=GMSettings)
async def update_settings(settings_data: GMSettings, state: AppState = Depends(get_app_state)):

    """Update AI GM settings (runtime-only, not persisted to disk).

    Settings changes apply to the running instance only and are lost on restart.
    To persist settings, modify the .env file directly.
    Note: LLM base_url/api_key changes require a restart to take effect
    (they are rejected here with 400) — those rebuild the HTTP client itself.
    Model is just a request parameter LLMManager reads per-call, so it applies
    immediately with no restart.
    """
    # Critical settings that require LLMManager recreation — reject at runtime
    critical_fields = ["llm_base_url", "llm_api_key", "relay_url", "relay_ws_url"]
    for field in critical_fields:
        if getattr(settings_data, field, None) and getattr(settings_data, field) != getattr(settings, field):
            raise HTTPException(
                status_code=400,
                detail=f"Changing '{field}' requires a server restart. Update .env and restart the engine."
            )

    if state.llm_manager:
        state.llm_manager._temperature = settings_data.temperature
        state.llm_manager._ai_tone = settings_data.ai_tone
    if state.foundry_client:
        state.foundry_client.set_ai_name(settings_data.ai_name)

    # Apply non-secret runtime changes
    if settings_data.model:
        settings.model = settings_data.model
        if state.llm_manager:
            state.llm_manager.model = settings_data.model
    if settings_data.comfyui_url:
        settings.comfyui_url = settings_data.comfyui_url
    if settings_data.ai_tone:
        settings.ai_tone = settings_data.ai_tone
        if state.llm_manager:
            state.llm_manager._ai_tone = settings_data.ai_tone
    if settings_data.ai_name:
        settings.ai_name = settings_data.ai_name
    if settings_data.temperature is not None:
        settings.temperature = settings_data.temperature
    if settings_data.llm_token_budget is not None and settings_data.llm_token_budget >= 0:
        settings.llm_token_budget = settings_data.llm_token_budget
        if state.token_usage:
            state.token_usage.budget = settings_data.llm_token_budget
    if settings_data.relay_url:
        settings.relay_url = settings_data.relay_url

    return settings_data


@router.post("/api/state/update", response_model=dict)
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


@router.get("/api/session/active")
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


@router.get("/api/session/usage")
async def get_active_session_usage(state: AppState = Depends(get_app_state)):
    """Return durable token spend for the active session."""
    info = await state.db.get_active_session_info()
    if not info:
        return {"session_id": None, "campaign": "", "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0, "calls": []}
    result = await state.db.get_llm_usage(session_id=info["session_id"])
    result.update({"session_id": info["session_id"], "campaign": info["campaign"] or "",
                   "budget": settings.llm_token_budget})
    return result


@router.get("/api/usage/session/{session_id}")
async def get_session_usage(session_id: str, state: AppState = Depends(get_app_state)):
    result = await state.db.get_llm_usage(session_id=session_id)
    result.update({"session_id": session_id, "budget": settings.llm_token_budget})
    return result


@router.get("/api/usage/campaign/{campaign}")
async def get_campaign_usage(campaign: str, state: AppState = Depends(get_app_state)):
    result = await state.db.get_llm_usage(campaign=campaign)
    result.update({"campaign": campaign})
    return result


@router.post("/api/session/new", response_model=SessionInfo)
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


@router.get("/api/session/events", response_model=List[EventEntry])
async def get_session_events(limit: int = 50, state: AppState = Depends(get_app_state)):

    """Get session event history."""
    session_id = await state.db.get_active_session()
    if session_id:
        events = await state.db.get_events(session_id, limit)
        return events
    return []


@router.post("/api/chat/test")
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


@router.post("/api/chat/gm")
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


@router.post("/api/foundry/js", response_model=dict)
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


@router.post("/api/roll")
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
