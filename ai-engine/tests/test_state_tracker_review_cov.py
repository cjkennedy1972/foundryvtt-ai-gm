"""GameStateTracker: every mutator persists, load restores, snapshots are isolated copies."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from state.models import GameMode, GameState
from state.tracker import GameStateTracker


class FakeDb:
    def __init__(self, stored=None):
        self.rows = {"game_state": stored} if stored else {}
        self.saves = 0
        self.events = []
        self.session_info = {"session_id": "s1", "campaign": "krynn"}
        self.fail_event = False

    async def load_state(self, key):
        return self.rows.get(key)

    async def save_state(self, key, value):
        self.saves += 1
        self.rows[key] = dict(value)

    async def get_active_session_info(self):
        return self.session_info

    async def record_event(self, session_id, campaign, event):
        if self.fail_event:
            raise RuntimeError("disk full")
        self.events.append((session_id, campaign, event))


@pytest.fixture
def t():
    return GameStateTracker(FakeDb())


@pytest.mark.asyncio
async def test_load_with_nothing_stored_persists_the_defaults(t):
    await t.load()
    assert t.db.saves == 1 and "game_state" in t.db.rows
    assert t.state.mode == GameMode.EXPLORATION


@pytest.mark.asyncio
async def test_load_restores_state_and_parses_the_timestamp():
    stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    db = FakeDb({"mode": "combat", "campaign": "krynn", "current_scene": "Keep", "session_number": 3,
                 "updated_at": stamp.isoformat(), "combat": {"in_combat": True, "round": 2, "turn": 1,
                                                              "turn_order": ["a"]}})
    t = GameStateTracker(db)
    await t.load()
    assert (t.state.mode, t.state.campaign, t.state.session_number) == (GameMode.COMBAT, "krynn", 3)
    assert t.state.updated_at == stamp
    assert t.state.combat.turn_order == ["a"]
    assert db.saves == 0  # loading existing state does not rewrite it


@pytest.mark.asyncio
async def test_load_tolerates_datetime_and_missing_timestamp():
    stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
    t = GameStateTracker(FakeDb({"updated_at": stamp}))
    await t.load()
    assert t.state.updated_at == stamp
    t = GameStateTracker(FakeDb({"campaign": "x", "updated_at": None}))
    await t.load()
    assert t.state.updated_at is None and t.state.campaign == "x"
    assert GameStateTracker._parse_datetime(None) is None


@pytest.mark.asyncio
async def test_each_mutator_persists_what_it_changed(t):
    await t.set_mode("combat")
    assert t.db.rows["game_state"]["mode"] == GameMode.COMBAT
    await t.set_scene("Crypt")
    await t.set_campaign("krynn")
    await t.increment_session()
    await t.set_scene_data({"a": 1})
    await t.set_scene_data({"b": 2})
    await t.set_npc_context("Mara is wary")
    row = t.db.rows["game_state"]
    assert (row["current_scene"], row["campaign"], row["session_number"]) == ("Crypt", "krynn", 2)
    assert row["scene_data"] == {"a": 1, "b": 2}  # merged, not replaced
    assert row["npc_context"] == "Mara is wary"
    assert t.db.saves == 7  # one write per mutator call
    with pytest.raises(ValueError):
        await t.set_mode("not-a-mode")


@pytest.mark.asyncio
async def test_timestamp_only_advances_after_a_successful_save(t):
    await t.save()
    first = t.state.updated_at
    assert first is not None
    t.db.save_state = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        await t.save()
    assert t.state.updated_at == first


@pytest.mark.asyncio
async def test_a_failed_save_releases_the_lock(t):
    t.db.save_state = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        await t.set_scene("A")
    t.db.save_state = AsyncMock()
    await asyncio.wait_for(t.set_scene("B"), 1)  # would hang forever if the lock leaked
    assert t.state.current_scene == "B"


@pytest.mark.asyncio
async def test_update_combat_enters_and_exits(t):
    await t.update_combat(True, round_num=2, turn=3, turn_order=["a", "b"])
    c = t.state.combat
    assert (c.in_combat, c.round, c.turn, c.turn_order) == (True, 2, 3, ["a", "b"])
    await t.update_combat(True, round_num=3, turn=0)  # no order given: previous order kept
    assert t.state.combat.turn_order == ["a", "b"] and t.state.combat.round == 3
    await t.update_combat(False, round_num=9, turn=9, turn_order=["z"])  # exit ignores the numbers
    c = t.state.combat
    assert (c.in_combat, c.round, c.turn, c.turn_order) == (False, 0, 0, [])
    assert t.db.rows["game_state"]["combat"]["in_combat"] is False


@pytest.mark.asyncio
async def test_set_combat_mode_flips_mode_and_flag_together(t):
    await t.set_combat_mode(True, turn_order=["a"])
    assert t.state.mode == GameMode.COMBAT and t.state.combat.in_combat and t.state.combat.turn_order == ["a"]
    await t.set_combat_mode(True)  # re-entering without an order keeps it
    assert t.state.combat.turn_order == ["a"]
    t.state.combat.round, t.state.combat.turn = 4, 2
    await t.set_combat_mode(False)
    assert t.state.mode == GameMode.EXPLORATION and not t.state.combat.in_combat
    assert (t.state.combat.round, t.state.combat.turn, t.state.combat.turn_order) == (0, 0, [])
    assert t.db.rows["game_state"]["mode"] == GameMode.EXPLORATION


@pytest.mark.asyncio
async def test_record_event_persists_state_and_log_and_survives_log_failure(t):
    await t.record_event("Door opened")
    assert t.state.last_event == "Door opened"
    assert t.db.events == [("s1", "krynn", "Door opened")]
    t.db.session_info = {"session_id": "s1", "campaign": None}
    await t.record_event("again")
    assert t.db.events[-1] == ("s1", "", "again")  # missing campaign becomes ""
    t.db.session_info = None
    await t.record_event("no session")
    assert len(t.db.events) == 2 and t.state.last_event == "no session"
    t.db.session_info = {"session_id": "s1", "campaign": "k"}
    t.db.fail_event = True
    await t.record_event("log fails")  # must not raise
    assert t.state.last_event == "log fails" and t.db.rows["game_state"]["last_event"] == "log fails"


@pytest.mark.asyncio
async def test_reset_discards_everything_including_the_snapshot(t):
    await t.set_scene("Crypt")
    await t.save_combat_snapshot(tokens=[{"id": "a"}])
    await t.reset("newcamp")
    assert t.state.campaign == "newcamp" and t.state.current_scene == ""
    assert t.get_combat_snapshot() is None
    assert t.db.rows["game_state"]["campaign"] == "newcamp"


@pytest.mark.asyncio
async def test_encounter_context_is_memory_only_and_cleared_with_scene_data(t):
    await t.set_encounter_context("3 goblins")
    assert t.get_encounter_context() == "3 goblins"
    assert t.db.saves == 0  # not persisted
    await t.set_scene("Crypt")
    await t.set_scene_data({"fog": True})
    saves = t.db.saves
    assert await t.clear_stale_scene_data() is True
    assert t.get_encounter_context() == "" and t.state.scene_data == {}
    assert t.db.saves == saves + 1
    assert await t.clear_stale_scene_data() is False  # nothing stale now
    assert t.db.saves == saves + 1


@pytest.mark.asyncio
async def test_orphaned_scene_data_without_a_scene_is_cleared_and_persisted(t):
    t.state.scene_data = {"old": 1}
    assert await t.clear_stale_scene_data() is True
    assert t.state.scene_data == {}
    assert t.db.rows["game_state"]["scene_data"] == {}  # persisted so the old scene can't come back on load
    t.state.current_scene, t.state.scene_data = "Crypt", {"keep": 1}
    assert await t.clear_stale_scene_data() is False and t.state.scene_data == {"keep": 1}


def test_encounter_context_getter_falls_back_when_state_is_broken(t):
    t._state = None
    assert t.get_encounter_context() == ""


@pytest.mark.asyncio
async def test_combat_snapshot_is_a_deep_enough_copy(t):
    await t.set_scene("Crypt")
    await t.update_combat(True, round_num=2, turn=1, turn_order=["a", "b"])
    tokens = [{"id": "a", "hp": 5}]
    snap = await t.save_combat_snapshot(tokens=tokens, actors={"A": {"hp": 5}, "B": MappingLike()})
    assert t.get_combat_snapshot() is snap
    assert snap["round"] == 2 and snap["turn"] == 1 and snap["turn_order"] == ["a", "b"]
    assert snap["scene"] == "Crypt" and snap["mode"] == "exploration"
    tokens[0]["hp"] = 0
    t.state.combat.turn_order.append("c")
    assert snap["tokens"] == [{"id": "a", "hp": 5}] and snap["turn_order"] == ["a", "b"]
    assert snap["actors"]["B"] == {"x": 1}
    empty = await t.save_combat_snapshot()
    assert empty["tokens"] == [] and empty["actors"] == {}
    t.clear_combat_snapshot()
    assert t.get_combat_snapshot() is None


class MappingLike:
    """An actor payload that is not a plain dict."""
    def keys(self):
        return ["x"]

    def __getitem__(self, k):
        return 1


@pytest.mark.asyncio
async def test_snapshot_mode_when_state_mode_is_a_plain_string(t):
    t.state.mode = "combat"
    assert (await t.save_combat_snapshot())["mode"] == "combat"


@pytest.mark.asyncio
async def test_update_sets_known_fields_ignores_unknown_and_persists(t):
    await t.update(current_scene="Hall", bogus="x", session_number=7)
    assert t.state.current_scene == "Hall" and t.state.session_number == 7
    assert not hasattr(t.state, "bogus")
    assert t.db.rows["game_state"]["session_number"] == 7


@pytest.mark.asyncio
async def test_concurrent_mutations_do_not_lose_updates(t):
    await asyncio.gather(*(t.increment_session() for _ in range(25)))
    assert t.state.session_number == 26
    assert t.db.rows["game_state"]["session_number"] == 26


def test_snapshot_summary_reflects_combat_and_players(t):
    t.state.last_event = "boom"
    assert "Combat" not in t.get_snapshot()
    t.state.combat.in_combat, t.state.combat.round, t.state.combat.turn = True, 2, 1
    t.state.combat.turn_order = ["a", "b"]
    t.state.set_player_actors({"Thalia": "user1"})
    s = t.get_snapshot()
    assert "Combat: Round 2, Turn 1" in s and "Turn Order: a, b" in s
    assert "Thalia: user1" in s and "Last Event: boom" in s


def test_summary_tolerates_string_mode():
    g = GameState()
    g.mode = "social"
    assert "Game Mode: social" in g.get_summary()
