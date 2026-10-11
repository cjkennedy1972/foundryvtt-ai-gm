"""FoundryClient world scan, module info, canvas documents, tokens and scene configuration."""
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


# ── scan_world ───────────────────────────────────────────────────────────────

def _scan_client(docs, world=None, scenes=None, actors=None):
    c = FoundryClient()

    async def meta():
        if isinstance(world, Exception):
            raise world
        return world if world is not None else {"name": "W", "systems": [{"name": "dnd5e", "version": "1"}]}

    async def get_scenes():
        return scenes or [{"name": "S"}]

    async def get_actors(world_only=False, strict=False):
        return actors or [{"name": "A"}]

    async def search(doc_type):
        value = docs.get(doc_type, [])
        if isinstance(value, Exception):
            raise value
        return value

    c._world_metadata, c.get_scenes, c.get_actors, c._search_documents = meta, get_scenes, get_actors, search
    return c


def test_scan_world_normalises_every_section():
    c = _scan_client({
        "Item": [{"name": "Sword", "kind": "weapon", "data": {"subtype": "sw", "rarity": "rare"}, "id": "i1"},
                 {"name": "Bare"}],
        "JournalEntry": [{"title": "Lore", "id": "j1", "parentId": "f", "sorting": 4}, {}],
        "Combat": [{"title": "Fight", "id": "c1", "active": True, "actors": [1, 2], "round": 3, "turn": 1}, {}],
    })
    out = run(c.scan_world())
    assert out["world"]["name"] == "W"
    assert out["scenes"] == [{"name": "S"}] and out["actors"] == [{"name": "A"}]
    assert out["items"] == [
        {"name": "Sword", "type": "weapon", "subtype": "sw", "uuid": "i1", "rarity": "rare", "equipped": False},
        {"name": "Bare", "type": "unknown", "subtype": "", "uuid": "", "rarity": "", "equipped": False},
    ]
    assert out["journal"] == [
        {"name": "Lore", "uuid": "j1", "parent": "f", "sorting": 4},
        {"name": "Unknown", "uuid": "", "parent": "", "sorting": 0},
    ]
    assert out["quests"] == [
        {"name": "Fight", "uuid": "c1", "active": True, "tokenCount": 2, "round": 3, "turn": 1},
        {"name": "Encounter", "uuid": "", "active": False, "tokenCount": 0, "round": 0, "turn": 0},
    ]
    assert out["modules"] == [{"name": "dnd5e", "version": "1"}]
    assert "error" not in out


def test_scan_world_sections_degrade_independently():
    boom = RuntimeError("search failed")
    c = _scan_client({"Item": boom, "JournalEntry": boom, "Combat": boom})
    out = run(c.scan_world())
    assert out["items"] == [] and out["journal"] == [] and out["quests"] == []
    assert out["scenes"] == [{"name": "S"}]  # earlier sections survive
    assert "error" not in out


def test_scan_world_ignores_non_list_documents():
    c = _scan_client({"Item": "nope", "JournalEntry": None, "Combat": 3})
    out = run(c.scan_world())
    assert out["items"] == [] and out["journal"] == [] and out["quests"] == []


def test_scan_world_top_level_failure_is_reported_in_error_key():
    out = run(_scan_client({}, world=RuntimeError("metadata exploded")).scan_world())
    assert out["error"] == "metadata exploded"
    assert out["scenes"] == [] and out["modules"] == []


# ── discover_addon_capabilities ──────────────────────────────────────────────

def test_capabilities_counts_and_module_classification():
    scan = {
        "scenes": [{"fogOfWar": True}, {"timedLights": True}, {}],
        "actors": [{"hp": 5}, {"hp": "?"}, {"hp": None}, {}],
        "items": [1], "journal": [1, 2],
        "quests": [{"active": True}, {"active": False}],
        "world": {"systems": [
            {"name": "Monks Combat Details", "version": "2", "active": True},
            {"name": "Dynamic Lighting"},
            {"name": "Item Inventory+"},
            {"name": "Spellbook"},
            {"name": "Misc"},
        ]},
    }
    cap = run(FoundryClient().discover_addon_capabilities(scan))
    assert (cap["available_maps"], cap["scenes_with_fog"], cap["scenes_with_lighting"]) == (3, 1, 1)
    assert (cap["total_actors"], cap["actors_with_combat"]) == (4, 1)
    assert (cap["total_items"], cap["total_journal_entries"], cap["active_encounters"]) == (1, 2, 1)
    assert cap["modules"][0] == {"name": "Monks Combat Details", "version": "2", "enabled": True}
    assert cap["modules"][1]["enabled"] is False
    sug = cap["suggestions"]
    assert sug[0].startswith("Combat Tracker add-on 'Monks Combat Details'")
    assert sug[1].startswith("Lighting system 'Dynamic Lighting'")
    assert sug[2].startswith("Inventory/Loot system 'Item Inventory+'")
    assert sug[3].startswith("Spell/Magic system 'Spellbook'")
    assert len(sug) == 4  # "Misc" adds none, and fog exists so no fog hint


