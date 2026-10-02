#!/usr/bin/env python3
"""ContextReinforcementManager — the periodic-reinforcement coordinator.

Covers the turn-counting/trigger logic, the game-state snapshot it builds
for each reinforcement pass, active-player tracking, and the admin-panel
status/trigger surface. Uses the real GameState/CombatState models (not
fakes) so the branches on `mode`/`in_combat` reflect real shape.

Bug found & fixed while writing these: on_combat_start() called
`self.llm_manager._reinforcer.update_npc_summary(...)` guarded only by
`self.campaign_loader`, unlike every other call site in this module (which
all guard with `self.llm_manager and self.llm_manager._reinforcer`).
`_reinforcer` can legitimately be None (see test_extract_json.py,
test_context_budget_and_combat_history.py), so combat starting while a
campaign is loaded but the reinforcer is unset crashed with an
AttributeError instead of just skipping the NPC-summary update.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context.reinforcement_manager import ContextReinforcementManager
from state.models import GameState, CombatState, GameMode


class _Reinforcer:
    """Stand-in for ContextReinforcer that records calls and returns canned data."""

    def __init__(self):
        self.npc_summary_calls = []
        self.world_summary_calls = []
        self.world_summary = "WORLD SUMMARY TEXT"
        self.anchor_facts_list = ["fact one", "fact two"]
        self.reinforcement_calls = []
        self.next_reinforcement = "REINFORCEMENT PAYLOAD"

    def update_npc_summary(self, npc_data):
        self.npc_summary_calls.append(npc_data)

    def update_world_summary(self, state_dict, scene_data=""):
        self.world_summary_calls.append((state_dict, scene_data))

    def get_anchor_facts(self):
        return self.anchor_facts_list

    def get_reinforcement(self, active_state=None):
        self.reinforcement_calls.append(active_state)
        return self.next_reinforcement


class _LLMManager:
    def __init__(self, reinforcer="default"):
        self._reinforcer = _Reinforcer() if reinforcer == "default" else reinforcer


class _StateTracker:
    def __init__(self, state):
        self.state = state


class _FoundryClient:
    def __init__(self, actors=None, raise_exc=None):
        self._actors = actors or []
        self._raise_exc = raise_exc

    async def get_actors(self, world_only=True):
        if self._raise_exc:
            raise self._raise_exc
        return self._actors


def _exploration_state(scene="The Sunken Chapel"):
    return GameState(mode=GameMode.EXPLORATION, current_scene=scene)


def _combat_state(round_num=3, scene="The Sunken Chapel"):
    return GameState(
        mode=GameMode.COMBAT,
        current_scene=scene,
        combat=CombatState(round=round_num, in_combat=True),
    )


def _mgr(llm_manager=None, state_tracker=None, foundry_client=None, campaign_loader=None,
         reinforce_interval=5):
    return ContextReinforcementManager(
        llm_manager=llm_manager,
        state_tracker=state_tracker,
        foundry_client=foundry_client,
        campaign_loader=campaign_loader,
        reinforce_interval=reinforce_interval,
    )


# ── start/stop ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_sets_running_status():
    mgr = _mgr()
    assert mgr._status == "idle"
    await mgr.start()
    assert mgr._status == "running"
    assert mgr._running is True


@pytest.mark.asyncio
async def test_stop_clears_running_status():
    mgr = _mgr()
    await mgr.start()
    await mgr.stop()
    assert mgr._status == "stopped"
    assert mgr._running is False


# ── record_turn / periodic triggering ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_turn_increments_counts():
    mgr = _mgr(reinforce_interval=100)
    await mgr.record_turn("hello", "hi there")
    assert mgr._turn_count == 1
    assert mgr._message_count == 2

    await mgr.record_turn("again", "yep")
    assert mgr._turn_count == 2
    assert mgr._message_count == 4


@pytest.mark.asyncio
async def test_reinforcement_fires_only_at_the_interval_boundary():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm, reinforce_interval=3)

    for _ in range(2):
        await mgr.record_turn("u", "a")
    assert llm._reinforcer.reinforcement_calls == [], "fired early"

    await mgr.record_turn("u", "a")
    assert len(llm._reinforcer.reinforcement_calls) == 1, "did not fire at the interval"
    assert mgr._last_reinforce_turn == 3


@pytest.mark.asyncio
async def test_reinforcement_interval_resets_after_firing():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm, reinforce_interval=2)

    for _ in range(4):
        await mgr.record_turn("u", "a")

    assert len(llm._reinforcer.reinforcement_calls) == 2
    assert mgr._last_reinforce_turn == 4


# ── active player extraction ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bracketed_player_name_is_tracked():
    mgr = _mgr(reinforce_interval=1000)
    await mgr.record_turn("[Aria]: I attack the goblin", "The goblin recoils.")
    assert list(mgr._active_players) == ["Aria"]


@pytest.mark.asyncio
async def test_same_player_name_is_not_duplicated():
    mgr = _mgr(reinforce_interval=1000)
    await mgr.record_turn("[Aria]: hello", "hi")
    await mgr.record_turn("[Aria]: again", "sure")
    assert list(mgr._active_players) == ["Aria"]


@pytest.mark.asyncio
async def test_message_without_bracket_or_players_keyword_tracks_nothing():
    mgr = _mgr(reinforce_interval=1000)
    await mgr.record_turn("I look around the room", "You see nothing unusual.")
    assert list(mgr._active_players) == []


@pytest.mark.asyncio
async def test_active_players_bounded_to_max():
    mgr = _mgr(reinforce_interval=1000)
    for i in range(25):
        await mgr.record_turn(f"[Player{i}]: hi", "ok")

    players = list(mgr._active_players)
    assert len(players) == 20, "deque should be capped at MAX_ACTIVE_PLAYERS"
    # Oldest entries should have been evicted, newest retained.
    assert "Player0" not in players
    assert "Player24" in players


# ── on_combat_start / on_combat_end ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_combat_start_builds_npc_summary_when_campaign_loaded():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm, campaign_loader=object())

    tokens = [
        {"name": "Goblin", "hp": 5, "max_hp": 10, "class_type": "Grunt"},
        {"name": "Orc"},
    ]
    await mgr.on_combat_start(tokens)

    assert len(llm._reinforcer.npc_summary_calls) == 1
    npc_data = llm._reinforcer.npc_summary_calls[0]
    assert npc_data[0] == {"name": "Goblin", "hp": 5, "max_hp": 10, "class": "Grunt"}
    # Missing fields fall back to sensible defaults rather than KeyError.
    assert npc_data[1] == {"name": "Orc", "hp": "?", "max_hp": "?", "class": "Creature"}


@pytest.mark.asyncio
async def test_on_combat_start_caps_tokens_at_thirty():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm, campaign_loader=object())

    tokens = [{"name": f"T{i}"} for i in range(50)]
    await mgr.on_combat_start(tokens)

    npc_data = llm._reinforcer.npc_summary_calls[0]
    assert len(npc_data) == 30


@pytest.mark.asyncio
async def test_on_combat_start_skips_npc_summary_without_campaign_loader():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm, campaign_loader=None)

    await mgr.on_combat_start([{"name": "Goblin"}])

    assert llm._reinforcer.npc_summary_calls == []


@pytest.mark.asyncio
async def test_on_combat_start_does_not_crash_when_reinforcer_is_unset():
    """Regression test: a campaign loaded with no reinforcer (e.g. llm_manager
    mid-init, or _reinforcer explicitly cleared) must not blow up combat start."""
    llm = _LLMManager(reinforcer=None)
    mgr = _mgr(llm_manager=llm, campaign_loader=object())

    # Must not raise AttributeError; nothing to call update_npc_summary on.
    result = await mgr.on_combat_start([{"name": "Goblin"}])
    assert result is None


@pytest.mark.asyncio
async def test_on_combat_start_does_not_crash_without_llm_manager():
    mgr = _mgr(llm_manager=None, campaign_loader=object())
    result = await mgr.on_combat_start([{"name": "Goblin"}])
    assert result is None


@pytest.mark.asyncio
async def test_on_combat_end_runs_without_error():
    mgr = _mgr()
    result = await mgr.on_combat_end()
    assert result is None


# ── _do_reinforcement / reinforce_context ─────────────────────────────────────

@pytest.mark.asyncio
async def test_reinforcement_pass_in_combat_sets_round_and_skips_npc_lookup():
    llm = _LLMManager()
    tracker = _StateTracker(_combat_state(round_num=4))
    foundry = _FoundryClient(actors=[{"name": "Should not be fetched"}])
    mgr = _mgr(llm_manager=llm, state_tracker=tracker, foundry_client=foundry)

    result = await mgr.reinforce_context()

    assert result == "REINFORCEMENT PAYLOAD"
    sent_state = llm._reinforcer.reinforcement_calls[0]
    assert sent_state["mode"] == "combat"
    assert sent_state["in_combat"] is True
    assert sent_state["combat_round"] == 4
    assert sent_state["nearby_npcs"] == [], "combat mode should not fetch nearby NPCs"
    assert mgr._last_reinforcement_time is not None


@pytest.mark.asyncio
async def test_reinforcement_pass_in_exploration_fetches_nearby_npcs():
    llm = _LLMManager()
    tracker = _StateTracker(_exploration_state(scene="Market Square"))
    foundry = _FoundryClient(actors=[
        {"name": "Halda", "hp": 12}, {"name": "Veyra", "hp": 8},
    ])
    mgr = _mgr(llm_manager=llm, state_tracker=tracker, foundry_client=foundry)

    await mgr._do_reinforcement()

    sent_state = llm._reinforcer.reinforcement_calls[0]
    assert sent_state["mode"] == "exploration"
    assert sent_state["in_combat"] is False
    assert sent_state["combat_round"] is None
    assert sent_state["scene"] == {"name": "Market Square"}
    assert sent_state["nearby_npcs"] == [
        {"name": "Halda", "hp": 12}, {"name": "Veyra", "hp": 8},
    ]


@pytest.mark.asyncio
async def test_nearby_npcs_capped_at_ten():
    llm = _LLMManager()
    tracker = _StateTracker(_exploration_state())
    actors = [{"name": f"NPC{i}", "hp": 1} for i in range(15)]
    foundry = _FoundryClient(actors=actors)
    mgr = _mgr(llm_manager=llm, state_tracker=tracker, foundry_client=foundry)

    await mgr._do_reinforcement()

    sent_state = llm._reinforcer.reinforcement_calls[0]
    assert len(sent_state["nearby_npcs"]) == 10


@pytest.mark.asyncio
async def test_empty_scene_name_yields_empty_scene_dict():
    llm = _LLMManager()
    tracker = _StateTracker(_exploration_state(scene=""))
    mgr = _mgr(llm_manager=llm, state_tracker=tracker, foundry_client=_FoundryClient())

    await mgr._do_reinforcement()

    sent_state = llm._reinforcer.reinforcement_calls[0]
    assert sent_state["scene"] == {}


@pytest.mark.asyncio
async def test_foundry_client_error_fails_closed_to_empty_npc_list():
    llm = _LLMManager()
    tracker = _StateTracker(_exploration_state())
    foundry = _FoundryClient(raise_exc=RuntimeError("foundry unreachable"))
    mgr = _mgr(llm_manager=llm, state_tracker=tracker, foundry_client=foundry)

    # Must not propagate the foundry error.
    result = await mgr._do_reinforcement()

    sent_state = llm._reinforcer.reinforcement_calls[0]
    assert sent_state["nearby_npcs"] == []
    assert result == "REINFORCEMENT PAYLOAD"


@pytest.mark.asyncio
async def test_mode_tolerates_plain_string_from_deserialization():
    """state.mode can arrive as a bare string rather than the GameMode enum."""
    llm = _LLMManager()
    state = _combat_state(round_num=1)
    state.mode = "combat"  # bypass enum coercion, simulating deserialized state
    tracker = _StateTracker(state)
    mgr = _mgr(llm_manager=llm, state_tracker=tracker, foundry_client=_FoundryClient())

    await mgr._do_reinforcement()

    sent_state = llm._reinforcer.reinforcement_calls[0]
    assert sent_state["mode"] == "combat"
    assert sent_state["in_combat"] is True


@pytest.mark.asyncio
async def test_no_state_tracker_sends_empty_state_dict():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm, state_tracker=None, foundry_client=_FoundryClient())

    result = await mgr._do_reinforcement()

    assert llm._reinforcer.reinforcement_calls[0] == {}
    assert result == "REINFORCEMENT PAYLOAD"


@pytest.mark.asyncio
async def test_no_reinforcer_returns_empty_string_without_crashing():
    mgr = _mgr(llm_manager=_LLMManager(reinforcer=None), state_tracker=_StateTracker(_exploration_state()),
                foundry_client=_FoundryClient())

    result = await mgr._do_reinforcement()

    assert result == ""
    assert mgr._last_reinforcement_time is not None


@pytest.mark.asyncio
async def test_no_llm_manager_returns_empty_string():
    mgr = _mgr(llm_manager=None, state_tracker=_StateTracker(_exploration_state()),
                foundry_client=_FoundryClient())

    result = await mgr._do_reinforcement()

    assert result == ""


# ── update_world_summary / _get_anchor_facts ──────────────────────────────────

@pytest.mark.asyncio
async def test_update_world_summary_delegates_and_caches_locally():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm)

    await mgr.update_world_summary({"campaign": "Oakhaven"}, scene_data="dungeon")

    assert llm._reinforcer.world_summary_calls == [({"campaign": "Oakhaven"}, "dungeon")]
    assert mgr._world_summary == "WORLD SUMMARY TEXT"


@pytest.mark.asyncio
async def test_update_world_summary_noop_without_llm_manager():
    mgr = _mgr(llm_manager=None)
    await mgr.update_world_summary({"campaign": "Oakhaven"})
    assert mgr._world_summary == ""


def test_get_anchor_facts_returns_reinforcer_facts():
    llm = _LLMManager()
    mgr = _mgr(llm_manager=llm)
    assert mgr._get_anchor_facts() == ["fact one", "fact two"]


def test_get_anchor_facts_empty_without_llm_manager():
    mgr = _mgr(llm_manager=None)
    assert mgr._get_anchor_facts() == []


# ── get_session_status ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_status_reports_counts_and_players():
    mgr = _mgr(reinforce_interval=5)
    await mgr.record_turn("[Aria]: hi", "hello")

    status = mgr.get_session_status()
    assert status["turn_count"] == 1
    assert status["active_players"] == ["Aria"]
    assert status["last_reinforce_turn"] == 0
    assert status["pending_reinforcement"] is False


def test_session_status_pending_reinforcement_true_once_interval_elapsed():
    mgr = _mgr(reinforce_interval=3)
    mgr._turn_count = 3
    mgr._last_reinforce_turn = 0

    assert mgr.get_session_status()["pending_reinforcement"] is True


def test_session_status_pending_reinforcement_false_below_interval():
    mgr = _mgr(reinforce_interval=3)
    mgr._turn_count = 2
    mgr._last_reinforce_turn = 0

    assert mgr.get_session_status()["pending_reinforcement"] is False


# ── force_reinforce ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_reinforce_runs_a_real_reinforcement_pass():
    llm = _LLMManager()
    tracker = _StateTracker(_exploration_state())
    mgr = _mgr(llm_manager=llm, state_tracker=tracker, foundry_client=_FoundryClient())

    task = mgr.force_reinforce()
    result = await task

    assert result == "REINFORCEMENT PAYLOAD"
    assert len(llm._reinforcer.reinforcement_calls) == 1
