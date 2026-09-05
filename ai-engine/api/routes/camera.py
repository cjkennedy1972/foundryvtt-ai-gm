"""Camera control endpoints for cinematic movements and state checks.
Called from the aigm-control-panel Foundry module (see
foundry-module/aigm-control-panel/scripts/aigm-control-panel.js). Every
response uses the module's expected shape: JSON `{ok: bool, data?: any,
error?: string}`.

All camera movement executes inside Foundry via the relay's execute-js, which
resolves the live `canvas` at call time — so a scene switch mid-flight simply
retargets the next call at the new scene instead of animating a stale one.
"""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import ApiError, AppState, get_app_state, require_foundry

logger = logging.getLogger("ai-gm")

router = APIRouter(prefix="/api/camera", tags=["camera"])


# Numeric fields reject NaN/Infinity (pydantic v2 would otherwise accept them
# and f-string them into the generated JS as invalid `nan`/`inf` tokens) and
# are range-checked so bad input fails fast with a 422 instead of reaching the
# remote JS eval. Durations are milliseconds, matching the control panel.
class PanRequest(BaseModel):
    """Absolute coordinates (pixels) for camera panning."""
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    duration: float = Field(default=1.0, ge=0, le=60_000, allow_inf_nan=False)


class PanToTokenRequest(BaseModel):
    """Pan camera to a specific token's position (Foundry token id)."""
    token_id: str
    duration: float = Field(default=1.0, ge=0, le=60_000, allow_inf_nan=False)


class ZoomRequest(BaseModel):
    """Set camera zoom level (scale factor, 1.0 = 100%)."""
    scale: float = Field(gt=0, le=10, allow_inf_nan=False)
    duration: float = Field(default=1.0, ge=0, le=60_000, allow_inf_nan=False)


class PointRequest(BaseModel):
    """Focus point (pixels) for push-in camera movement."""
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    duration: float = Field(default=1.0, ge=0, le=60_000, allow_inf_nan=False)


class DurationRequest(BaseModel):
    """Duration (ms) for return-to-default movement."""
    duration: float = Field(default=1.0, ge=0, le=60_000, allow_inf_nan=False)


@router.get("/is-player-turn")
async def camera_is_player_turn(state: AppState = Depends(get_app_state)):
    """Whether the current initiative turn belongs to a player character.

    Returns `{ok: true, data: {player_turn: bool}}`. The control panel blocks
    camera movement while it is True, so this must be True outside combat and
    whenever the active combatant is a player-owned token.
    """
    if not state.state_tracker:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "State tracker not initialized"}
        )
    try:
        game_state = state.state_tracker.state
        # Outside combat the player is free to move/interact — treat as player's turn.
        if game_state.mode != "combat":
            return {"ok": True, "data": {"player_turn": True}}

        combat = game_state.combat
        if not combat.turn_order or combat.turn <= 0:
            return {"ok": True, "data": {"player_turn": True}}

        # combat.turn is a monotonic counter — chat_listener increments it on
        # every native turn event and never wraps it — so reduce it modulo the
        # initiative order (the same indexing combat/loop.py uses). A raw
        # out-of-range check would report every NPC turn from round 2 on as
        # the player's turn.
        turn_index = (combat.turn - 1) % len(combat.turn_order)
        active_token_id = combat.turn_order[turn_index]

        require_foundry(state)

        tokens = await state.foundry_client.get_scene_tokens()
        token = next((t for t in tokens if t.get("id") == active_token_id), None)
        if not token:
            # Combatant isn't visible on the active canvas — can't prove it's an
            # NPC, so don't risk animating over player input.
            return {"ok": True, "data": {"player_turn": True}}

        mapping = await state.foundry_client.get_player_actor_mapping()
        # get_scene_tokens falls back to the bare TokenDocument actorId while
        # the mapping is keyed by full Actor uuid ("Actor.<id>") — normalize
        # both sides to bare ids, or player tokens never match and the camera
        # would move during the player's own turn.
        actor_ref = token.get("actorUuid") or ""
        actor_uuids = mapping.get("actor_uuids", {})
        player_bare_ids = {u.rsplit(".", 1)[-1] for u in actor_uuids}
        is_player = bool(actor_ref) and (
            actor_ref in actor_uuids
            or actor_ref.rsplit(".", 1)[-1] in player_bare_ids
        )
        return {"ok": True, "data": {"player_turn": is_player}}
    except ApiError:
        raise
    except Exception as e:
        logger.error(f"camera_is_player_turn failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"Internal error checking turn: {str(e)}"}
        )


