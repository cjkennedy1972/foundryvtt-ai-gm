"""FoundryClient token movement/placement, dispositions, layer clearing and scene configuration."""
import asyncio
import json

import pytest

import foundry.client as client_mod
from foundry import world_cli_reads as R
from foundry.client import FoundryClient
from test_foundry_client_cov_common import FakeRouter, client_with_pw, command_of


def run(coro):
    return asyncio.run(coro)


def js(result):
    return {"result": result}


# ── move_token ───────────────────────────────────────────────────────────────

ROW = {"id": "T1", "name": "Orc", "actorId": "a1"}


def test_move_token_world_cli_resolves_row_by_name():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": "S1"}, R.token_rows: [ROW]},
                                    write_result="CLI")
    assert run(c.move_token("orc", 10, 20)) == "CLI"
    assert c._pw.sent == []
    cmd, params = run(command_of(c.world_cli_router.write_calls[0][:2]))
    assert "T1" in json.dumps(params) and "S1" in json.dumps(params)


@pytest.mark.parametrize("rows,write_result", [([], "CLI"), ([ROW], None), ([{"id": "other", "name": "x"}], "CLI")])
def test_move_token_cli_unavailable_uses_execute_js(rows, write_result):
    c = client_with_pw({"execute-js": js({"ok": True, "id": "T1"})})
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": "S1"}, R.token_rows: rows},
                                    write_result=write_result)
    assert run(c.move_token("orc", 10, 20)) == {"ok": True, "id": "T1"}
    script = c._pw.sent[0][1]["script"]
    assert 'const want="orc";' in script and "x:10.0" in script and "y:20.0" in script


def test_move_token_no_active_scene_skips_row_lookup():
    c = client_with_pw({"execute-js": js({"ok": True})})
    c.world_cli_router = FakeRouter(reads={R.active_scene: None})
    run(c.move_token("T1", 1, 2))
    assert all(a is not R.token_rows for a, _ in c.world_cli_router.read_calls)


def test_move_token_unresolved_returns_script_failure_dict():
    fail = {"ok": False, "error": "token not found", "tried": "nobody"}
    assert run(client_with_pw({"execute-js": js(fail)}).move_token("nobody", 1, 2)) == fail


def test_move_token_js_error_without_result_reports_move_failed():
    out = run(client_with_pw({"execute-js": js(None)}).move_token("t", 1, 2))
    assert out == {"ok": False, "error": "move failed"}


def test_move_token_execute_js_exception_falls_back_to_relay_move_token():
    c = client_with_pw({"execute-js": RuntimeError("gated"), "move-token": {"moved": 1}})
    assert run(c.move_token("t1", 5, 6)) == {"moved": 1}
    assert c._pw.sent[-1] == ("move-token", {"tokenId": "t1", "x": 5, "y": 6})


# ── place_token ──────────────────────────────────────────────────────────────

def _place_client(actors, tokens=(), *, level=None, proto=None, router=None, level_err=False, proto_err=False):
    c = FoundryClient()
    c.created = []
    c.moves = []

    async def get_actors(world_only=False, strict=False):
        return actors

    async def get_scene_tokens(name=None):
        if isinstance(tokens, Exception):
            raise tokens
        return list(tokens)

    async def move_token(tid, x, y):
        c.moves.append((tid, x, y))
        return c.move_reply

    async def execute_js(code, _timeout=None):
        if "levels" in code:
            if level_err:
                raise RuntimeError("no canvas")
            return js(level)
        if proto_err:
            raise RuntimeError("no game")
        return js(proto)

    async def canvas_create(doc_type, data):
        c.created.append((doc_type, data))
        return {"created": True}

    c.move_reply = {"ok": True}
    c.get_actors, c.get_scene_tokens, c.move_token = get_actors, get_scene_tokens, move_token
    c.execute_js, c.canvas_create = execute_js, canvas_create
    c.world_cli_router = router
    return c


ACTOR = {"name": "Goblin", "uuid": "Actor.g1", "img": "g.png"}


def test_place_token_creates_token_with_defaults():
    c = _place_client([ACTOR])
    assert run(c.place_token(actor_name="goblin", x=100, y=200, disposition=-1, hidden=True)) == {"created": True}
    (doc_type, data), = c.created
    assert doc_type == "tokens"
    assert data == {"name": "Goblin", "actorId": "g1", "actorLink": False, "x": 100, "y": 200, "disposition": -1,
                    "hidden": True, "width": 1, "height": 1, "texture": {"src": "g.png"}}