def test_capabilities_empty_world_suggests_fog_and_maps():
    cap = run(FoundryClient().discover_addon_capabilities({}))
    assert cap["available_maps"] == 0 and cap["modules"] == []
    assert cap["suggestions"] == [
        "No fog-of-war scenes detected — consider enabling fog for exploration campaigns",
        "No scenes/maps found — campaign will need to create new maps",
    ]


# ── world info / active modules ──────────────────────────────────────────────

def test_get_world_info_delegates_to_active_modules():
    c = client_with_pw({"execute-js": js([{"id": "m"}])})
    assert run(c.get_world_info()) == {"modules": [{"id": "m"}]}


def test_active_modules_world_cli_routed_with_include_world_flag():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(reads={R.active_modules: {"modules": [], "world": {"id": "w"}}})
    assert run(c.get_active_modules_info(include_world=True)) == {"modules": [], "world": {"id": "w"}}
    assert c.world_cli_router.read_calls == [(R.active_modules, (True,))]
    assert c._pw.sent == []


def test_active_modules_include_world_reads_title_and_id():
    replies = iter([js([{"id": "m"}]), js({"title": "T", "id": "w"})])

    async def reply(**kw):
        return next(replies)

    c = client_with_pw({"execute-js": reply})
    assert run(c.get_active_modules_info(include_world=True)) == {"modules": [{"id": "m"}], "world": {"title": "T", "id": "w"}}
    assert len(c._pw.sent) == 2


def test_active_modules_tolerates_odd_replies():
    c = client_with_pw({"execute-js": js("garbage")})
    assert run(c.get_active_modules_info()) == {"modules": []}
    replies = iter([js([]), js(None)])

    async def reply(**kw):
        return next(replies)

    assert run(client_with_pw({"execute-js": reply}).get_active_modules_info(include_world=True)) == {"modules": [], "world": {}}


def test_active_modules_failure_returns_empty_modules():
    assert run(client_with_pw({"execute-js": RuntimeError("x")}).get_active_modules_info()) == {"modules": []}


# ── canvas documents ─────────────────────────────────────────────────────────

def test_canvas_create_wraps_dict_and_sends_class_name_with_canvas_timeout():
    seen = {}
    c = FoundryClient()

    async def send(msg, **kw):
        seen.update(msg=msg, **kw)
        return {"ok": 1}

    c._send = send
    assert run(c.canvas_create("lights", {"x": 1})) == {"ok": 1}
    assert seen == {"msg": "create-canvas-document", "documentType": "lights", "className": "AmbientLight",
                    "data": [{"x": 1}], "_timeout": client_mod.settings.relay_rpc_timeout_canvas}


def test_canvas_create_unknown_type_passes_name_through_as_class():
    c = client_with_pw()
    run(c.canvas_create("widgets", [{"a": 1}]))
    assert c._pw.sent[0][1]["className"] == "widgets"


def test_canvas_create_world_cli_on_active_scene():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": "S1"}}, write_result="CLI")
    assert run(c.canvas_create("walls", {"c": [0, 0, 1, 1]})) == "CLI"
    assert c._pw.sent == []
    assert c.world_cli_router.write_calls[0][2] is True
    assert run(command_of(c.world_cli_router.write_calls[0][:2])) == (
        "scene.wall.create-many", {"sceneId": "S1", "data": [{"c": [0, 0, 1, 1]}]})


@pytest.mark.parametrize("scene,doc_type,write_result", [
    (None, "walls", "CLI"),          # no active scene
    ("S1", "walls", None),           # CLI declined
    ("S1", "widgets", "CLI"),        # adapter does not know the type
])
def test_canvas_create_falls_back_to_relay(scene, doc_type, write_result):
    c = client_with_pw({"create-canvas-document": {"ok": 1}})
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": scene} if scene else None},
                                    write_result=write_result)
    assert run(c.canvas_create(doc_type, [{"a": 1}])) == {"ok": 1}


def test_canvas_get_shapes():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(reads={R.canvas_documents: [{"_id": "w"}]})
    assert run(c.canvas_get("walls")) == [{"_id": "w"}]
    assert c.world_cli_router.read_calls == [(R.canvas_documents, ("walls",))]
    assert run(client_with_pw({"get-canvas-documents": {"data": [1]}}).canvas_get("walls")) == [1]
    assert run(client_with_pw({"get-canvas-documents": {"documents": [2]}}).canvas_get("walls")) == [2]
    assert run(client_with_pw({"get-canvas-documents": {"results": [3]}}).canvas_get("walls")) == [3]
    assert run(client_with_pw({"get-canvas-documents": {"data": "x"}}).canvas_get("walls")) == []
    assert run(client_with_pw({"get-canvas-documents": RuntimeError("x")}).canvas_get("walls")) == []


def test_canvas_update_world_cli_when_uuid_splits():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(write_result="CLI")
    assert run(c.canvas_update("walls", {"c": [1]}, uuid="Scene.S1.Wall.W1")) == "CLI"
    assert c._pw.sent == []
    assert run(command_of(c.world_cli_router.write_calls[0][:2]))[1]["sceneId"] == "S1"


