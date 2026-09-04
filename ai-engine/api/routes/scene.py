"""Scene management endpoints: switch, list, current, background."""

import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import AppState, ErrorResponse, get_app_state

router = APIRouter(tags=["scene"])


@router.post("/api/scene/background", response_model=dict)
async def set_scene_background_endpoint(scene_name: str = "", background_src: str = "", state: AppState = Depends(get_app_state)):
    """Set the background image for a scene (by name, or active scene if omitted)."""
    if not state.foundry_client:
        return JSONResponse(status_code=503, content={"error": "Not connected to Foundry"})
    try:
        if scene_name:
            js = f"const s=game.scenes.getName({json.dumps(scene_name)});if(s){{await s.update({{background:{{src:{json.dumps(background_src)}}}}});return 'ok'}}return 'not found'"
        else:
            js = f"await canvas.scene.update({{background:{{src:{json.dumps(background_src)}}}}});return 'ok'"
        result = await state.foundry_client.execute_js(js)
        return {"status": "ok", "result": result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/api/scene/switch", response_model=dict)
async def switch_scene_endpoint(scene_name: str = "", state: AppState = Depends(get_app_state)):

    """Switch to a different scene."""
    if not state.foundry_client:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Not connected to Foundry",
                code="FOUNDRY_NOT_CONNECTED"
            ).model_dump()
        )
    try:
        await state.foundry_client.set_active_scene(scene_name)
        if state.scene_awareness:
            await state.scene_awareness.on_scene_change(scene_name)
        return {"status": "switched", "scene": scene_name}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Failed to switch scene: {str(e)}",
                code="SCENE_SWITCH_FAILED"
            ).model_dump()
        )


@router.get("/api/scenes/list", response_model=dict)
async def list_scenes_endpoint(state: AppState = Depends(get_app_state)):

    """List all available scenes."""
    if state.foundry_client and state.foundry_client.is_connected:
        try:
            scenes = await state.foundry_client.get_scenes()
            return {"scenes": scenes}
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    status="error",
                    error=f"Failed to list scenes: {str(e)}",
                    code="SCENE_LIST_FAILED"
                ).model_dump()
            )
    return {"scenes": []}


@router.get("/api/scene/current", response_model=dict)
async def get_current_scene_endpoint(state: AppState = Depends(get_app_state)):

    """Get current scene details."""
    if state.foundry_client and state.foundry_client.is_connected:
        try:
            scene_name = state.state_tracker.state.current_scene or ""
            details = await state.foundry_client.get_scene_details(scene_name)
            tokens = await state.foundry_client.get_scene_tokens(scene_name)
            return {"name": scene_name, "details": details, "tokens": tokens}
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    status="error",
                    error=f"Failed to get scene details: {str(e)}",
                    code="SCENE_DETAILS_FAILED"
                ).model_dump()
            )
    return {"name": ""}


@router.get("/api/scene/spatial-context", response_model=dict)
async def get_spatial_context_endpoint(scene_name: str = "", state: AppState = Depends(get_app_state)):
    """Get spatial context (tokens, positions, distances) for the narration panel.

    Returns token positions, relative distances, and spatial relationships for
    display in the narration UI. If scene_name is omitted, uses current scene.
    """
    if not state.foundry_client or not state.foundry_client.is_connected:
        return JSONResponse(
            status_code=503,
            content={"error": "Not connected to Foundry", "tokens": []}
        )

    try:
        if not scene_name:
            scene_name = state.state_tracker.state.current_scene or ""

        if not scene_name:
            return {"tokens": [], "error": "No active scene"}

        # Get tokens and details from scene
        tokens = await state.foundry_client.get_scene_tokens(scene_name)
        details = await state.foundry_client.get_scene_details(scene_name)

        grid_size = float(details.get("grid", 64) or 64)

        # Transform tokens into UI-friendly spatial data
        spatial_tokens = []
        for token in tokens:
            spatial_tokens.append({
                "id": token.get("id", ""),
                "name": token.get("name", "Unknown"),
                "x": token.get("x", 0),
                "y": token.get("y", 0),
                "width": token.get("width", 1),
                "height": token.get("height", 1),
                "disposition": token.get("disposition", 0),  # -1 hostile, 0 neutral, 1+ friendly
                "hidden": token.get("hidden", False),
            })

        return {
            "scene": scene_name,
            "tokens": spatial_tokens,
            "grid_size": grid_size,
            "error": None
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Failed to get spatial context: {str(e)}",
                "tokens": []
            }
        )
