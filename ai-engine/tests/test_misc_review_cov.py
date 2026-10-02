"""Review-pass coverage for config, utils, world_tick, npc and procedural gaps."""

import asyncio
import logging
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import Settings
from utils import tasks
from utils.path_safety import validate_contained_path


def run(coro):
    return asyncio.run(coro)


# --- config.Settings validators ---------------------------------------------------------------

def _settings(monkeypatch, **kw):
    # Built from explicit kwargs only: nothing here may depend on a developer's .env.
    base = dict(_env_file=None, model="m", llm_api_key="k", relay_scoped_key="rk")
    base.update(kw)
    return Settings(**base)


def test_settings_reject_blank_model_and_bad_urls_and_timeouts(monkeypatch):
    with pytest.raises(ValueError, match="model cannot be empty"):
        _settings(monkeypatch, model="  ")
    assert _settings(monkeypatch, model="  gpt  ").model == "gpt"
    with pytest.raises(ValueError, match="relay_url must be a valid URL"):
        _settings(monkeypatch, relay_url="ftp://x")
    with pytest.raises(ValueError, match="relay_ws_url must be a WebSocket URL"):
        _settings(monkeypatch, relay_ws_url="http://x")
    for bad in (0, -1, 301):
        with pytest.raises(ValueError, match="relay_rpc_timeout must be between"):
            _settings(monkeypatch, relay_rpc_timeout=bad)
        with pytest.raises(ValueError, match="relay_rpc_timeout_canvas must be between"):
            _settings(monkeypatch, relay_rpc_timeout_canvas=bad)
    assert _settings(monkeypatch, relay_rpc_timeout=300).relay_rpc_timeout == 300  # boundary is allowed


