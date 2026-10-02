"""Time for the table to act: the idle clock, the deferred pacing check, activity signals, and combat turn
ownership, turn gaps and reaction windows. Each test pins a behavior that used to squeeze players."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from combat.loop import CombatLoop
from config import settings
from foundry.chat_listener import ChatListener
from tts import playback


def _listener(**attrs):
    state = SimpleNamespace(mode="exploration", player_actors={"Grazen": "u-chris"},
                            combat=SimpleNamespace(turn_order=[], turn=0))
    tracker = MagicMock()
    tracker.state = state
    listener = ChatListener(foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(), state_tracker=tracker, db=MagicMock())
    listener.db.get_active_session = AsyncMock(return_value="s1")
    listener._running = True
    for k, v in attrs.items():
        setattr(listener, k, v)
    return listener


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(playback, "is_speaking", lambda: False)


# ── idle clock ──────────────────────────────────────────────────────────────

def _drive_countdown(listener, *, fired):
    """Run one idle countdown with the beat stubbed; returns the re-arm calls made."""
    rearmed = []
    listener._reset_idle_timer = lambda extra_delay=0.0, _escalate=False: rearmed.append(_escalate)

    async def beat(reason="idle"):
        listener.beats.append(reason)
        if fired:
            listener._last_proactive_beat_at += 1

    listener.beats = []
    listener._process_proactive_action = beat
    asyncio.run(listener._idle_countdown(0))
    return rearmed


def test_a_nudge_that_did_not_speak_does_not_count_against_the_table():
    listener = _listener()
    rearmed = _drive_countdown(listener, fired=False)
    assert listener._consecutive_idle_beats == 0 and rearmed == [True]


def test_nudges_stop_after_the_limit_and_wait_quietly(monkeypatch):
    monkeypatch.setattr(settings, "gm_max_unanswered_nudges", 2)
    listener = _listener()
    assert _drive_countdown(listener, fired=True) == [True] and listener._consecutive_idle_beats == 1
    assert _drive_countdown(listener, fired=True) == [] and listener._consecutive_idle_beats == 2   # no re-arm: waits


def test_a_limit_of_zero_nudges_forever(monkeypatch):
    monkeypatch.setattr(settings, "gm_max_unanswered_nudges", 0)
    listener = _listener(_consecutive_idle_beats=50)
    assert _drive_countdown(listener, fired=True) == [True]


def test_the_clock_waits_while_the_gm_is_still_speaking(monkeypatch):
    monkeypatch.setattr(playback, "is_speaking", lambda: True)
    listener = _listener()
    rearmed = _drive_countdown(listener, fired=True)
    assert listener.beats == [] and rearmed == [True] and listener._consecutive_idle_beats == 0


def test_the_pacing_check_is_owed_not_fired_after_the_reply_and_rides_the_next_nudge():
    listener = _listener(_pacing_due=True)
    _drive_countdown(listener, fired=True)
    assert listener.beats == ["pacing"] and listener._pacing_due is False


def test_a_pacing_check_that_was_skipped_stays_owed():
    listener = _listener(_pacing_due=True)
    _drive_countdown(listener, fired=False)
    assert listener._pacing_due is True


def test_player_activity_keeps_the_gm_quiet_but_not_during_the_ais_own_turn():
    async def run():
        listener = _listener()
        resets = []
        listener._reset_idle_timer = lambda *a, **k: resets.append(1)
        listener._note_player_activity()
        async with listener._turn_lock:
            listener._note_player_activity()
        return resets

    assert asyncio.run(run()) == [1]


# ── combat: who ends a turn ─────────────────────────────────────────────────

@pytest.mark.parametrize("text,ends", [
    ("I swing my axe. End turn", True), ("I'm done", True), ("done.", True), ("pass", True),
    ("that's all", True), ("I attack the goblin", False), ("Do I see the door? done with that, what is it?", False),
])
def test_end_of_turn_phrases(text, ends):
    assert bool(ChatListener._END_TURN.search(text.strip())) is ends


def _combat_listener(awaiting=True):
    loop = SimpleNamespace(awaiting_pc={"id": "t1", "name": "Grazen"} if awaiting else None, is_running=True,
                           turn_key=(1, 0), advance_pc_turn=MagicMock(), reaction_window_open=False,
                           reaction_declared=False, reaction_processed=asyncio.Event())
    return _listener(_combat_loop=loop), loop


def test_only_the_turns_owner_owns_it():
    listener, loop = _combat_listener()
    assert listener._owns_current_turn({"author": {"id": "u-chris"}}) is True
    assert listener._owns_current_turn({"author": {"id": "u-sara"}}) is False
    assert _combat_listener(awaiting=False)[0]._owns_current_turn({"author": {"id": "u-chris"}}) is False   # an NPC's turn


def test_an_unmappable_author_is_treated_as_the_owner_so_a_fight_never_stalls():
    listener, _ = _combat_listener()
    listener.state_tracker.state.player_actors = {}
    assert listener._owns_current_turn({"author": {"id": "anyone"}}) is True


async def _process(listener, content, owner=True, results_ok=True):
    listener._process_player_input = AsyncMock(return_value=([], []))
    listener._record_exchange = AsyncMock()
    await listener._process_combat_input(content, "Chris", "state", "ctx", owner=owner)
    return listener._process_player_input.await_args.kwargs["advance_turn"]


def test_a_players_first_message_no_longer_ends_their_turn_until_they_go_quiet(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0.05)

    async def run():
        listener, loop = _combat_listener()
        advanced = await _process(listener, "I move to the door")
        before = loop.advance_pc_turn.call_count
        await asyncio.sleep(0.15)
        return advanced, before, loop.advance_pc_turn.call_count

    advanced, before, after = asyncio.run(run())
    assert advanced is False and before == 0 and after == 1


def test_a_second_message_inside_the_window_restarts_it(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0.1)

    async def run():
        listener, loop = _combat_listener()
        await _process(listener, "I move to the door")
        await asyncio.sleep(0.06)
        await _process(listener, "and attack the guard")
        await asyncio.sleep(0.06)
        mid = loop.advance_pc_turn.call_count
        await asyncio.sleep(0.15)
        return mid, loop.advance_pc_turn.call_count

    assert asyncio.run(run()) == (0, 1)


def test_saying_end_turn_ends_it_at_once(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 8)
    listener, _ = _combat_listener()
    assert asyncio.run(_process(listener, "I attack. End turn")) is True


def test_a_quiet_window_of_zero_keeps_the_old_one_message_turn(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0)
    listener, _ = _combat_listener()
    assert asyncio.run(_process(listener, "I attack")) is True


def test_a_question_does_not_start_the_end_of_turn_timer(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0.02)

    async def run():
        listener, loop = _combat_listener()
        await _process(listener, "How many enemies can I see?")
        await asyncio.sleep(0.08)
        return loop.advance_pc_turn.call_count

    assert asyncio.run(run()) == 0


def test_another_players_message_never_ends_someone_elses_turn(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0.02)

    async def run():
        listener, loop = _combat_listener()
        advanced = await _process(listener, "Watch out Grazen! End turn", owner=False)
        await asyncio.sleep(0.08)
        return advanced, loop.advance_pc_turn.call_count

    assert asyncio.run(run()) == (False, 0)


def test_a_late_timer_cannot_end_the_next_players_turn(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0.05)

    async def run():
        listener, loop = _combat_listener()
        await _process(listener, "I move")
        loop.turn_key = (1, 1)                     # the turn moved on by other means
        await asyncio.sleep(0.12)
        return loop.advance_pc_turn.call_count

    assert asyncio.run(run()) == 0


# ── combat loop: breathing room and reactions ───────────────────────────────

def _loop():
    loop = CombatLoop(foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(), state_tracker=MagicMock(), db=MagicMock())
    loop.foundry.chat_message = AsyncMock()
    loop._running = True
    loop._pc_tokens = [{"id": "pc1", "name": "Grazen"}]
    return loop


def test_there_is_a_pause_before_an_npc_acts(monkeypatch):
    monkeypatch.setattr(settings, "combat_turn_gap_seconds", 0.1)

    async def run():
        t0 = asyncio.get_running_loop().time()
        await _loop()._turn_gap()
        return asyncio.get_running_loop().time() - t0

    assert asyncio.run(run()) >= 0.1


def test_the_pause_waits_for_narration_to_finish_first(monkeypatch):
    monkeypatch.setattr(settings, "combat_turn_gap_seconds", 0)
    states = iter([True, True, False])
    monkeypatch.setattr(playback, "is_speaking", lambda: next(states, False))

    async def run():
        t0 = asyncio.get_running_loop().time()
        await _loop()._turn_gap()
        return asyncio.get_running_loop().time() - t0

    assert asyncio.run(run()) >= 1.0           # two 0.5s polls


def test_no_reaction_window_for_attacks_on_npcs_or_when_it_is_off(monkeypatch):
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 0.05)
    loop = _loop()
    asyncio.run(loop._reaction_window("Dragon", [{"type": "attack_with_item", "target_token_id": "npc9"}]))
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 0)
    asyncio.run(loop._reaction_window("Dragon", [{"type": "attack_with_item", "target_token_id": "pc1"}]))
    assert loop.foundry.chat_message.await_count == 0


def test_an_attack_on_a_pc_opens_a_window_and_names_them(monkeypatch):
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 0.05)
    loop = _loop()
    asyncio.run(loop._reaction_window("Dragon", [{"type": "attack_with_item", "target_token_id": "pc1"},
                                                   {"type": "attack_with_item", "target_token_id": "pc1"}]))
    said = loop.foundry.chat_message.await_args.args[0]
    assert "Dragon" in said and said.count("Grazen") == 1 and loop.reaction_window_open is False


def test_a_declared_reaction_is_resolved_before_the_attack_lands(monkeypatch):
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 0.05)

    async def run():
        loop = _loop()
        order = []

        async def player():
            await asyncio.sleep(0.02)
            loop.reaction_declared = True               # the chat listener saw a message in the window
            await asyncio.sleep(0.15)                   # ...and takes a while to process it
            order.append("reaction resolved")
            loop.reaction_processed.set()

        task = asyncio.create_task(player())
        await loop._reaction_window("Dragon", [{"type": "attack_with_item", "target_token_id": "pc1"}])
        order.append("attack proceeds")
        await task
        return order

    assert asyncio.run(run()) == ["reaction resolved", "attack proceeds"]


# ── activity from Foundry ───────────────────────────────────────────────────

def test_a_token_move_is_activity_not_a_scene_change():
    """Seen live: moving a token arrives on the scene-events channel as eventType 'token-update'."""
    listener = _listener()
    listener._note_player_activity = MagicMock()
    listener.state_tracker.set_scene = AsyncMock()

    asyncio.run(listener._handle_scene_event(
        {"data": {"data": {"changes": {"_id": "t1", "x": 192, "y": 128}, "eventType": "token-update"}, "name": "Goblin"}}))

    listener._note_player_activity.assert_called_once()
    listener.state_tracker.set_scene.assert_not_awaited()          # the token's name is not a scene name


def test_a_real_scene_change_is_still_a_scene_change():
    listener = _listener()
    listener._note_player_activity = MagicMock()
    listener.state_tracker.set_scene = AsyncMock()
    listener.state_tracker.save = AsyncMock()
    listener.state_tracker.state.scene_data = {}

    asyncio.run(listener._handle_scene_event({"sceneName": "The Gatehouse"}))

    listener.state_tracker.set_scene.assert_awaited_once_with("The Gatehouse")
    listener._note_player_activity.assert_not_called()
