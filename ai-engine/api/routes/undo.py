"""Undo endpoints: what the AI's last reversible actions were, and take the newest back."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import AppState, ErrorResponse, get_app_state

router = APIRouter(tags=["undo"])


def _dispatcher(state: AppState):
    return state.action_dispatcher


@router.get("/api/undo")
async def list_undoable(state: AppState = Depends(get_app_state)):
    """Recent undoable actions, newest first. The first one is what POST /api/undo reverses."""
    dispatcher = _dispatcher(state)
    return {"actions": dispatcher.undo.recent() if dispatcher else []}


@router.post("/api/undo")
async def undo_last_action(state: AppState = Depends(get_app_state)):
    """Reverse the AI's most recent HP change, token move, condition or exhaustion change."""
    dispatcher = _dispatcher(state)
    if dispatcher is None:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(error="The engine is still starting up.", code="NOT_READY").model_dump(),
        )
    result = await dispatcher.undo_last()
    if not result["success"]:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(error=result["error"], code="UNDO_FAILED").model_dump(),
        )
    return {"status": "ok", "undone": result["label"]}
