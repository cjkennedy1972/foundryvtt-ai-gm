"""In-Foundry control surface endpoints: engine pause/resume and narration
dispatch, called from the aigm-control-panel Foundry module. Mode switching
and scene listing reuse the existing /api/state/update and /api/scenes/list
endpoints instead of duplicating them here.

Like every /api/* route, these are still gated by the admin_token check in
main.py's protect_api_resources middleware when ADMIN_TOKEN is configured.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import AppState, broadcast_state_update, get_app_state

logger = logging.getLogger("ai-gm")

router = APIRouter(prefix="/api/admin", tags=["control"])


class NarrateRequest(BaseModel):
    """Narration text to post directly to Foundry chat via the engine."""
    text: str


@router.post("/pause")
async def admin_pause(state: AppState = Depends(get_app_state)):
    """Pause the AI engine — stops processing incoming messages.

    Also pauses the Foundry game via game.togglePause() if connected.
    """
    if not state.chat_listener:
        logger.error("Admin pause: Chat listener not initialized")
        return JSONResponse(
            status_code=503,
            content={"error": "Chat listener not initialized"}
        )
    state.chat_listener._running = False
    if state.foundry_client:
        try:
            await state.foundry_client.execute_js(
                "if(!game.paused){game.togglePause(true,true);}"
            )
        except Exception as e:
            logger.error(f"Admin pause: Foundry togglePause failed: {e}", exc_info=True)
    await broadcast_state_update({"type": "ai_paused"})
    return {"status": "paused", "ai_running": False}


@router.post("/resume")
async def admin_resume(state: AppState = Depends(get_app_state)):
    """Resume the AI engine — resumes processing incoming messages."""
    if not state.chat_listener:
        logger.error("Admin resume: Chat listener not initialized")
        return JSONResponse(
            status_code=503,
            content={"error": "Chat listener not initialized"}
        )
    state.chat_listener._running = True
    if state.foundry_client:
        try:
            await state.foundry_client.execute_js(
                "if(game.paused){game.togglePause(false,true);}"
            )
        except Exception as e:
            logger.error(f"Admin resume: Foundry togglePause failed: {e}", exc_info=True)
    if state.chat_listener:
        state.chat_listener._reset_idle_timer()
    await broadcast_state_update({"type": "ai_resumed"})
    return {"status": "resumed", "ai_running": True}


@router.post("/narrate")
async def admin_narrate(req: NarrateRequest, state: AppState = Depends(get_app_state)):
    """Post narration text directly to Foundry chat.

    This bypasses the LLM and sends the text verbatim as a GM chat message.
    Used by the in-Foundry control panel's narration textarea.
    """
    if not req.text or not req.text.strip():
        logger.error("Admin narrate: Empty narration text")
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Empty narration text"}
        )
    if not state.foundry_client:
        logger.error("Admin narrate: Not connected to Foundry")
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Not connected to Foundry"}
        )
    try:
        result = await state.foundry_client.chat_message(
            text=req.text.strip(),
            speaker="GM",
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Admin narrate failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )
