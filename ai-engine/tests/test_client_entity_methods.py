#!/usr/bin/env python3
"""place_token, update_actor and the small entity RPCs.

Each behaviour asserted here was observed against a live Foundry v13 world
first, including the two not-found paths, which answer differently:

    place_token(known)     {"moved": True, "token_id": "iP9...", "actor": "..."}
    place_token(unknown)   {"error": "Actor 'No Such Actor' not found"}
    update_actor(known)    {"clientId": ..., "entity": [{...}]}
    update_actor(unknown)  None
    get_rooms              []
    contested_check        {"initiatorName", "initiatorRoll", "initiatorSuccess", ...}

update_actor returning None rather than an error dict is pinned rather than
changed: it is falsy, callers branch on truthiness, and tightening the
return type is a change to a contract this is only here to document.

Run:
    cd ai-engine && python -m pytest tests/test_client_entity_methods.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient

ACTOR = {"name": "Probe Dummy", "uuid": "Actor.AY2EL1oqkk2K7ZHj"}


def run(coro):
    return asyncio.run(coro)


def _client(**attrs):
    c = FoundryClient()
    c._send = AsyncMock(return_value={"data": {}})
    c._send_with_retry = AsyncMock(return_value={"data": {}})
    c.execute_js = AsyncMock(return_value={"result": None})
    for k, v in attrs.items():
        setattr(c, k, v)
    return c


# ── place_token ───────────────────────────────────────────────────────────

def test_an_unknown_actor_is_reported_not_placed():
    c = _client(get_actors=AsyncMock(return_value=[ACTOR]))

    out = run(c.place_token("No Such Actor", x=1, y=1))

    assert "error" in out and "No Such Actor" in out["error"]


def test_an_actor_that_already_has_a_token_is_moved_not_duplicated():
    """Placing twice must not leave two of the same NPC on the scene."""
    c = _client(
        get_actors=AsyncMock(return_value=[ACTOR]),
        get_scene_tokens=AsyncMock(return_value=[
            {"id": "iP9HcDMHQpKQoEsh", "actorUuid": ACTOR["uuid"]}]),
        move_token=AsyncMock(return_value={"success": True}),
    )

    out = run(c.place_token("Probe Dummy", x=700, y=700, disposition=-1))

    assert out == {"moved": True, "token_id": "iP9HcDMHQpKQoEsh", "actor": "Probe Dummy"}
    assert c.move_token.await_count == 1


def test_a_uuid_resolves_the_actor_without_a_name():
    """The model usually has the uuid and no name."""
    c = _client(
        get_actors=AsyncMock(return_value=[ACTOR]),
        get_scene_tokens=AsyncMock(return_value=[
            {"id": "t1", "actorUuid": ACTOR["uuid"]}]),
        move_token=AsyncMock(return_value={"success": True}),
    )

    out = run(c.place_token(uuid=ACTOR["uuid"], x=5, y=5))

    assert "error" not in out
    assert out["token_id"] == "t1"


def test_a_short_id_resolves_the_same_actor():
    """Foundry uuids are Actor.<id>; the model often passes just the id."""
    c = _client(
        get_actors=AsyncMock(return_value=[ACTOR]),
        get_scene_tokens=AsyncMock(return_value=[
            {"id": "t1", "actorUuid": ACTOR["uuid"]}]),
        move_token=AsyncMock(return_value={"success": True}),
    )

    out = run(c.place_token(uuid="AY2EL1oqkk2K7ZHj", x=5, y=5))

    assert "error" not in out


def test_a_failed_move_is_reported_with_the_token_it_tried():
    c = _client(
        get_actors=AsyncMock(return_value=[ACTOR]),
        get_scene_tokens=AsyncMock(return_value=[
            {"id": "t1", "actorUuid": ACTOR["uuid"]}]),
        move_token=AsyncMock(return_value={"error": "token locked"}),
    )

    out = run(c.place_token("Probe Dummy", x=5, y=5))

    assert "error" in out and out["token_id"] == "t1"


# ── update_actor ──────────────────────────────────────────────────────────

def test_updating_an_unknown_actor_returns_nothing():
    c = _client(get_scenes=AsyncMock(return_value=[]),
                get_scene_tokens=AsyncMock(return_value=[]),
                get_actors=AsyncMock(return_value=[]))
    c._send_with_retry = AsyncMock(return_value={"data": []})

    assert run(c.update_actor("No Such Actor", {"name": "x"})) is None


def test_an_actor_found_on_a_scene_token_is_updated_by_uuid():
    c = _client(
        get_scenes=AsyncMock(return_value=[{"name": "test"}]),
        get_scene_tokens=AsyncMock(return_value=[
            {"name": "Probe Dummy", "actorUuid": ACTOR["uuid"]}]),
    )

    run(c.update_actor("Probe Dummy", {"system": {"attributes": {"hp": {"value": 25}}}}))

    assert c._send.await_args.args[0] == "update"
    assert c._send.await_args.kwargs["uuid"] == ACTOR["uuid"]


# ── update_entity ─────────────────────────────────────────────────────────

def test_update_entity_sends_only_the_fields_it_was_given():
    c = _client()

    run(c.update_entity(uuid=ACTOR["uuid"], data={"name": "Renamed"}))

    kw = c._send.await_args.kwargs
    assert kw == {"uuid": ACTOR["uuid"], "data": {"name": "Renamed"}}


def test_update_entity_can_target_a_token():
    c = _client()

    run(c.update_entity(token_id="tok1", data={"hidden": True}))

    assert c._send.await_args.kwargs["token_id"] == "tok1"
    assert "uuid" not in c._send.await_args.kwargs


# ── canvas_update ─────────────────────────────────────────────────────────

def test_canvas_update_sends_the_class_for_the_layer():
    c = _client()

    run(c.canvas_update("lights", {"config": {"bright": 30}}))

    kw = c._send.await_args.kwargs
    assert c._send.await_args.args[0] == "update-canvas-document"
    assert kw["documentType"] == "lights"
    assert kw["className"] == "AmbientLight"
    assert kw["data"] == {"config": {"bright": 30}}


def test_canvas_update_passes_a_uuid_when_given_one():
    c = _client()

    run(c.canvas_update("walls", {"move": 0}, uuid="Scene.a.Wall.b"))

    assert c._send.await_args.kwargs["uuid"] == "Scene.a.Wall.b"


# ── get_rooms ─────────────────────────────────────────────────────────────

def test_rooms_come_from_the_structure_request():
    c = _client()
    c._send = AsyncMock(return_value={"data": {"rooms": [{"name": "Hall"}]}})

    assert run(c.get_rooms()) == [{"name": "Hall"}]


def test_an_empty_world_has_no_rooms():
    c = _client()
    c._send = AsyncMock(return_value={"data": {}})

    assert run(c.get_rooms()) == []


def test_rooms_degrade_to_empty_on_error():
    c = _client()
    c._send = AsyncMock(side_effect=RuntimeError("relay down"))

    assert run(c.get_rooms()) == []


# ── saving throws ─────────────────────────────────────────────────────────

def test_a_saving_throw_abbreviates_the_ability():
    c = _client()

    run(c.request_saving_throw(ACTOR["uuid"], "dexterity", dc=12))

    kw = c._send.await_args.kwargs
    assert c._send.await_args.args[0] == "ability-save"
    assert kw["ability"] == "dex"
    assert kw["actorUuid"] == ACTOR["uuid"]


def test_advantage_and_disadvantage_are_separate_flags():
    c = _client()
    run(c.request_saving_throw(ACTOR["uuid"], "dex", advantage=True))
    assert c._send.await_args.kwargs.get("advantage") is True

    c = _client()
    run(c.request_saving_throw(ACTOR["uuid"], "dex", advantage=False))
    assert c._send.await_args.kwargs.get("disadvantage") is True


def test_no_advantage_flag_is_sent_when_none_was_asked_for():
    c = _client()

    run(c.request_saving_throw(ACTOR["uuid"], "dex"))

    kw = c._send.await_args.kwargs
    assert "advantage" not in kw and "disadvantage" not in kw


# ── contested checks ──────────────────────────────────────────────────────

def test_a_contested_check_returns_the_scripts_result():
    live = {"initiatorName": "Probe Dummy", "initiatorRoll": 16,
            "initiatorSuccess": True, "targetName": "Probe Dummy", "targetRoll": 10}
    c = _client()
    c.execute_js = AsyncMock(return_value={"result": live})

    assert run(c.contested_check(ACTOR["uuid"], ACTOR["uuid"], "str", "dex")) == live


def test_a_contested_check_reports_a_failure_rather_than_raising():
    c = _client()
    c.execute_js = AsyncMock(side_effect=RuntimeError("execute_js disabled"))

    assert "error" in run(c.contested_check(ACTOR["uuid"], ACTOR["uuid"]))
