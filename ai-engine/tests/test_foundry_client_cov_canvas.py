"""FoundryClient world scan, module info, canvas documents, tokens and scene configuration."""
import asyncio
import json


import foundry.client as client_mod
from test_foundry_client_cov_common import client_with_pw
from foundry.client import FoundryClient


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
