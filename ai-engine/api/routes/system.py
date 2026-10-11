"""System/admin endpoints: status, health, context reinforcement, ComfyUI."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from api.deps import internal_error, AppState, ErrorResponse, get_app_state
from config import settings

logger = logging.getLogger("ai-gm")

router = APIRouter(tags=["system"])


@router.get("/")
async def admin_redirect(state: AppState = Depends(get_app_state)):

    """Redirect to admin panel."""
    return RedirectResponse(url="/admin/index.html")


# --- Admin API Endpoints ---

@router.get("/api/status")
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
    }


@router.get("/health")
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


@router.get("/ready")
async def readiness_check(state: AppState = Depends(get_app_state)):
    """Readiness probe: report whether core services can accept work."""
    checks = {
        "database": state.db is not None,
        "llm": state.llm_manager is not None,
        "foundry": state.foundry_client is not None and state.foundry_client.is_connected,
    }
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail={"status": "not_ready", "components": checks})
    return {"status": "ready", "components": checks}


@router.get("/api/context/reinforcement")
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


@router.post("/api/context/reinforce")
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
                error=f"Reinforcement failed ({type(e).__name__})",
                code="REINFORCEMENT_FAILED"
            ).model_dump()
        )


@router.post("/api/context/summarize")
async def trigger_summarization(state: AppState = Depends(get_app_state)):

    """Compact the active session's raw log into campaign memory now."""
    memory = state.campaign_memory
    session_info = await state.db.get_active_session_info() if state.db else None
    if not (memory and session_info):
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Campaign memory or active session not available",
                code="MEMORY_NOT_READY"
            ).model_dump()
        )
    try:
        written = await memory.maybe_compact(
            session_info.get("campaign") or "", session_info["session_id"], include_partial=True, force=True,
        )
        return {"status": "ok", "nodes_written": written}
    except Exception as e:
        logger.error(f"Summarization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Summarization failed ({type(e).__name__})",
                code="SUMMARIZATION_FAILED"
            ).model_dump()
        )


@router.post("/api/memory/rebuild")
async def rebuild_memory(campaign: str, state: AppState = Depends(get_app_state)):

    """Drop a campaign's derived memory and recompact it from the raw log."""
    if not state.campaign_memory:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error", error="Campaign memory not available", code="MEMORY_NOT_READY"
            ).model_dump()
        )
    try:
        nodes = await state.campaign_memory.rebuild(campaign)
    except Exception as e:
        return internal_error("Memory rebuild failed", e)
    return {"status": "ok", "campaign": campaign, "nodes": nodes}


@router.post("/api/context/world_summary")
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
                error=f"World summary update failed ({type(e).__name__})",
                code="WORLD_SUMMARY_UPDATE_FAILED"
            ).model_dump()
        )


@router.get("/api/comfyui/health")
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
                error=f"ComfyUI health check failed ({type(e).__name__})",
                code="COMFYUI_HEALTH_CHECK_FAILED"
            ).model_dump()
        )


@router.get("/api/comfyui/models")
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
                error=f"Failed to list ComfyUI models ({type(e).__name__})",
                code="COMFYUI_MODELS_FAILED"
            ).model_dump()
        )
