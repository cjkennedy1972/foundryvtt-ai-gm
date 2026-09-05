"""Tests for the camera control API endpoints (api/routes/camera.py).

Calls the async route functions directly against a mocked AppState, matching
the direct-call convention in test_canon_routes.py (no FastAPI TestClient in
this codebase).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.deps import ApiError
from api.routes.camera import (
    DurationRequest,
    PanRequest,
    PanToTokenRequest,
    PointRequest,
    ZoomRequest,
    camera_is_player_turn,
    camera_pan,
    camera_pan_to_token,
    camera_pull_back,
    camera_push_in,
    camera_zoom,
)


def _make_state(*, mode="explore", turn=0, turn_order=None, connected=True,
                tokens=None, mapping=None):
    client = SimpleNamespace(
        is_connected=connected,
        execute_js=AsyncMock(return_value={"result": {"ok": True}}),
        get_scene_tokens=AsyncMock(return_value=tokens or []),
        get_player_actor_mapping=AsyncMock(
            return_value=mapping or {"actor_names": {}, "actor_uuids": {}}
        ),
    )
    tracker = SimpleNamespace(
        state=SimpleNamespace(
            mode=mode,
            combat=SimpleNamespace(turn=turn, turn_order=turn_order or []),
        )
    )
    return SimpleNamespace(state_tracker=tracker, foundry_client=client)


def _executed_js(state) -> str:
    return state.foundry_client.execute_js.await_args.args[0]


# --- Movement endpoints use Foundry's real canvas.animatePan API -------------

def test_pan_uses_animate_pan_not_nonexistent_pan_to():
    state = _make_state()

    result = asyncio.run(camera_pan(PanRequest(x=100.0, y=200.0, duration=500.0), state))

    assert result == {"ok": True}
    js = _executed_js(state)
    assert "canvas.animatePan(" in js
    assert "panTo" not in js
    assert "setZoom" not in js
    assert "x: 100.0" in js and "y: 200.0" in js and "duration: 500.0" in js


def test_pan_to_token_validates_token_and_pans_to_its_center():
    state = _make_state()

    result = asyncio.run(
        camera_pan_to_token(PanToTokenRequest(token_id="tok-1", duration=250.0), state)
    )

    assert result == {"ok": True}
    js = _executed_js(state)
    assert "canvas.tokens.get(" in js
    assert "not found in current scene" in js
    assert "canvas.animatePan({x: tok.center.x, y: tok.center.y, duration: 250.0})" in js
    assert "panTo" not in js


def test_zoom_uses_animate_pan_with_scale():
    state = _make_state()

    result = asyncio.run(camera_zoom(ZoomRequest(scale=2.0, duration=500.0), state))

    assert result == {"ok": True}
    js = _executed_js(state)
    assert "canvas.animatePan({scale: 2.0, duration: 500.0})" in js
    assert "setZoom" not in js


def test_push_in_combines_pan_and_zoom_in_single_animate_pan():
    state = _make_state()

    result = asyncio.run(
        camera_push_in(PointRequest(x=10.0, y=20.0, duration=500.0), state)
    )

    assert result == {"ok": True}
    js = _executed_js(state)
    assert js.count("canvas.animatePan(") == 1
    assert "x: 10.0" in js and "y: 20.0" in js and "scale: 1.5" in js


def test_pull_back_combines_scene_center_and_zoom_reset():
    state = _make_state()

    result = asyncio.run(camera_pull_back(DurationRequest(duration=500.0), state))

    assert result == {"ok": True}
    js = _executed_js(state)
    assert js.count("canvas.animatePan(") == 1
    assert "canvas.scene.center" in js
    assert "scale: 1.0" in js


# --- Movement endpoints fail fast (503) when the relay is disconnected -------

@pytest.mark.parametrize(
    "handler,req",
    [
        (camera_pan, PanRequest(x=1.0, y=2.0)),
        (camera_pan_to_token, PanToTokenRequest(token_id="t")),
        (camera_zoom, ZoomRequest(scale=1.5)),
        (camera_push_in, PointRequest(x=1.0, y=2.0)),
        (camera_pull_back, DurationRequest()),
    ],
)
def test_movement_endpoints_raise_503_when_relay_disconnected(handler, req):
    # foundry_client is never nulled on a relay drop — only is_connected flips —
    # so a truthiness-only check would stall on reconnect instead of failing fast.
    state = _make_state(connected=False)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(handler(req, state))
    assert exc_info.value.status == 503


def test_movement_endpoints_raise_503_without_foundry_client():
    state = _make_state()
    state.foundry_client = None

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(camera_pan(PanRequest(x=1.0, y=2.0), state))
    assert exc_info.value.status == 503


# --- is-player-turn: monotonic turn counter + actor id namespaces ------------

def test_is_player_turn_outside_combat_is_true_without_foundry():
    state = _make_state(mode="explore")
    state.foundry_client = None

    result = asyncio.run(camera_is_player_turn(state))

    assert result == {"ok": True, "data": {"player_turn": True}}


def test_is_player_turn_matches_player_actor_across_id_namespaces():
    # Token carries the bare TokenDocument actorId; the mapping is keyed by
    # full Actor uuid — the two must still resolve to the same actor.
    state = _make_state(
        mode="combat",
        turn=1,
        turn_order=["pc-tok", "npc-tok"],
        tokens=[{"id": "pc-tok", "actorUuid": "PcOne123"}],
        mapping={"actor_names": {}, "actor_uuids": {"Actor.PcOne123": "user-1"}},
    )

    result = asyncio.run(camera_is_player_turn(state))

    assert result == {"ok": True, "data": {"player_turn": True}}


def test_is_player_turn_wraps_monotonic_turn_counter_per_round():
    # chat_listener increments combat.turn forever (never wraps). turn=4 in a
    # 2-slot order is round 2, second combatant — the NPC — so the camera must
    # stay blocked (player_turn False), not default to True as "ambiguous".
    state = _make_state(
        mode="combat",
        turn=4,
        turn_order=["pc-tok", "npc-tok"],
        tokens=[
            {"id": "pc-tok", "actorUuid": "PcOne123"},
            {"id": "npc-tok", "actorUuid": "Npc999"},
        ],
        mapping={"actor_names": {}, "actor_uuids": {"Actor.PcOne123": "user-1"}},
    )

    result = asyncio.run(camera_is_player_turn(state))

    assert result == {"ok": True, "data": {"player_turn": False}}


def test_is_player_turn_wraps_back_to_first_combatant_on_new_round():
    state = _make_state(
        mode="combat",
        turn=3,
        turn_order=["pc-tok", "npc-tok"],
        tokens=[{"id": "pc-tok", "actorUuid": "PcOne123"}],
        mapping={"actor_names": {}, "actor_uuids": {"Actor.PcOne123": "user-1"}},
    )

    result = asyncio.run(camera_is_player_turn(state))

    assert result == {"ok": True, "data": {"player_turn": True}}


def test_is_player_turn_defaults_safe_when_combatant_not_on_canvas():
    state = _make_state(
        mode="combat", turn=1, turn_order=["ghost-tok"], tokens=[]
    )

    result = asyncio.run(camera_is_player_turn(state))

    assert result == {"ok": True, "data": {"player_turn": True}}


# --- Request models reject non-finite / out-of-range input -------------------

def test_request_models_reject_nan_and_infinity():
    with pytest.raises(ValidationError):
        PanRequest(x=float("nan"), y=0.0)
    with pytest.raises(ValidationError):
        PanRequest(x=0.0, y=float("-inf"))
    with pytest.raises(ValidationError):
        ZoomRequest(scale=float("inf"))
    with pytest.raises(ValidationError):
        DurationRequest(duration=float("nan"))


def test_request_models_reject_out_of_range_values():
    with pytest.raises(ValidationError):
        ZoomRequest(scale=0.0)
    with pytest.raises(ValidationError):
        ZoomRequest(scale=11.0)
    with pytest.raises(ValidationError):
        PanRequest(x=0.0, y=0.0, duration=-1.0)
    with pytest.raises(ValidationError):
        DurationRequest(duration=60_001.0)