def test_place_token_resolves_by_uuid_short_id_and_adds_level_and_vision():
    actor = {"name": "Hero", "uuid": "Actor.h1", "has_player_owner": True}
    c = _place_client([ACTOR, actor], level="L1", proto="proto.png")
    run(c.place_token(uuid="h1", x=1, y=2))
    data = c.created[0][1]
    assert data["name"] == "Hero" and data["actorId"] == "h1"
    assert data["level"] == "L1"
    assert data["texture"] == {"src": "proto.png"}
    assert data["sight"] == {"enabled": True, "range": 60, "visionMode": "basic"}


def test_place_token_prefers_cli_actor_image():
    c = _place_client([ACTOR], router=FakeRouter(reads={R.actor_image: "cli.png"}))
    run(c.place_token(actor_name="Goblin"))
    assert c.created[0][1]["texture"] == {"src": "cli.png"}
    assert c.world_cli_router.read_calls == [(R.actor_image, ("g1",))]


def test_place_token_default_art_when_no_image_anywhere():
    c = _place_client([{"name": "Goblin", "uuid": "Actor.g1"}], proto=None)
    run(c.place_token(actor_name="Goblin"))
    assert c.created[0][1]["texture"] == {"src": "icons/svg/mystery-man.svg"}


def test_place_token_lookup_failures_do_not_block_creation():
    c = _place_client([ACTOR], level_err=True, proto_err=True)
    run(c.place_token(actor_name="Goblin"))
    data = c.created[0][1]
    assert "level" not in data and data["texture"] == {"src": "g.png"}


def test_place_token_unknown_actor_returns_error_without_creating():
    c = _place_client([ACTOR])
    assert run(c.place_token(actor_name="Ghost")) == {"error": "Actor 'Ghost' not found"}
    assert run(c.place_token(uuid="Actor.zzz")) == {"error": "Actor 'Actor.zzz' not found"}
    assert c.created == []


def test_place_token_moves_existing_token_instead_of_duplicating():
    c = _place_client([ACTOR], tokens=[{"id": "t0", "actorUuid": "Actor.other"},
                                       {"id": "T9", "actorUuid": "Actor.g1"}])
    assert run(c.place_token(actor_name="Goblin", x=7, y=8)) == {"moved": True, "token_id": "T9", "actor": "Goblin"}
    assert c.moves == [("T9", 7, 8)]
    assert c.created == []


def test_place_token_matches_existing_token_by_short_actor_id():
    c = _place_client([ACTOR], tokens=[{"id": "T9", "actorUuid": "g1"}])
    assert run(c.place_token(actor_name="Goblin"))["moved"] is True


def test_place_token_failed_move_is_reported_not_swallowed():
    c = _place_client([ACTOR], tokens=[{"id": "T9", "actorUuid": "Actor.g1"}])
    c.move_reply = {"ok": False, "error": "blocked by wall"}
    out = run(c.place_token(actor_name="Goblin"))
    assert out == {"error": "Failed to move 'Goblin' token: blocked by wall", "token_id": "T9", "actor": "Goblin"}
    assert c.created == []
    c.move_reply = {"ok": False}
    assert "unknown error" in run(c.place_token(actor_name="Goblin"))["error"]


def test_place_token_success_flag_from_move_result_is_honoured():
    c = _place_client([ACTOR], tokens=[{"id": "T9", "actorUuid": "Actor.g1"}])
    c.move_reply = {"success": True}
    assert run(c.place_token(actor_name="Goblin"))["moved"] is True


def test_place_token_dedup_lookup_failure_creates_anyway():
    c = _place_client([ACTOR], tokens=RuntimeError("scene gone"))
    run(c.place_token(actor_name="Goblin"))
    assert len(c.created) == 1


def test_place_token_token_without_actor_or_id_is_not_a_dedup_match():
    c = _place_client([ACTOR], tokens=[{"id": "", "actorUuid": "Actor.g1"}, {"id": "t", "actorUuid": ""}])
    run(c.place_token(actor_name="Goblin"))
    assert len(c.created) == 1 and c.moves == []


# ── dispositions / clear layer / configure_scene ────────────────────────────

def test_actor_dispositions_empty_names_short_circuits():
    c = client_with_pw()
    assert run(c.get_actor_dispositions([])) == {}
    assert c._pw.sent == []


def test_actor_dispositions_paths():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(reads={R.actor_dispositions: {"Orc": -1}})
    assert run(c.get_actor_dispositions(["Orc"])) == {"Orc": -1}
    c2 = client_with_pw({"execute-js": js({"Elf": 1})})
    assert run(c2.get_actor_dispositions(["Elf", 5])) == {"Elf": 1}
    assert 'const want=["Elf", "5"]' in c2._pw.sent[0][1]["script"]
    assert run(client_with_pw({"execute-js": js("x")}).get_actor_dispositions(["a"])) == {}
    assert run(client_with_pw({"execute-js": RuntimeError("x")}).get_actor_dispositions(["a"])) == {}


