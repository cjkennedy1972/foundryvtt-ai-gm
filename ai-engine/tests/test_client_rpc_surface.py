#!/usr/bin/env python3
"""The client's remaining RPC methods, against the shapes the relay returns.

Each of these was called against a live Foundry v13 world before being
written, so the envelopes here are observed rather than assumed:

    get-users / get-playlists  {"clientId", "data", "requestId", "type"}
    create-canvas-document     {"clientId", "data", "documentType",
                                "requestId", "sceneId", "type"}
    execute-js                 {"clientId", "requestId", "result",
                                "success", "type"}

Those live calls also produced the real return values these assert against —
get_actor_dispositions answered {"Probe Dummy 2": -1}, and
discover_addon_capabilities answered with real counts once scan_world was
fixed.

Run:
    cd ai-engine && python -m pytest tests/test_client_rpc_surface.py -v
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient


def _client(send=None, js=None):
    c = FoundryClient()
    c._send = AsyncMock(return_value=send if send is not None else {"data": {}})
    c._send_with_retry = AsyncMock(return_value=send if send is not None else {"data": {}})
    c.execute_js = AsyncMock(return_value=js if js is not None else {"result": None})
    return c


def run(coro):
    return asyncio.run(coro)


# ── get_users ─────────────────────────────────────────────────────────────

USERS = [{"active": True, "name": "Gamemaster", "role": 4,
          "avatar": "icons/svg/mystery-man.svg", "character": None}]


def test_users_come_out_of_the_data_envelope():
    c = _client(send={"clientId": "x", "type": "get-users-result", "data": USERS})

    assert run(c.get_users()) == USERS


def test_users_nested_under_a_users_key_are_unwrapped():
    c = _client(send={"data": {"users": USERS}})

    assert run(c.get_users()) == USERS


def test_a_bare_list_is_accepted():
    c = _client(send=USERS)

    assert run(c.get_users()) == USERS


def test_users_degrade_to_empty_on_error():
    c = FoundryClient()
    c._send = AsyncMock(side_effect=RuntimeError("relay down"))

    assert run(c.get_users()) == []


def test_an_unexpected_users_shape_yields_a_list():
    c = _client(send={"data": 42})

    assert run(c.get_users()) == []


# ── get_playlists ─────────────────────────────────────────────────────────

def test_playlists_come_out_of_data_playlists():
    lists = [{"name": "Tavern", "sounds": [{"name": "Lute", "path": "sounds/lute.ogg"}]}]
    c = _client(send={"data": {"playlists": lists}})

    assert run(c.get_playlists()) == lists


def test_an_empty_world_has_no_playlists():
    c = _client(send={"data": {"playlists": []}})

    assert run(c.get_playlists()) == []


def test_playlists_degrade_to_empty_on_a_missing_key():
    c = _client(send={"data": {}})

    assert run(c.get_playlists()) == []


def test_playlists_degrade_to_empty_on_error():
    c = FoundryClient()
    c._send = AsyncMock(side_effect=RuntimeError("relay down"))

    assert run(c.get_playlists()) == []


# ── get_actor_dispositions ────────────────────────────────────────────────

def test_dispositions_are_returned_per_actor():
    """A live world answered {"Probe Dummy 2": -1} for a hostile NPC."""
    c = _client(js={"result": {"Probe Dummy 2": -1, "Halda": 1}})

    assert run(c.get_actor_dispositions(["Probe Dummy 2", "Halda"])) == {
        "Probe Dummy 2": -1, "Halda": 1
    }


def test_asking_for_no_actors_does_not_call_foundry():
    c = _client()

    assert run(c.get_actor_dispositions([])) == {}
    assert c.execute_js.await_count == 0


def test_the_names_asked_for_reach_the_script():
    c = _client(js={"result": {}})

    run(c.get_actor_dispositions(["Elder Morwenna"]))

    assert "Elder Morwenna" in c.execute_js.await_args.args[0]


def test_dispositions_degrade_to_empty_when_the_script_fails():
    c = _client()
    c.execute_js = AsyncMock(side_effect=RuntimeError("execute_js disabled"))

    assert run(c.get_actor_dispositions(["Halda"])) == {}


# ── clear_canvas_layer ────────────────────────────────────────────────────

@pytest.mark.parametrize("layer,cls", [
    ("walls", "Wall"), ("lights", "AmbientLight"), ("sounds", "AmbientSound"),
    ("tokens", "Token"), ("tiles", "Tile"), ("drawings", "Drawing"),
    ("notes", "Note"),
])
def test_each_known_layer_maps_to_its_foundry_class(layer, cls):
    c = _client(js={"result": 3})

    run(c.clear_canvas_layer(layer))

    script = c.execute_js.await_args.args[0]
    assert json.dumps(cls) in script
    assert json.dumps(layer) in script


def test_an_unknown_layer_is_refused_rather_than_spliced_into_the_script():
    """The layer name reaches Foundry inside a JS string. An unmapped one used
    to be interpolated straight in, so an LLM- or API-supplied value would
    have executed there."""
    c = _client()

    out = run(c.clear_canvas_layer("'); dropDatabase(); ('"))

    assert out["success"] is False
    assert "Unknown canvas layer" in out["error"]
    assert c.execute_js.await_count == 0


def test_clearing_falls_back_to_canvas_delete_when_the_script_fails():
    c = _client()
    c.execute_js = AsyncMock(side_effect=RuntimeError("execute_js disabled"))

    run(c.clear_canvas_layer("lights"))

    sent = c._send.await_args
    assert sent.args[0] == "delete-canvas-document"
    assert sent.kwargs["documentType"] == "lights"


# ── canvas_delete ─────────────────────────────────────────────────────────

def test_canvas_delete_sends_the_document_type_and_class():
    c = _client()

    run(c.canvas_delete("lights"))

    kw = c._send.await_args.kwargs
    assert kw["documentType"] == "lights"
    assert kw["className"] == "AmbientLight"


def test_canvas_delete_passes_a_uuid_when_given_one():
    c = _client()

    run(c.canvas_delete("lights", uuid="Scene.a.AmbientLight.b"))

    assert c._send.await_args.kwargs["uuid"] == "Scene.a.AmbientLight.b"


def test_canvas_delete_passes_ids_when_given_them():
    c = _client()

    run(c.canvas_delete("walls", ids=["w1", "w2"]))

    assert c._send.await_args.kwargs["ids"] == ["w1", "w2"]


def test_canvas_delete_omits_the_optional_fields_when_absent():
    c = _client()

    run(c.canvas_delete("walls"))

    kw = c._send.await_args.kwargs
    assert "uuid" not in kw and "ids" not in kw


# ── configure_scene ───────────────────────────────────────────────────────

def test_configuring_a_named_scene_goes_through_update_scene():
    c = _client()

    run(c.configure_scene({"darkness": 0.3}, "The Crypt"))

    assert c._send.await_args.args[0] == "update-scene"
    assert c._send.await_args.kwargs["name"] == "The Crypt"


def test_configuring_the_active_scene_uses_the_canvas():
    c = _client(js={"result": True})

    run(c.configure_scene({"darkness": 0.4}))

    assert "canvas.scene.update" in c.execute_js.await_args.args[0]
    assert "0.4" in c.execute_js.await_args.args[0]


def test_configuring_the_active_scene_reports_a_failure_rather_than_raising():
    c = _client()
    c.execute_js = AsyncMock(side_effect=RuntimeError("execute_js disabled"))

    out = run(c.configure_scene({"darkness": 0.4}))

    assert "error" in out


# ── move_token ────────────────────────────────────────────────────────────

def test_move_token_script_compares_the_committed_position_not_just_the_update():
    """v14 leaves a vetoed token where it was and update() still resolves, so
    the script has to check _source to know whether the move happened."""
    c = _client(js={"result": {"ok": True, "x": 5, "y": 6}})

    run(c.move_token("tok1", 5, 6))

    script = c.execute_js.await_args.args[0]
    assert "tok._source.x" in script and "moved" in script


def test_a_move_foundry_refused_is_reported_not_swallowed():
    refused = {"result": {"ok": False, "error": "Foundry did not move the token"}}
    c = _client(js=refused)

    out = run(c.move_token("tok1", 5, 6))

    assert out["ok"] is False
    assert "did not move" in out["error"]


# ── get_player_actor_mapping ──────────────────────────────────────────────

def test_player_mapping_reads_v14_ownership_and_skips_gm_owners():
    """v14 made Actor#permission the current user's level (a number), so the old
    `Object.keys(a.permission)` was always empty and no player was ever mapped.
    GM users are listed as owners too; the player must be the one picked."""
    c = _client(send={"result": [{"name": "Grazen", "uuid": "Actor.g", "ownerId": "chris"}]})

    out = run(c.get_player_actor_mapping())

    script = c._send_with_retry.await_args.kwargs["script"]
    assert "a.ownership" in script and "isGM" in script
    assert "a.permission" not in script
    assert out["actor_names"] == {"Grazen": "chris"}