def test_settings_warnings_for_unsafe_configuration(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _settings(monkeypatch, relay_rpc_timeout=20, relay_rpc_timeout_canvas=10, allow_execute_js=True,
                  llm_api_key="", relay_scoped_key="", admin_host="0.0.0.0", admin_token="")
    text = caplog.text
    assert "canvas ops will timeout before data ops" in text
    assert "allow_execute_js=true" in text and "llm_api_key is not set" in text
    assert "relay_scoped_key not set" in text and "ADMIN_HOST=0.0.0.0 exposes the admin API" in text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _settings(monkeypatch, admin_host="0.0.0.0", admin_token="t")
        _settings(monkeypatch, admin_host="::1")
    assert "exposes the admin API" not in caplog.text


def test_campaign_vault_path_is_expanded_and_made_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = _settings(monkeypatch, campaign_vault_path="~/vault/../vault2")
    assert s.campaign_vault_path == str((tmp_path / "vault2").resolve())
    assert _settings(monkeypatch, campaign_vault_path="").campaign_vault_path == ""


# --- utils ---------------------------------------------------------------------------------------

def test_spawn_retains_task_logs_failures_and_releases_it(caplog):
    async def go():
        async def boom():
            raise RuntimeError("bg exploded")

        async def fine():
            return 1

        async def slow():
            await asyncio.sleep(10)

        with caplog.at_level(logging.ERROR, logger="utils.tasks"):
            t = tasks.spawn(boom())
            ok = tasks.spawn(fine())
            assert t in tasks._bg_tasks  # strongly referenced while pending
            await asyncio.gather(t, ok, return_exceptions=True)
            await asyncio.sleep(0)
        assert "Background task failed: bg exploded" in caplog.text
        assert t not in tasks._bg_tasks and ok not in tasks._bg_tasks
        c = tasks.spawn(slow())
        await asyncio.sleep(0)
        c.cancel()
        await asyncio.gather(c, return_exceptions=True)
        await asyncio.sleep(0)
        assert c not in tasks._bg_tasks  # cancellation is not logged as a failure
    run(go())


def test_validate_contained_path_rejects_escape_absolute_and_bad_input(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    assert validate_contained_path("a/b.txt", str(base)) == (base / "a" / "b.txt").resolve()
    for bad in ("../x", "a/../../x"):
        with pytest.raises(ValueError, match="escapes base directory"):
            validate_contained_path(bad, str(base))
    with pytest.raises(ValueError, match="Absolute paths not allowed"):
        validate_contained_path("/etc/passwd", str(base), allow_absolute=False)
    with pytest.raises(ValueError, match="escapes"):
        validate_contained_path("/etc/passwd", str(base))
    with pytest.raises(ValueError, match="non-empty"):
        validate_contained_path("", str(base))
    with pytest.raises(ValueError, match="Invalid path"):
        validate_contained_path("a\0b", str(base))  # embedded NUL: resolve() raises ValueError
    (tmp_path / "outside").mkdir()
    (base / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="escapes"):
        validate_contained_path("link/file", str(base))


# --- world_tick.clock ------------------------------------------------------------------------------

def _tick_fixture(tmp_path, npc_names=("Mara",), llm_actions=None, per_call_tokens=0, **kw):
    from events.store import EventStore
    from llm.router import ModelRouter
    from npc.goals import Goal
    from npc.memory import NPCMemory
    from npc.registry import NPCRegistry
    from persistence.db import Database
    from referee.agent import RefereeAgent
    from world_tick.clock import WorldTick

    async def build():
        db = Database(str(tmp_path / "wt.db"))
        await db.init()
        await db.create_session("s1", campaign="C")
        reg = NPCRegistry()
        for i, name in enumerate(npc_names):
            reg.register_npc(f"n{i}", name, "d")
            reg.add_goal(f"n{i}", Goal(description="scheme", status="active",
                                       trigger_conditions={"event_type": "time_advanced"}))
        llm = MagicMock()

        async def gen(**_):
            if per_call_tokens:
                await db.record_llm_usage("s1", "C", per_call_tokens, 0, "m")
            return {"actions": llm_actions or []}

        llm.generate = gen
        store = EventStore(db)
        return WorldTick(db=db, npc_registry=reg, model_router=ModelRouter(llm), referee=RefereeAgent(),
                         memory=NPCMemory(store), event_store=store, **kw), db, reg
    return build()


def test_tick_with_no_session_or_zero_days_does_nothing(tmp_path):
    async def go():
        tick, db, _ = await _tick_fixture(tmp_path)
        s = await tick.tick("C", days=0)
        assert s["days_simulated"] == 0 and s.get("stopped_reason") != "no_session"
        assert await db.get_events_full("C") == []  # no time_advanced event for a zero-day tick
        none = await tick.tick("Unknown campaign", days=3)
        assert none["stopped_reason"] == "no_session" and none["days_simulated"] == 0
        await db.close()
    run(go())


def test_tick_stops_when_the_day_budget_is_spent_and_caps_days(tmp_path):
    async def go():
        acts = [{"type": "narrate", "text": "x", "delivery_path": "a letter"}]
        tick, db, _ = await _tick_fixture(tmp_path, llm_actions=acts, per_call_tokens=700,
                                          token_budget_per_day=500, max_days_per_tick=5)
        s = await tick.tick("C", days=10)   # asks for 10, capped to 5; budget = 500 * 5 = 2500
        # each simulated day spends 700 tokens: days 0-3 run (2800 >= 2500 is detected before day 4)
        assert s["stopped_reason"] == "token_budget" and s["days_simulated"] == 4
        assert s["tokens_spent"] == 2800 and s["proposals"] == 4
        await db.close()
    run(go())


def test_frozen_npcs_are_skipped_including_by_name_in_recent_events(tmp_path):
    async def go():
        acts = [{"type": "narrate", "text": "x", "delivery_path": "a rumour"}]
        tick, db, reg = await _tick_fixture(tmp_path, npc_names=("Mara", "Borin", "Cyd"), llm_actions=acts)
        # Recent play named Mara in a free-text description, and Borin only via payload.
        await db.record_typed_event("s1", "C", "action_resolved", {"target_id": "n1"}, "something")
        await db.record_event("s1", "C", "The party argues with MARA at the gate")
        # The tick's own events must not freeze anyone.
        await db.record_typed_event("s1", "C", "world_simulation", {"npc_id": "n2"}, "Cyd did a thing")
        await db.record_typed_event("s1", "C", "time_advanced", {}, "Cyd waited")
        assert await tick._frozen_npc_ids("C") == {"n0", "n1"}
        s = await tick.tick("C", days=1)
        assert s["npcs_ticked"] == 1  # only Cyd was free to act
        ticked = [e["payload"]["npc_id"] for e in await db.get_events_full("C") if e["type"] == "world_simulation"]
        assert ticked.count("n2") == 2 and "n0" not in ticked[1:] and "n1" not in ticked[1:]
        await db.close()
    run(go())


# --- npc ---------------------------------------------------------------------------------------------

def test_npc_agent_context_marks_loyal_companions_and_limits_memory():
    from npc.agent import NPCAgent
    from npc.goals import Goal
    npc = SimpleNamespace(npc_name="Tika", disposition=1.0)
    agent = NPCAgent(npc, MagicMock(), MagicMock(), MagicMock())
    mem = [{"description": f"e{i}"} for i in range(8)] + [{"type": "fell_back"}]
    ctx = agent._build_context([Goal("guard"), Goal("scout")], mem, {"type": "time_advanced"})
    assert "loyal companion" in ctx and "Triggering event: time_advanced" in ctx
    assert "Active goals: guard; scout" in ctx
    assert "remembers: e4; e5; e6; e7; fell_back" in ctx and "e3" not in ctx  # last five only
    npc.disposition = 0.2
    assert "loyal companion" not in agent._build_context([Goal("g")], [], {"type": "t"})


def test_npc_chat_resolution_and_context_fallbacks():
    from npc.chat import NPCChat, _resolve, parse_npc_chat
    from npc.registry import NPCRegistry
    reg = NPCRegistry()
    reg.register_npc("mira", "Mira", "A smith")
    reg.register_npc("mira_the_elder", "Mira the Elder", "Old")
    reg.register_npc("borin", "Borin", "Innkeeper")
    assert _resolve(reg, "   ") is None
    assert _resolve(reg, "MIRA").npc_id == "mira"            # exact beats the ambiguous substring
    assert _resolve(reg, "mir") is None                        # ambiguous: two NPCs contain it
    assert _resolve(reg, "borin the kind").npc_id == "borin"   # one near match
    npc, msg = parse_npc_chat(reg, "Mira the Elder hello there")
    assert npc.npc_id == "mira_the_elder" and msg == "hello there"  # longest name wins
    assert parse_npc_chat(reg, "Mira: ") == (None, "")
    assert parse_npc_chat(reg, "nobody home") == (None, "")

    memory = MagicMock()
    memory.recall = AsyncMock(side_effect=RuntimeError("db down"))
    router = MagicMock()
    router.get.return_value.generate_text = AsyncMock(return_value='  "Aye."  ')
    chat = NPCChat(reg, memory, router, lore=AsyncMock(return_value="LORE"))
    out = run(chat.reply("C", reg.get_npc("borin"), "Aria", "any rooms?"))
    assert out == "Aye."  # quotes/space stripped; memory outage did not break the reply
    ctx = router.get.return_value.generate_text.await_args.kwargs["context"]
    assert "Innkeeper" in ctx and "LORE" in ctx and "remember" not in ctx
    run(chat.reply("C", reg.get_npc("borin"), "Aria", "and food?"))
    ctx2 = router.get.return_value.generate_text.await_args.kwargs["context"]
    assert "## THIS CONVERSATION SO FAR\nAria: any rooms?\nBorin: Aye." in ctx2
    other = router.get.return_value.generate_text.await_args
    run(chat.reply("Other", reg.get_npc("borin"), "Aria", "hi"))
    assert "CONVERSATION SO FAR" not in router.get.return_value.generate_text.await_args.kwargs["context"]
    assert other is not None


def test_registry_goal_helpers_and_foundry_sync_skips():
    from npc.goals import Goal
    from npc.personality import PersonalityEngine
    from npc.registry import NPCRegistry
    reg = NPCRegistry()
    assert reg.add_goal("ghost", Goal("x")) is False
    assert reg.get_active_goals("ghost") == []
    reg.register_npc("a", "Aria Stone", "d")
    reg.add_goal("a", Goal("low", priority=1, status="active"))
    reg.add_goal("a", Goal("high", priority=9, status="pending"))
    reg.add_goal("a", Goal("done", priority=99, status="completed"))
    assert [g.description for g in reg.get_active_goals("a")] == ["high", "low"]

    client = MagicMock()
    client.get_actors = AsyncMock(return_value=[
        {"name": "Aria Stone", "uuid": "Actor.1"}, {"name": "Aria Stone (copy)", "uuid": "Actor.2"},
        {"name": "", "uuid": "Actor.3"}, {"name": "Nobody", "uuid": "Actor.4"}])
    assert run(reg.sync_with_foundry(client, "s")) == 1  # second actor can't re-map the same NPC
    assert reg.get_npc_id_for_actor("Actor.1") == "a" and reg.get_npc_id_for_actor("Actor.2") is None
    assert run(reg.sync_with_foundry(client, "s")) == 0  # already mapped
    client.get_actors = AsyncMock(side_effect=RuntimeError("relay"))
    assert run(reg.sync_with_foundry(client, "s")) == 0
    client.get_actors = AsyncMock(return_value=[])
    assert run(reg.sync_with_foundry(client, "s")) == 0
    assert PersonalityEngine().get_npc_context("unknown") == ""


# --- procedural -----------------------------------------------------------------------------------------

def test_content_generator_session_week_and_roll_all():
    from procedural.generator import ProceduralGenerator
    random.seed(3)
    g = ProceduralGenerator()
    session = g.generate_session(5, include_settlement=True)
    assert len(session["encounters"]) == 2 and len(session["quests"]) == 2 and len(session["npcs"]) == 4
    assert session["settlement"].name == "Unnamed Settlement" and session["settlement"].size == "village"
    assert "settlement" not in g.generate_session(5)
    week = g.generate_campaign_week(5)
    assert sorted(week) == ["day_1", "day_2", "day_3", "day_4", "day_5", "quests"] and len(week["quests"]) == 5
    assert g.roll_all("bogus") == {"error": "Unknown category: bogus"}
    s = g.roll_all("settlement", name="Redmarch", size="town")
    assert s.name == "Redmarch" and s.size == "town"
    assert g.roll_all("npc") is not None and len(g.roll_all("party", size=6, level=3)) == 6


def test_party_has_core_classes_then_fills_and_arc_links_quests():
    from procedural.npcs import NPCGenerator
    from procedural.quests import QuestGenerator
    random.seed(1)
    party = NPCGenerator().generate_party(size=6, level=7)
    assert [p.class_name for p in party[:4]] == ["Fighter", "Rogue", "Cleric", "Wizard"]
    assert len(party) == 6 and all(p.level == 7 for p in party)
    arc = QuestGenerator().generate_campaign_arc(3)
    assert len(arc) == 3 and "Connected to the previous quest" not in arc[0].complications
    assert all("Connected to the previous quest" in q.complications for q in arc[1:])
    assert QuestGenerator().generate_campaign_arc(0) == []


def _settlement():
    from procedural.settlement import (
        Building, NPCSchedule, Settlement, SettlementNPC, TypedRelationship, TimeSlot)
    s = Settlement(name="Redmarch", size="village")
    s.add_building(Building("The Anchor", "tavern", services=["food", "rooms"]))
    s.add_building(Building("Smithy", "shop", services=["repair"]))
    sched = NPCSchedule("Mara")
    sched.add_entry(TimeSlot.MORNING, "Smithy", "work")
    sched.add_entry(TimeSlot.EVENING, "The Anchor", "drink")
    s.add_npc(SettlementNPC("Mara", "smith", schedule=sched, building="Smithy"))
    s.add_npc(SettlementNPC("Tomas", "farmer", building="Field"))
    s.add_npc(SettlementNPC("Vera", "baker"))
    s.npcs[1].relationships.append(TypedRelationship.__new__(TypedRelationship))
    return s, TimeSlot


def test_settlement_queries():
    s, TS = _settlement()
    assert [n.name for n in s.query_at_time(TS.MORNING, location="Smithy")] == ["Mara"]
    assert [n.name for n in s.query_at_time(TS.EVENING, location="The Anchor")] == ["Mara"]
    assert {n.name for n in s.query_at_time(TS.MORNING)} == {"Mara", "Tomas", "Vera"}
    # an NPC without a schedule is wherever their home building is, or "unknown"
    assert s.npcs[1].find_at_time(TS.NIGHT) == "Field" and s.npcs[2].find_at_time(TS.NIGHT) == "unknown"
    # filtering by building type must not let through NPCs who are not in any building at all
    assert [n.name for n in s.query_at_time(TS.MORNING, building_type="shop")] == ["Mara"]
    assert [n.name for n in s.query_at_time(TS.EVENING, building_type="tavern")] == ["Mara"]
    assert s.query_building("Smithy").building_type == "shop" and s.query_building("Nope") is None
    assert [b.name for b in s.query_building_by_service("food")] == ["The Anchor"]
    assert [b.name for b in s.query_building_by_service("repair", time_slot=TS.MORNING)] == ["Smithy"]
    assert s.query_building_by_service("magic") == []
    assert s._find_building("Smithy").name == "Smithy" and s._find_building("x") is None
    assert s.npc_schedule_lookup("Mara").npc_name == "Mara"
    assert s.npc_schedule_lookup("Tomas") is None and s.npc_schedule_lookup("Nobody") is None
    s.npcs[0].relationships = [SimpleNamespace(relationship_type="friend"), SimpleNamespace(relationship_type="rival")]
    assert len(s.query_relationships("Mara")) == 2
    assert [r.relationship_type for r in s.query_relationships("Mara", rel_type="rival")] == ["rival"]
    assert s.query_relationships("Nobody") == []


def test_settlement_generator_fallback_paths():
    from procedural.settlement import Building, BuildingType, SettlementSize
    from procedural.settlement_gen import SettlementGenerator, _choose_building_name
    gen = SettlementGenerator(random.Random(5))
    assert gen._building_types_for_size("metropolis") == BuildingType.ALL  # unknown size: everything
    # exhausting unique names falls back to a numbered one instead of looping or duplicating
    class Stuck(random.Random):
        def choice(self, seq):
            return seq[0]
    g2 = SettlementGenerator(Stuck())
    first = g2._unique_name("tavern", set())
    assert g2._unique_name("tavern", {first}) == "Tavern #1"
    # occupation weighting: a building with no mapped occupations falls back to a common one
    assert gen._pick_occupation([Building("x", "unmapped-type")]) is not None
    assert gen._pick_occupation([]) is not None
    from procedural.settlement import Settlement
    tiny = Settlement(name="t", size=SettlementSize.HAMLET)
    gen._generate_relationships(tiny)
    assert tiny.npcs == []
    names = {_choose_building_name(t, random.Random(1)) for t in ("farm", "temple", "government", "other")}
    assert len(names) == 4
    out = gen.generate("Quiet", SettlementSize.HAMLET, num_npcs=1, num_buildings=2)
    assert len(out.npcs) == 1 and len(out.buildings) == 2


def test_dungeon_floor_names():
    from procedural.layout_gen import MultiLevelDungeonGenerator
    gen = MultiLevelDungeonGenerator.__new__(MultiLevelDungeonGenerator)
    assert [gen._get_floor_name(i, 3) for i in range(3)] == ["Ground Floor", "Second Floor", "Third Floor"]
    assert gen._get_floor_name(5, 6) == "Floor 6"
    # names keep counting up with the floor number whatever the total (they used to flip to
    # "Basement Level N" for the middle floors of a deep dungeon, then back to "Floor 7")
    names = [gen._get_floor_name(i, 7) for i in range(7)]
    assert names[3:] == ["Fourth Floor", "Floor 5", "Floor 6", "Floor 7"]
    assert len(set(names)) == 7 and not any("Basement" in n for n in names)