def test_clear_canvas_layer_rejects_unknown_layer_without_calling_foundry():
    c = client_with_pw()
    assert run(c.clear_canvas_layer("x'); evil(); //")) == {
        "error": "Unknown canvas layer: x'); evil(); //", "success": False}
    assert c._pw.sent == []


def test_clear_canvas_layer_via_execute_js_uses_canvas_timeout():
    seen = {}
    c = FoundryClient()

    async def execute_js(code, _timeout=None):
        seen.update(code=code, t=_timeout)
        return js(3)

    c.execute_js = execute_js
    assert run(c.clear_canvas_layer("walls")) == js(3)
    assert 'scene["walls"]' in seen["code"] and 'deleteEmbeddedDocuments("Wall", ids)' in seen["code"]
    assert seen["t"] == client_mod.settings.relay_rpc_timeout_canvas


def test_clear_canvas_layer_js_failure_falls_back_to_canvas_delete():
    c = client_with_pw({"execute-js": RuntimeError("js off"), "delete-canvas-document": {"deleted": 1}})
    assert run(c.clear_canvas_layer("lights")) == {"deleted": 1}
    assert c._pw.sent[-1] == ("delete-canvas-document", {"documentType": "lights", "className": "AmbientLight"})


def _clear_cli(docs, write_result="CLI"):
    c = client_with_pw({"execute-js": js(0)})
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": "S1"}, R.canvas_documents: docs},
                                    write_result=write_result)
    return c


def test_clear_canvas_layer_world_cli_deletes_existing_ids():
    c = _clear_cli([{"_id": "a"}, {"id": "b"}, {}])
    assert run(c.clear_canvas_layer("walls")) == "CLI"
    cmd, params = run(command_of(c.world_cli_router.write_calls[0][:2]))
    assert params["sceneId"] == "S1" and "a" in json.dumps(params) and "b" in json.dumps(params)


def test_clear_canvas_layer_world_cli_nothing_to_clear_is_a_noop():
    c = _clear_cli([{}])
    assert run(c.clear_canvas_layer("walls")) == {"result": 0, "success": True, "via": "world-cli"}
    assert c.world_cli_router.write_calls == [] and c._pw.sent == []


def test_clear_canvas_layer_world_cli_declined_falls_to_js():
    c = _clear_cli([{"_id": "a"}], write_result=None)
    assert run(c.clear_canvas_layer("walls")) == js(0)


def test_clear_canvas_layer_world_cli_without_active_scene_falls_to_js():
    c = client_with_pw({"execute-js": js(0)})
    c.world_cli_router = FakeRouter(reads={R.active_scene: None})
    # scene lookup falls back to execute-js (returns 0 -> not a str -> None), then the clear script runs
    assert run(c.clear_canvas_layer("walls")) == js(0)
    assert c.world_cli_router.write_calls == []


def test_configure_scene_with_name_updates_that_scene():
    c = client_with_pw({"update-scene": {"ok": 1}})
    assert run(c.configure_scene({"darkness": 1}, scene_name="Crypt")) == {"ok": 1}
    assert c._pw.sent == [("update-scene", {"name": "Crypt", "data": {"darkness": 1}})]


def test_configure_scene_world_cli_on_active_scene():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": "S1"}}, write_result="CLI")
    assert run(c.configure_scene({"darkness": 1})) == "CLI"
    assert run(command_of(c.world_cli_router.write_calls[0][:2])) == (
        "scene.update", {"sceneId": "S1", "patch": {"darkness": 1}})


@pytest.mark.parametrize("scene,write_result", [(None, "CLI"), ("S1", None)])
def test_configure_scene_falls_back_to_execute_js(scene, write_result):
    seen = {}
    c = FoundryClient()

    async def execute_js(code, _timeout=None):
        seen.update(code=code, t=_timeout)
        return js(True)

    c.execute_js = execute_js
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": scene} if scene else None},
                                    write_result=write_result)
    assert run(c.configure_scene({"darkness": 0.5})) == js(True)
    assert seen["code"] == 'await canvas.scene.update({"darkness": 0.5}); return true;'
    assert seen["t"] == client_mod.settings.relay_rpc_timeout_canvas


def test_configure_scene_js_failure_returns_error_dict():
    out = run(client_with_pw({"execute-js": RuntimeError("no canvas")}).configure_scene({"a": 1}))
    assert out == {"error": "no canvas"}
