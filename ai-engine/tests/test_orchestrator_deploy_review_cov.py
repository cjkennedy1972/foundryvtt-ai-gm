"""DeploymentMixin: what each deploy block sends to Foundry, retry-linking, encounter placement, teardown."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import campaign.orchestrator_deploy as od
from campaign.orchestrator import CampaignOrchestrator
from campaign.modules.registry import MODULE_REGISTRY


def run(c):
    return asyncio.run(c)


def orch():
    return CampaignOrchestrator()


def foundry(uuid="X.1"):
    f = MagicMock()
    f.create_entity = AsyncMock(side_effect=lambda t, d: {"data": {"uuid": uuid}})
    return f


def deployment():
    return {k: [] for k in ("scenes", "npcs", "journal_entries", "quest_logs", "loot_tables", "loot_piles",
                            "playlists", "calendar_events", "encounters", "encounter_actors")}


def created(f):
    """[(entity_type, data)] sent to create_entity."""
    return [(c.args[0], c.args[1]) for c in f.create_entity.await_args_list]


# ── helpers ──────────────────────────────────────────────────────────────

def test_create_entity_unwraps_envelope():
    f = MagicMock()
    f.create_entity = AsyncMock(return_value={"data": {"uuid": "A"}})
    assert run(orch()._create_entity(f, "Actor", {})) == {"uuid": "A"}
    f.create_entity = AsyncMock(return_value={"uuid": "B"})
    assert run(orch()._create_entity(f, "Actor", {})) == {"uuid": "B"}
    f.create_entity = AsyncMock(return_value=None)
    assert run(orch()._create_entity(f, "Actor", {})) == {}


def test_entity_uuid_prefers_uuid_then_id():
    assert CampaignOrchestrator._entity_uuid({"uuid": "u", "_id": "i"}) == "u"
    assert CampaignOrchestrator._entity_uuid({"_id": "i"}) == "i"
    assert CampaignOrchestrator._entity_uuid({}) == ""


# ── NPCs ─────────────────────────────────────────────────────────────────

def test_npc_deploy_builds_actor_and_records_uuid_on_campaign_data():
    f, d = foundry("Actor.9"), deployment()
    npc = {"name": "Borin", "description": "Gruff", "hp": 22, "ac": 15, "cr": 2, "faction": "Guild",
           "portrait_src": "img/b.png", "languages": ["Common"]}
    run(orch()._deploy_npcs({"npcs": [npc]}, f, d, {}))
    (etype, data), = created(f)
    assert etype == "Actor" and data["name"] == "Borin" and data["type"] == "npc"
    assert data["system"]["attributes"]["hp"] == {"value": 22, "max": 22, "formula": ""}
    assert data["system"]["attributes"]["ac"]["flat"] == 15 and data["system"]["details"]["cr"] == 2
    assert data["system"]["details"]["biography"]["value"] == "Gruff"
    assert data["flags"]["ai-gm"]["faction"] == "Guild" and data["flags"]["ai-gm"]["npc_type"] == "combat"
    assert data["img"] == "img/b.png" and data["prototypeToken"]["texture"]["src"] == "img/b.png"
    assert d["npcs"] == [{"name": "Borin", "uuid": "Actor.9", "status": "created"}]
    assert npc["existing_uuid"] == "Actor.9"           # a retry will link instead of recreating


def test_npc_with_existing_uuid_is_linked_not_created():
    f, d = foundry(), deployment()
    run(orch()._deploy_npcs({"npcs": [{"name": "A", "existing_uuid": "Actor.old"}]}, f, d, {}))
    f.create_entity.assert_not_awaited()
    assert d["npcs"] == [{"name": "A", "uuid": "Actor.old", "status": "linked"}]


def test_npc_failure_is_recorded_and_next_npc_still_deploys():
    f, d = MagicMock(), deployment()
    f.create_entity = AsyncMock(side_effect=[RuntimeError("relay"), {"data": {"uuid": "Actor.2"}}])
    npcs = [{"name": "A"}, {"name": "B"}]
    run(orch()._deploy_npcs({"npcs": npcs}, f, d, {}))
    assert d["npcs"][0] == {"name": "A", "status": "failed", "error": "relay"}
    assert d["npcs"][1]["status"] == "created" and "existing_uuid" not in npcs[0]


# ── journals / prologue / quests ─────────────────────────────────────────

def test_journal_text_pdf_and_linked_variants():
    f, d = foundry("J.1"), deployment()
    entries = [{"title": "Lore", "body": "<p>x</p>", "type": "lore", "act": 2},
               {"title": "Letter", "pdf_src": "campaigns/c/a.pdf"},
               {"title": "Old", "_deployed_uuid": "J.old"}]
    run(orch()._deploy_journal_entries({"journal_entries": entries}, f, d, {}))
    (t1, text), (t2, pdf) = created(f)
    assert text["pages"][0]["text"]["content"] == "<p>x</p>" and text["flags"]["ai-gm"] == {"type": "lore", "act": 2}
    assert pdf["pages"][0] == {"name": "Letter", "type": "pdf", "src": "campaigns/c/a.pdf"}
    assert [e["status"] for e in d["journal_entries"]] == ["created", "created", "linked"]
    assert entries[0]["_deployed_uuid"] == "J.1"


def test_journal_failure_recorded():
    f, d = MagicMock(), deployment()
    f.create_entity = AsyncMock(side_effect=RuntimeError("x"))
    run(orch()._deploy_journal_entries({"journal_entries": [{"title": "T"}, {"no_title": 1}]}, f, d, {}))
    assert d["journal_entries"][0] == {"title": "T", "status": "failed", "error": "x"}
    assert d["journal_entries"][1]["title"] == "?"


PRO = {"title": "Oath", "vessel": "scroll", "panels": [{"title": "P1", "body": "b"}]}


def test_prologue_created_with_flags_and_book_sheet():
    f, d = foundry("J.p"), deployment()
    pro = dict(PRO, panels=list(PRO["panels"]))
    run(orch()._deploy_prologue({"prologue": pro}, f, d, {"storyteller": {}, "storyteller-x": {}}))
    (_, data), = created(f)
    assert data["name"] == "Prologue — Oath" and data["flags"]["ai-gm"] == {"prologue": True, "vessel": "scroll", "shown": False}
    assert [p["type"] for p in data["pages"]] == ["text"]
    assert d["prologue"] == {"uuid": "J.p", "title": "Oath", "status": "created"}
    assert pro["_deployed_uuid"] == "J.p"
    assert d["journal_entries"] == [{"title": "Prologue — Oath", "uuid": "J.p", "status": "created"}]


def test_prologue_linked_failed_and_skipped():
    f, d = foundry(), deployment()
    run(orch()._deploy_prologue({"prologue": dict(PRO, _deployed_uuid="J.old")}, f, d, {}))
    f.create_entity.assert_not_awaited()
    assert d["prologue"]["status"] == "linked" and d["journal_entries"][0]["status"] == "linked"
    f2, d2 = MagicMock(), deployment()
    f2.create_entity = AsyncMock(side_effect=RuntimeError("no"))
    run(orch()._deploy_prologue({"prologue": PRO}, f2, d2, {}))
    assert d2["prologue"] == {"status": "failed", "error": "no"}
    for pro in ({"title": "x", "panels": []}, "not a dict", None):
        f3, d3 = foundry(), deployment()
        run(orch()._deploy_prologue({"prologue": pro}, f3, d3, {}))
        f3.create_entity.assert_not_awaited() and "prologue" not in d3


def test_quest_body_objectives_and_rewards():
    f, d = foundry("Q.1"), deployment()
    q = {"title": "Find Sword", "description": "d", "id": "q1", "status": "active", "act": 3,
         "objectives": ["go", {"desc": "fight", "check": "DC 12"}, {"desc": "plain"}], "rewards": ["100 gp"]}
    run(orch()._deploy_quest_logs({"quest_logs": [q, {"title": "Done", "_deployed_uuid": "Q.old"}]}, f, d, {}))
    (_, data), = created(f)
    body = data["pages"][0]["text"]["content"]
    assert data["name"] == "[Quest] Find Sword" and data["flags"]["ai-gm"] == {"quest_id": "q1", "status": "active", "act": 3}
    assert "<li>go</li>" in body and "<li>fight <em>(DC 12)</em></li>" in body and "<li>plain</li>" in body
    assert "<li>100 gp</li>" in body
    assert [x["status"] for x in d["quest_logs"]] == ["created", "linked"]


def test_quest_failure_recorded():
    f, d = MagicMock(), deployment()
    f.create_entity = AsyncMock(side_effect=RuntimeError("x"))
    run(orch()._deploy_quest_logs({"quest_logs": [{"title": "Q"}, {}]}, f, d, {}))
    assert d["quest_logs"][0] == {"title": "Q", "status": "failed", "error": "x"}
    assert d["quest_logs"][1]["title"] == "?"


# ── loot ─────────────────────────────────────────────────────────────────

def test_loot_table_weights_ranges_and_formula():
    f, d = foundry("R.1"), deployment()
    table = {"name": "Hoard", "description": "d", "entries": [
        {"name": "sword", "weight": 3}, {"name": "feather", "weight": 0.2},      # rounds to 0 -> min 1
        {"name": "bad", "weight": "heavy"}, {"name": "gem", "weight": "2.6"}, {"name": "dflt"}]}
    run(orch()._deploy_loot_tables({"loot_tables": [table, {"name": "Done", "_deployed_uuid": "R.old"}]}, f, d, {}))
    (etype, data), = created(f)
    assert etype == "RollTable"
    assert [(r["text"], r["weight"], r["range"]) for r in data["results"]] == [
        ("sword", 3, [1, 3]), ("feather", 1, [4, 4]), ("bad", 1, [5, 5]), ("gem", 3, [6, 8]), ("dflt", 1, [9, 9])]
    assert data["formula"] == "1d9" and all(r["drawn"] is False for r in data["results"])
    assert table["_deployed_uuid"] == "R.1"
    assert [x["status"] for x in d["loot_tables"]] == ["created", "linked"]


def test_empty_loot_table_formula_is_1d1():
    f, d = foundry(), deployment()
    run(orch()._deploy_loot_tables({"loot_tables": [{"name": "Empty"}]}, f, d, {}))
    assert created(f)[0][1]["formula"] == "1d1"


def test_loot_table_failure_recorded():
    f, d = MagicMock(), deployment()
    f.create_entity = AsyncMock(side_effect=RuntimeError("x"))
    run(orch()._deploy_loot_tables({"loot_tables": [{"name": "T"}]}, f, d, {}))
    assert d["loot_tables"] == [{"name": "T", "status": "failed", "error": "x"}]


def test_item_pile_created_only_with_module_active(monkeypatch):
    hook = AsyncMock(return_value={"name": "Pile", "type": "character"})
    monkeypatch.setitem(MODULE_REGISTRY, "item-piles", SimpleNamespace(on_loot_table=hook))
    f, d = foundry("A.1"), deployment()
    run(orch()._deploy_loot_tables({"loot_tables": [{"name": "T"}]}, f, d, {}))
    hook.assert_not_awaited()                                         # module not active
    f, d = foundry("A.1"), deployment()
    run(orch()._deploy_loot_tables({"loot_tables": [{"name": "T"}]}, f, d, {"item-piles": {}}))
    assert [t for t, _ in created(f)] == ["RollTable", "Actor"]
    assert d["loot_piles"] == [{"name": "T", "uuid": "A.1", "status": "created"}]


def test_item_pile_hook_failure_recorded_and_none_pile_skipped(monkeypatch):
    monkeypatch.setitem(MODULE_REGISTRY, "item-piles", SimpleNamespace(on_loot_table=AsyncMock(return_value=None)))
    f, d = foundry(), deployment()
    run(orch()._deploy_loot_tables({"loot_tables": [{"name": "T"}]}, f, d, {"item-piles": {}}))
    assert d["loot_piles"] == []
    monkeypatch.setitem(MODULE_REGISTRY, "item-piles", SimpleNamespace(on_loot_table=AsyncMock(side_effect=RuntimeError("h"))))
    run(orch()._deploy_loot_tables({"loot_tables": [{"name": "T"}]}, f, d, {"item-piles": {}}))
    assert d["loot_piles"] == [{"name": "T", "status": "failed", "error": "h"}]


# ── scenes ───────────────────────────────────────────────────────────────

def test_scene_dimensions_grid_and_background():
    f, d = foundry("S.1"), deployment()
    scenes = [{"name": "Crypt", "type": "dungeon", "act": 2, "atmosphere": "cold", "darkness": 0.5,
               "scene_setup": {"grid_width": 10, "grid_height": 8}, "background_src": "maps/c.png"},
              {"name": "Sized", "scene_setup": {"grid_width": 10, "grid_height": 8}, "_grid_size_px": 128,
               "_map_width_px": 1000, "_map_height_px": 700},
              {"name": "NoGrid"},
              {"name": "Linked", "existing_uuid": "S.old", "foundry_scene_name": "Map 1"},
              {"name": "LinkedPlain", "existing_uuid": "S.old2"}]
    c = {"scenes": scenes}
    run(orch()._deploy_scenes(c, f, d, {}))
    crypt, sized, nogrid = [x[1] for x in created(f)]
    assert (crypt["width"], crypt["height"]) == (640, 512) and crypt["grid"] == {"size": 64, "padding": 0} and crypt["padding"] == 0
    assert crypt["darkness"] == 0.5 and crypt["flags"]["ai-gm"] == {"type": "dungeon", "act": 2, "atmosphere": "cold"}
    assert crypt["levels"] == [{"name": "Base Level", "background": {
        "src": "maps/c.png", "offsetX": 0, "offsetY": 0, "scaleX": 1.0, "scaleY": 1.0}}]
    assert (sized["width"], sized["height"], sized["grid"]["size"]) == (1000, 700, 128)
    assert "width" not in nogrid and nogrid["levels"][0]["background"] == {} and nogrid["flags"]["ai-gm"]["type"] == "scene"
    assert [x["status"] for x in d["scenes"]] == ["created", "created", "created", "linked", "linked"]
    assert d["scenes"][3]["foundry_name"] == "Map 1" and d["scenes"][4]["foundry_name"] == "LinkedPlain"
    assert scenes[0]["existing_uuid"] == "S.1"


def test_scene_failure_recorded():
    f, d = MagicMock(), deployment()
    f.create_entity = AsyncMock(side_effect=RuntimeError("x"))
    run(orch()._deploy_scenes({"scenes": [{"name": "S"}, {}]}, f, d, {}))
    assert d["scenes"][0] == {"name": "S", "status": "failed", "error": "x"} and d["scenes"][1]["name"] == "?"


# ── calendar / playlists ─────────────────────────────────────────────────

def test_calendar_events_only_with_simple_calendar():
    ev = {"title": "Festival", "description": "d", "type": "holiday"}
    f, d = foundry("C.1"), deployment()
    run(orch()._deploy_calendar_events({"calendar_events": [ev]}, f, d, {}))
    f.create_entity.assert_not_awaited()
    run(orch()._deploy_calendar_events({"calendar_events": [ev]}, f, d, {"foundryvtt-simple-calendar-reborn": {}}))
    (_, data), = created(f)
    assert data["name"] == "Festival" and "Type: holiday" in data["pages"][0]["text"]["content"]
    assert data["flags"]["ai-gm"]["type"] == "calendar_event"
    assert d["calendar_events"] == [{"title": "Festival", "uuid": "C.1", "status": "created"}]


def test_calendar_event_failure_recorded():
    f, d = MagicMock(), deployment()
    f.create_entity = AsyncMock(side_effect=RuntimeError("x"))
    run(orch()._deploy_calendar_events({"calendar_events": [{"title": "T"}, {}]}, f, d, {"foundryvtt-simple-calendar-reborn": {}}))
    assert d["calendar_events"][0]["status"] == "failed"


@pytest.mark.parametrize("mod", ["dynamic-soundscapes", "moulinette-soundboards"])
def test_playlists_need_a_soundscape_module(mod):
    pl = {"name": "Tavern", "scene": "Inn", "mood": "cosy"}
    f, d = foundry("P.1"), deployment()
    run(orch()._deploy_playlists({"playlists": [pl]}, f, d, {"other": {}}))
    f.create_entity.assert_not_awaited()
    run(orch()._deploy_playlists({"playlists": [pl]}, f, d, {mod: {}}))
    (_, data), = created(f)
    assert data["name"] == "Tavern" and data["description"] == "cosy" and data["flags"]["ai-gm"] == {"scene": "Inn", "mood": "cosy"}
    assert d["playlists"] == [{"name": "Tavern", "uuid": "P.1", "status": "created"}]


def test_playlist_failure_recorded():
    f, d = MagicMock(), deployment()
    f.create_entity = AsyncMock(side_effect=RuntimeError("x"))
    run(orch()._deploy_playlists({"playlists": [{"name": "A"}, {}]}, f, d, {"dynamic-soundscapes": {}}))
    assert d["playlists"][0] == {"name": "A", "status": "failed", "error": "x"} and d["playlists"][1]["name"] == "?"


# ── deploy_to_foundry orchestration ──────────────────────────────────────

def _patched_orch(monkeypatch, audit=None):
    o = orch()
    o._generate_placeholder_portraits = AsyncMock(return_value={"generated": 1})
    monkeypatch.setattr(od, "audit_world_files", AsyncMock(return_value=audit))
    return o


def test_deploy_runs_encounters_portraits_and_audit(monkeypatch):
    o = _patched_orch(monkeypatch, audit={"status": "ok", "broken": 0})
    o.deploy_encounters = AsyncMock(return_value=[{"name": "E"}])
    out = run(o.deploy_to_foundry({"campaign": {"name": "Camp"}, "encounters": [{"name": "E"}]}, foundry(), {}, {"active_modules": {"m": 1}}))
    assert out["encounters"] == [{"name": "E"}] and out["placeholder_portraits"] == {"generated": 1}
    assert out["file_audit"] == {"status": "ok", "broken": 0} and out["status"] == "complete"
    assert o.deploy_encounters.await_args.args[3] == {"m": 1}
    o._generate_placeholder_portraits.assert_awaited_once()
    assert o._generate_placeholder_portraits.await_args.args[1] == "Camp"


def test_deploy_survives_encounter_and_portrait_failures(monkeypatch):
    o = _patched_orch(monkeypatch)
    o.deploy_encounters = AsyncMock(side_effect=RuntimeError("enc"))
    o._generate_placeholder_portraits = AsyncMock(side_effect=RuntimeError("art"))
    out = run(o.deploy_to_foundry({"encounters": [{"name": "E"}]}, foundry(), {}))
    assert out["encounters"] == [{"status": "failed", "error": "enc"}]
    assert "placeholder_portraits" not in out and "file_audit" not in out


# ── wall / position helpers ──────────────────────────────────────────────

def test_wall_blocked_squares_marks_endpoints_and_axis_runs():
    b = orch()._wall_blocked_squares({"walls": [[1, 1, 1, 3], [2, 5, 4, 5], [0, 0, 2, 2], [1, 2]]})
    assert b == {(1, 1), (1, 2), (1, 3), (2, 5), (3, 5), (4, 5), (0, 0), (2, 2)}      # diagonal: endpoints only; malformed ignored
    assert orch()._wall_blocked_squares({"walls": [[3, 4, 3, 2]]}) == {(3, 2), (3, 3), (3, 4)}   # reversed direction


def test_real_wall_blocked_squares_uses_real_grid_size():
    f = MagicMock()
    f.canvas_get = AsyncMock(return_value=[{"c": [100, 100, 100, 400]}, {"c": [0, 0, 300, 0]}, {"c": [1]}, "junk", {"c": []}])
    b = run(orch()._real_wall_blocked_squares(f, 100))
    assert b == {(1, 1), (1, 4), (1, 2), (1, 3), (0, 0), (3, 0), (1, 0), (2, 0)}
    assert run(orch()._real_wall_blocked_squares(f, 0)) == set()
    f.canvas_get = AsyncMock(side_effect=RuntimeError("x"))
    assert run(orch()._real_wall_blocked_squares(f, 100)) == set()


def test_safe_fallback_positions_skip_blocked_and_edges():
    pos = orch()._safe_fallback_positions({"grid_width": 5, "grid_height": 5}, {(1, 1)}, 3)
    assert len(pos) == 3 and all(1 <= x <= 3 and 1 <= y <= 3 for x, y in pos) and (1, 1) not in pos
    assert len(set(pos)) == 3
    shifted = orch()._safe_fallback_positions({"grid_width": 5, "grid_height": 5}, set(), 2, start_offset=1)
    assert shifted != orch()._safe_fallback_positions({"grid_width": 5, "grid_height": 5}, set(), 2)


def test_safe_fallback_positions_never_crash_on_tiny_or_fully_blocked_scenes():
    for setup, blocked in (({"grid_width": 2, "grid_height": 2}, set()),
                           ({"grid_width": 3, "grid_height": 3}, {(1, 1)})):
        pos = orch()._safe_fallback_positions(setup, blocked, 2)
        assert len(pos) == 2


# ── deploy_encounters ────────────────────────────────────────────────────

def enc_foundry(actors=(), tokens_fail=False):
    f = MagicMock()
    f.get_actors = AsyncMock(return_value=list(actors))
    f.activate_scene_and_wait = AsyncMock(return_value={"ok": True})
    f.canvas_create = AsyncMock(side_effect=RuntimeError("canvas") if tokens_fail else None)
    f.canvas_get = AsyncMock(return_value=[])
    f.get_scene_by_name = AsyncMock(return_value=None)
    f._send = AsyncMock(return_value={"data": {"uuid": "J.enc"}})
    return f


def enc_orch(actor_uuid="Actor.m1"):
    o = orch()
    o._ensure_monster_actor = AsyncMock(return_value=actor_uuid)
    return o


def scene_dep(*names, status="created", foundry_name=None):
    s = [{"name": n, "status": status} for n in names]
    if foundry_name:
        s[0]["foundry_name"] = foundry_name
    return {"scenes": s}


def test_tokens_placed_hidden_at_explicit_grid_positions():
    f, o = enc_foundry(), enc_orch()
    enc = {"name": "Ambush", "linked_scene": "Crypt", "monsters": [
        {"name": "Goblin", "count": 2, "cr": 1, "disposition": -1, "placement": [{"grid_x": 3, "grid_y": 4}]}]}
    dep = scene_dep("Crypt")
    res = run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "Crypt", "scene_setup": {"grid_width": 10, "grid_height": 10}}]}, f, dep, {}))
    assert res[0]["tokens_placed"] == 2 and res[0]["status"] == "ok" and res[0]["journal_created"] is True
    t1, t2 = [c.args[1] for c in f.canvas_create.await_args_list]
    assert (t1["name"], t1["x"], t1["y"]) == ("Goblin 1", 192, 256)               # grid (3,4) * 64
    assert t2["name"] == "Goblin 2" and (t2["x"], t2["y"]) != (192, 256)           # no placement: fallback square
    assert t1["hidden"] is True and t1["actorId"] == "m1" and t1["actorLink"] is False and t1["disposition"] == -1
    f.activate_scene_and_wait.assert_awaited_once_with("Crypt", timeout=7)
    assert dep["encounter_actors"] == [{"name": "Goblin", "uuid": "Actor.m1", "reused": False}]
    o._ensure_monster_actor.assert_awaited_once_with(f, "Goblin", cr=1, hp=10, ac=11)     # hp/ac defaults derive from CR


def test_default_hp_ac_from_cr():
    f, o = enc_foundry(), enc_orch()
    enc = {"linked_scene": "S", "monsters": [{"name": "Ogre", "cr": 3}]}
    run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "S"}]}, f, scene_dep("S"), {}))
    assert o._ensure_monster_actor.await_args.kwargs == {"cr": 3, "hp": 24, "ac": 13}


def test_placement_on_a_wall_is_nudged_to_a_safe_square():
    f, o = enc_foundry(), enc_orch()
    enc = {"linked_scene": "S", "monsters": [{"name": "G", "placement": [{"grid_x": 2, "grid_y": 2}]}]}
    scene = {"name": "S", "scene_setup": {"grid_width": 8, "grid_height": 8, "walls": [[2, 1, 2, 3]]}}
    run(o.deploy_encounters({"encounters": [enc], "scenes": [scene]}, f, scene_dep("S"), {}))
    t = f.canvas_create.await_args.args[1]
    assert (t["x"] // 64, t["y"] // 64) not in {(2, 1), (2, 2), (2, 3)}


def test_actor_ownership_reused_flag_and_failed_snapshot_failsafe():
    f, o = enc_foundry(actors=[{"uuid": "Actor.m1"}, {}]), enc_orch()
    enc = {"linked_scene": "S", "monsters": [{"name": "G"}, {"name": "G2"}]}
    dep = scene_dep("S")
    run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "S"}]}, f, dep, {}))
    assert dep["encounter_actors"] == [{"name": "G", "uuid": "Actor.m1", "reused": True}]    # same uuid recorded once
    f2, o2 = enc_foundry(), enc_orch()
    f2.get_actors = AsyncMock(side_effect=RuntimeError("down"))
    dep2 = scene_dep("S")
    run(o2.deploy_encounters({"encounters": [{"linked_scene": "S", "monsters": [{"name": "G"}]}], "scenes": [{"name": "S"}]}, f2, dep2, {}))
    assert dep2["encounter_actors"][0]["reused"] is True                                      # can't prove it's ours


def test_unresolved_actor_places_token_without_actor_id():
    f, o = enc_foundry(), enc_orch(actor_uuid=None)
    dep = scene_dep("S")
    run(o.deploy_encounters({"encounters": [{"linked_scene": "S", "monsters": [{"name": "G"}]}], "scenes": [{"name": "S"}]}, f, dep, {}))
    assert "actorId" not in f.canvas_create.await_args.args[1] and dep.get("encounter_actors", []) == []


def test_linked_scene_uses_real_name_grid_and_walls():
    f, o = enc_foundry(), enc_orch()
    f.get_scene_by_name = AsyncMock(return_value={"grid": {"size": 100}, "width": 1000, "height": 800})
    f.canvas_get = AsyncMock(return_value=[{"c": [300, 100, 300, 500]}])
    enc = {"linked_scene": "Brass Crab", "monsters": [{"name": "G", "count": 1, "placement": [{"grid_x": 3, "grid_y": 2}]}]}
    dep = scene_dep("Brass Crab", status="linked", foundry_name="Map 3.1: Vogler")
    run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "Brass Crab", "scene_setup": {"grid_width": 99}}]}, f, dep, {}))
    f.activate_scene_and_wait.assert_awaited_once_with("Map 3.1: Vogler", timeout=7)
    f.get_scene_by_name.assert_awaited_once_with("Map 3.1: Vogler")
    t = f.canvas_create.await_args.args[1]
    assert (t["x"] // 100, t["y"] // 100) != (3, 2)             # explicit square sits on a real wall -> nudged; pixel scale is the real 100


def test_linked_scene_lookup_failure_falls_back_to_defaults():
    f, o = enc_foundry(), enc_orch()
    f.get_scene_by_name = AsyncMock(side_effect=RuntimeError("x"))
    dep = scene_dep("S", status="linked")
    res = run(o.deploy_encounters({"encounters": [{"linked_scene": "S", "monsters": [{"name": "G"}]}], "scenes": [{"name": "S"}]}, f, dep, {}))
    assert res[0]["tokens_placed"] == 1


def test_scene_switch_failures_mark_partial_but_still_place_tokens():
    f, o = enc_foundry(), enc_orch()
    f.activate_scene_and_wait = AsyncMock(return_value={"ok": False, "error": "nope"})
    enc = {"linked_scene": "S", "monsters": [{"name": "G"}]}
    res = run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "S"}]}, f, scene_dep("S"), {}))
    assert res[0]["status"] == "partial" and res[0]["errors"] == ["scene switch: nope"] and res[0]["tokens_placed"] == 1
    f.activate_scene_and_wait = AsyncMock(side_effect=RuntimeError("timeout"))
    res = run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "S"}]}, f, scene_dep("S"), {}))
    assert res[0]["errors"] == ["scene switch: timeout"]


def test_token_creation_failure_recorded_per_token():
    f, o = enc_foundry(tokens_fail=True), enc_orch()
    enc = {"linked_scene": "S", "monsters": [{"name": "G", "count": 2}]}
    res = run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "S"}]}, f, scene_dep("S"), {}))
    assert res[0]["tokens_placed"] == 0 and res[0]["status"] == "partial"
    assert res[0]["errors"] == ["token 'G 1': canvas", "token 'G 2': canvas"]


@pytest.mark.parametrize("linked,reason", [("", "no linked_scene"), ("Nowhere Land", "not deployed")])
def test_encounter_without_deployed_scene_skips_tokens(linked, reason):
    f, o = enc_foundry(), enc_orch()
    res = run(o.deploy_encounters({"encounters": [{"name": "E", "linked_scene": linked, "monsters": [{"name": "G"}]}], "scenes": []},
                                  f, scene_dep("Crypt"), {}))
    assert res[0]["errors"][0] == f"token placement skipped ({reason})" and res[0]["status"] == "partial"
    f.canvas_create.assert_not_awaited()


@pytest.mark.parametrize("hallucinated,expected", [
    ("the crypt", "The Crypt of Doom"),              # case-insensitive substring
    ("Doom Crypt Sanctum", "The Crypt of Doom"),     # word-overlap fallback
])
def test_linked_scene_fuzzy_matching(hallucinated, expected):
    f, o = enc_foundry(), enc_orch()
    res = run(o.deploy_encounters({"encounters": [{"linked_scene": hallucinated, "monsters": []}], "scenes": []},
                                  f, scene_dep("The Crypt of Doom", "Market"), {}))
    assert res[0]["scene"] == expected


def test_linked_scene_no_match_is_left_as_is():
    f, o = enc_foundry(), enc_orch()
    res = run(o.deploy_encounters({"encounters": [{"linked_scene": "Zzz", "monsters": []}], "scenes": []}, f, scene_dep("Market"), {}))
    assert res[0]["scene"] == "Zzz" and res[0]["status"] == "partial"


def test_encounter_brief_journal_content_and_failure():
    f, o = enc_foundry(), enc_orch()
    enc = {"name": "Boss", "linked_scene": "S", "difficulty": "deadly", "xp_award": 500, "trigger": "enter", "act": 2,
           "description": "desc", "environment_notes": "pillars", "tactical_notes": "focus healer", "rewards": ["gem"],
           "monsters": [{"name": "Lich", "count": 1, "cr": 10, "hp": 135, "ac": 17}]}
    res = run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "S"}]}, f, scene_dep("S"), {}))
    assert res[0]["journal_uuid"] == "J.enc"
    _, kw = f._send.await_args.args, f._send.await_args.kwargs
    data = kw["data"]
    body = data["pages"][0]["text"]["content"]
    assert f._send.await_args.args == ("create",) and kw["entityType"] == "JournalEntry" and data["name"] == "[Encounter] Boss"
    assert "#8e44ad" in body and "DEADLY" in body and "500 XP" in body and "<strong>Lich</strong>" in body and "HP 135 / AC 17" in body
    assert "focus healer" in body and "<li>gem</li>" in body
    assert data["flags"]["ai-gm"] == {"type": "encounter_brief", "act": 2, "linked_scene": "S", "difficulty": "deadly"}
    f._send = AsyncMock(side_effect=RuntimeError("relay"))
    res = run(o.deploy_encounters({"encounters": [enc], "scenes": [{"name": "S"}]}, f, scene_dep("S"), {}))
    assert res[0]["journal_created"] is False and res[0]["status"] == "partial" and res[0]["errors"] == ["journal: relay"]


# ── scene_setup -> canvas ────────────────────────────────────────────────

def test_scene_setup_to_canvas_scales_to_pixels_and_feet():
    setup = {"walls": [[0, 0, 2, 0], [1]], "doors": [{"c": [2, 0, 3, 0], "door": 2, "ds": 1}, {"c": [1]}],
             "lights": [{"x": 1, "y": 2, "bright": 10}, {}], "sounds": [{"x": 1, "y": 1, "radius": 4, "path": "a.ogg"}, {}],
             "darkness": 0.3, "global_illumination": True, "fog_exploration": False, "token_vision": True}
    out = orch()._scene_setup_to_canvas(setup, grid_size=100)
    assert out["walls"][0] == {"c": [0, 0, 200, 0], "move": 20, "sense": 20, "sound": 20, "door": 0, "ds": 0}
    assert out["walls"][1]["c"] == [200, 0, 300, 0] and out["walls"][1]["door"] == 2 and out["walls"][1]["ds"] == 1
    assert len(out["walls"]) == 2
    assert out["lights"][0]["x"] == 100 and out["lights"][0]["y"] == 200 and out["lights"][0]["config"]["bright"] == 10
    assert out["lights"][1]["config"]["color"] == "#ff6600" and out["lights"][1]["config"]["angle"] == 360
    assert out["sounds"][0] == {"x": 100, "y": 100, "path": "a.ogg", "radius": 20, "volume": 0.5, "repeat": True}   # 4 squares = 20 ft
    assert out["sounds"][1]["radius"] == 75
    assert out["scene_config"] == {"darkness": 0.3, "globalLight": True, "fogExploration": False, "tokenVision": True}
    assert orch()._scene_setup_to_canvas({"walls": [[1, 1, 2, 1]]})["walls"][0]["c"] == [64, 64, 128, 64]    # default GRID_PX
    assert orch()._scene_setup_to_canvas({})["scene_config"] == {}


# ── teardown ─────────────────────────────────────────────────────────────

def td_foundry(flag_counts=None, uuid_counts=None, connected=True):
    f = MagicMock()
    f.is_connected = connected
    f.execute_js = AsyncMock(side_effect=[{"result": flag_counts if flag_counts is not None else {"Actor": 2}},
                                          {"result": uuid_counts if uuid_counts is not None else {"Scene": 1}}])
    return f


def write_state(tmp_path, state, name="my camp"):
    d = tmp_path / "campaign_assets" / name
    d.mkdir(parents=True)
    (d / "deployment_state.json").write_text(json.dumps(state))


def test_teardown_requires_connection():
    out = run(orch().teardown_campaign("c", td_foundry(connected=False)))
    assert out["status"] == "error" and out["errors"] == ["Not connected to FoundryVTT"]
    assert run(orch().teardown_campaign("c", None))["status"] == "error"


def test_teardown_flag_pass_only_when_no_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = td_foundry()
    out = run(orch().teardown_campaign("My Camp", f))
    assert out["status"] == "ok" and out["deleted"] == {"flag_pass": {"Actor": 2}}
    assert f.execute_js.await_count == 1


def test_teardown_uuid_pass_never_deletes_reused_documents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, {
        "scenes": [{"name": "Mine", "uuid": "Scene.1", "status": "created"}, {"name": "Theirs", "uuid": "Scene.2", "status": "linked"}, {"name": "no uuid"}],
        "npcs": [{"name": "N", "uuid": "Actor.1"}],
        "encounter_actors": [{"name": "Ogre", "uuid": "Actor.2", "reused": True}, {"name": "Orc", "uuid": "Actor.3", "reused": False}],
        "playlists": [{"name": "P", "uuid": "Playlist.1"}], "unknown_section": [{"uuid": "X.1"}]})
    f = td_foundry()
    out = run(orch().teardown_campaign("My Camp", f))
    sent = f.execute_js.await_args_list[1].args[0]
    for keep in ("Scene.2", "Actor.2"):
        assert keep not in sent
    for gone in ("Scene.1", "Actor.1", "Actor.3", "Playlist.1"):
        assert gone in sent
    assert "X.1" not in sent
    assert out["preserved"] == ["Theirs (scenes)", "Ogre (encounter_actors)"]
    assert out["deleted"]["uuid_pass"] == {"Scene": 1} and out["status"] == "ok"


def test_teardown_all_preserved_skips_uuid_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, {"scenes": [{"name": "T", "uuid": "Scene.2", "status": "linked"}]})
    f = td_foundry()
    out = run(orch().teardown_campaign("My Camp", f))
    assert f.execute_js.await_count == 1 and "uuid_pass" not in out["deleted"] and out["preserved"] == ["T (scenes)"]


def test_teardown_errors_make_status_partial(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = MagicMock()
    f.is_connected = True
    f.execute_js = AsyncMock(side_effect=[RuntimeError("flag boom"), RuntimeError("uuid boom")])
    write_state(tmp_path, {"npcs": [{"name": "N", "uuid": "Actor.1"}]})
    out = run(orch().teardown_campaign("My Camp", f))
    assert out["status"] == "partial" and out["errors"] == ["flag_pass: flag boom", "uuid_pass: uuid boom"]
    (tmp_path / "campaign_assets" / "my camp" / "deployment_state.json").write_text("{corrupt")
    f.execute_js = AsyncMock(return_value={"result": "not a dict"})
    out = run(orch().teardown_campaign("My Camp", f))
    assert out["deleted"]["flag_pass"] == {} and out["errors"][0].startswith("state_read:")