# All movement goes through canvas.animatePan({x, y, scale, duration}) — the
# only animated pan/zoom API Foundry's Canvas actually exposes (there is no
# canvas.panTo / canvas.setZoom). Push-in/pull-back pass pan and zoom in one
# call so both animate together instead of restarting the animation.
@router.post("/pan")
async def camera_pan(req: PanRequest, state: AppState = Depends(get_app_state)):
    """Pan the camera to absolute canvas coordinates (x, y)."""
    require_foundry(state)
    js = (
        "if(!canvas?.scene) return {ok:false, error:'no active scene'};"
        f"await canvas.animatePan({{x: {req.x}, y: {req.y}, duration: {req.duration}}});"
        "return {ok:true};"
    )
    return await _run_camera_js(state, js)


@router.post("/pan-to-token")
async def camera_pan_to_token(req: PanToTokenRequest, state: AppState = Depends(get_app_state)):
    """Pan the camera to a token's position on the current scene.

    The token must exist on the active canvas; panning to a token that is not
    in the current scene is rejected.
    """
    require_foundry(state)
    token_id = json.dumps(req.token_id)
    js = (
        "if(!canvas?.scene) return {ok:false, error:'no active scene'};"
        f"const want={token_id};"
        "const tok=canvas.tokens.get(want);"
        "if(!tok) return {ok:false, error:'Token '+want+' not found in current scene'};"
        f"await canvas.animatePan({{x: tok.center.x, y: tok.center.y, duration: {req.duration}}});"
        "return {ok:true};"
    )
    return await _run_camera_js(state, js)


@router.post("/zoom")
async def camera_zoom(req: ZoomRequest, state: AppState = Depends(get_app_state)):
    """Set the camera zoom level (scale factor, 1.0 = 100%)."""
    require_foundry(state)
    js = (
        "if(!canvas?.scene) return {ok:false, error:'no active scene'};"
        f"await canvas.animatePan({{scale: {req.scale}, duration: {req.duration}}});"
        "return {ok:true};"
    )
    return await _run_camera_js(state, js)


@router.post("/push-in")
async def camera_push_in(req: PointRequest, state: AppState = Depends(get_app_state)):
    """Push the camera in on a focus point: pan to (x, y) and zoom closer."""
    require_foundry(state)
    js = (
        "if(!canvas?.scene) return {ok:false, error:'no active scene'};"
        f"await canvas.animatePan({{x: {req.x}, y: {req.y}, scale: 1.5, duration: {req.duration}}});"
        "return {ok:true};"
    )
    return await _run_camera_js(state, js)


@router.post("/pull-back")
async def camera_pull_back(req: DurationRequest, state: AppState = Depends(get_app_state)):
    """Return the camera to the default view: zoom 1.0 centered on the scene."""
    require_foundry(state)
    js = (
        "if(!canvas?.scene) return {ok:false, error:'no active scene'};"
        "const c=canvas.scene.center;"
        f"await canvas.animatePan({{x: c.x, y: c.y, scale: 1.0, duration: {req.duration}}});"
        "return {ok:true};"
    )
    return await _run_camera_js(state, js)


async def _run_camera_js(state: AppState, js: str):
    """Execute camera JS in Foundry and normalize the result to {ok, error}.

    The relay surfaces the JS expression value under ``result``, so unwrap
    that before reading `ok`/`error`. Any exception (relay down, script error)
    is surfaced as a descriptive JSON error response, never a rejection.
    """
    try:
        res = await state.foundry_client.execute_js(js, _timeout=2.0)
        payload = res.get("result") if isinstance(res, dict) else None
        if isinstance(payload, dict) and payload.get("ok"):
            return {"ok": True}
        error = (
            (payload or {}).get("error")
            if isinstance(payload, dict)
            else "Unknown Foundry error"
        )
        return {"ok": False, "error": error or "Camera operation failed"}
    except Exception as e:
        logger.warning(f"Camera operation failed: {e}")
        return {"ok": False, "error": str(e)}