@pytest.mark.parametrize("uuid,write_result,expect_uuid", [
    ("Scene.S1.Wall.W1", None, "Scene.S1.Wall.W1"),   # CLI declined
    ("Wall.W1", "CLI", "Wall.W1"),                    # not a scene-embedded uuid
    (None, "CLI", None),
])
def test_canvas_update_relay_fallback_shape(uuid, write_result, expect_uuid):
    c = client_with_pw({"update-canvas-document": {"ok": 1}})
    c.world_cli_router = FakeRouter(write_result=write_result)
    assert run(c.canvas_update("tiles", {"a": 1}, uuid=uuid)) == {"ok": 1}
    expected = {"documentType": "tiles", "className": "Tile", "data": {"a": 1}}
    if expect_uuid:
        expected["uuid"] = expect_uuid
    assert c._pw.sent == [("update-canvas-document", expected)]


def test_canvas_delete_world_cli_by_ids_and_by_uuid():
    c = client_with_pw()
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": "S1"}}, write_result="CLI")
    assert run(c.canvas_delete("walls", ids=["a", "b"])) == "CLI"
    assert run(command_of(c.world_cli_router.write_calls[0][:2]))[1]["sceneId"] == "S1"
    assert run(c.canvas_delete("walls", uuid="Scene.S1.Wall.W9")) == "CLI"
    assert len(c.world_cli_router.write_calls) == 2
    assert c._pw.sent == []


@pytest.mark.parametrize("scene,kwargs", [
    (None, {"ids": ["a"]}),                       # no scene
    ("S1", {}),                                   # nothing to delete
    ("S1", {"uuid": "bad-uuid"}),                 # uuid does not split
])
def test_canvas_delete_skips_cli_when_it_cannot_target_anything(scene, kwargs):
    c = client_with_pw({"delete-canvas-document": {"ok": 1}})
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": scene} if scene else None})
    assert run(c.canvas_delete("walls", **kwargs)) == {"ok": 1}
    assert c.world_cli_router.write_calls == []


def test_canvas_delete_relay_payload_and_cli_declined():
    c = client_with_pw({"delete-canvas-document": {"ok": 1}})
    c.world_cli_router = FakeRouter(reads={R.active_scene: {"name": "N", "id": "S1"}}, write_result=None)
    run(c.canvas_delete("lights", uuid="Scene.S1.AmbientLight.L1", ids=["x"]))
    assert c._pw.sent == [("delete-canvas-document", {
        "documentType": "lights", "className": "AmbientLight", "uuid": "Scene.S1.AmbientLight.L1", "ids": ["x"]})]


# ── execute_js / create_entity / create_player_character ────────────────────

def test_execute_js_sends_script_with_timeout_override():
    seen = {}
    c = FoundryClient()

    async def send(msg, **kw):
        seen.update(msg=msg, **kw)
        return {"result": 1}

    c._send = send
    assert run(c.execute_js("return 1;", _timeout=3)) == {"result": 1}
    assert seen == {"msg": "execute-js", "script": "return 1;", "_timeout": 3}


def test_create_entity_cli_then_relay():
    c = client_with_pw({"create": {"ok": 1}})
    c.world_cli_router = FakeRouter(write_result="CLI")
    assert run(c.create_entity("Actor", {"name": "x"})) == "CLI"
    assert c.world_cli_router.write_calls[0][2] is True
    c.world_cli_router.write_result = None
    assert run(c.create_entity("Actor", {"name": "x"})) == {"ok": 1}
    assert c._pw.sent == [("create", {"entityType": "Actor", "data": {"name": "x"}})]


def test_create_player_character_embeds_sanitised_spec_and_returns_result():
    c = client_with_pw({"execute-js": js({"ok": True, "created": True})})
    out = run(c.create_player_character(
        {"name": "N" * 100, "class": "Wizard", "race": "ELF", "background": "Sage", "concept": "c" * 2000},
        user_id="u1"))
    assert out == {"ok": True, "created": True}
    script = c._pw.sent[0][1]["script"]
    spec = json.loads(script.split("const spec = ", 1)[1].split(";\n", 1)[0])
    assert spec == {"name": "N" * 60, "class": "wizard", "race": "elf", "background": "sage",
                    "concept": "c" * 1000, "userId": "u1"}


def test_create_player_character_defaults_when_fields_missing():
    c = client_with_pw({"execute-js": js({"ok": True})})
    run(c.create_player_character({}))
    spec = json.loads(c._pw.sent[0][1]["script"].split("const spec = ", 1)[1].split(";\n", 1)[0])
    assert spec == {"name": "Adventurer", "class": "fighter", "race": "human", "background": "folk-hero",
                    "concept": "", "userId": ""}


def test_create_player_character_invalid_and_failed_replies():
    assert run(client_with_pw({"execute-js": js("x")}).create_player_character({})) == {
        "ok": False, "error": "Invalid Foundry response"}
    assert run(client_with_pw({"execute-js": []}).create_player_character({})) == {
        "ok": False, "error": "Invalid Foundry response"}
    assert run(client_with_pw({"execute-js": RuntimeError("js off")}).create_player_character({})) == {
        "ok": False, "error": "js off"}
