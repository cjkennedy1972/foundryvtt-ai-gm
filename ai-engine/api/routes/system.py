"""System/admin endpoints: status, relay control, health, context reinforcement, ComfyUI."""

import logging

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from api.deps import AppState, ErrorResponse, get_app_state
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
        "relay": {
            **(state.relay_manager.status() if state.relay_manager else {"managed": False, "running": False}),
            "headless_client_id": settings.relay_headless_client_id or None,
        },
    }


@router.get("/api/relay/status")
async def relay_status(state: AppState = Depends(get_app_state)):
    """Get relay service status."""
    if not state.relay_manager:
        return {"managed": False, "running": False, "error": "No relay manager"}
    return state.relay_manager.status()


@router.get("/api/relay/logs")
async def relay_logs(lines: int = 200, state: AppState = Depends(get_app_state)):
    """Return the last N lines from the relay log file."""
    lines = max(1, min(lines, 1000))
    if not state.relay_manager:
        return JSONResponse({"error": "No relay manager"}, status_code=503)
    log_path = state.relay_manager.data_dir / "relay.log"
    if not log_path.exists():
        return {"lines": [], "path": str(log_path), "error": "Log file not found"}
    try:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
        safe_lines = []
        for line in all_lines[-lines:]:
            # Do not send common credential-bearing fields to LAN clients.
            for marker in ("password", "api_key", "apikey", "authorization", "token"):
                if marker in line.lower():
                    line = f"[redacted line containing {marker}]\n"
                    break
            safe_lines.append(line)
        return {"lines": safe_lines, "total": len(all_lines)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/relay/start")
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


@router.post("/api/relay/stop")
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


@router.post("/api/relay/restart")
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


@router.get("/api/relay/interactive-sessions")
async def relay_interactive_sessions(state: AppState = Depends(get_app_state)):
    """Proxy the relay's admin interactive-sessions list (live Foundry pairings)."""
    if not state.relay_manager:
        return JSONResponse({"error": "No relay manager"}, status_code=503)
    try:
        creds = state.relay_manager.admin_credentials()
        async with httpx.AsyncClient(timeout=10) as client:
            login_resp = await client.post(
                f"{settings.relay_url}/admin/auth/login",
                json={"email": creds["email"], "password": creds["password"]},
            )
            if login_resp.status_code != 200:
                return JSONResponse(
                    {"error": f"Relay admin login failed ({login_resp.status_code})"}, status_code=502
                )
            resp = await client.get(f"{settings.relay_url}/admin/api/interactive-sessions")
        if resp.status_code != 200:
            return JSONResponse({"error": f"Relay returned {resp.status_code}"}, status_code=502)
        return resp.json()
    except Exception as e:
        logger.exception("Failed to fetch interactive sessions from relay")
        return JSONResponse({"error": str(e)}, status_code=500)


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
                error=f"Reinforcement failed: {str(e)}",
                code="REINFORCEMENT_FAILED"
            ).model_dump()
        )


@router.post("/api/context/summarize")
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
                error=f"World summary update failed: {str(e)}",
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
                error=f"ComfyUI health check failed: {str(e)}",
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
                error=f"Failed to list ComfyUI models: {str(e)}",
                code="COMFYUI_MODELS_FAILED"
            ).model_dump()
        )
