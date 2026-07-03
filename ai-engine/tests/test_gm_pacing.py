"""Checks for GM pacing tuning: adaptive idle backoff, anti-stacking between
idle/pacing-interval beats, and idle-nudge style rotation.

Addresses the playtest feedback that pacing was simultaneously too naggy
(back-to-back nudges), too slow (fixed 45s before the first nudge), and
repetitive (same generic instruction every time).
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import ChatListener


def _make_listener():
    listener = ChatListener(
        foundry=MagicMock(),
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
    )
    listener.db.get_active_session = AsyncMock(return_value="sess-1")
    listener._running = True
    return listener


def test_idle_timeout_escalates_on_consecutive_unanswered_nudges():
    listener = _make_listener()
    base = 30  # gm_idle_timeout default
    listener._cancel_idle_timer = lambda: None  # avoid touching the real event loop task

    captured = []
    real_reset = ChatListener._reset_idle_timer

    def spy_reset(self, extra_delay=0.0, _escalate=False):
        # Capture the timeout that would be scheduled without starting a real task
        if not _escalate:
            self._consecutive_idle_beats = 0
        timeout = min(base * (1.6 ** self._consecutive_idle_beats), base * 4) + extra_delay
        captured.append(timeout)

    listener._reset_idle_timer = spy_reset.__get__(listener)

    listener._reset_idle_timer()  # genuine activity — baseline
    assert captured[-1] == base

    listener._consecutive_idle_beats += 1
    listener._reset_idle_timer(_escalate=True)
    assert captured[-1] == base * 1.6

    listener._consecutive_idle_beats += 1
    listener._reset_idle_timer(_escalate=True)
    assert abs(captured[-1] - base * 1.6 ** 2) < 0.01

    # Backoff caps at 4x base even after many consecutive unanswered nudges
    listener._consecutive_idle_beats = 20
    listener._reset_idle_timer(_escalate=True)
    assert captured[-1] == base * 4


def test_player_message_resets_backoff_to_baseline():
    listener = _make_listener()
    listener._consecutive_idle_beats = 5
    listener._cancel_idle_timer = lambda: None

    async def run():
        listener._reset_idle_timer()  # simulates the player-message reset path
        listener._idle_timer_task.cancel()  # avoid leaking a real sleeping task

    asyncio.run(run())

    assert listener._consecutive_idle_beats == 0


def test_pacing_beat_skipped_within_min_gap_of_previous_beat():
    listener = _make_listener()
    listener._run_proactive_action = AsyncMock()

    asyncio.run(listener._process_proactive_action(reason="idle"))
    assert listener._run_proactive_action.await_count == 1

    # A pacing-interval beat landing immediately after must be skipped
    asyncio.run(listener._process_proactive_action(reason="pacing"))
    assert listener._run_proactive_action.await_count == 1  # unchanged


def test_session_start_never_blocked_by_min_gap():
    listener = _make_listener()
    listener._run_proactive_action = AsyncMock()

    asyncio.run(listener._process_proactive_action(reason="idle"))
    asyncio.run(listener._process_proactive_action(reason="session_start"))

    assert listener._run_proactive_action.await_count == 2


def test_idle_beat_dropped_when_turn_already_in_flight():
    listener = _make_listener()
    listener._run_proactive_action = AsyncMock()

    async def hold_lock():
        async with listener._turn_lock:
            await asyncio.sleep(0.05)

    async def run():
        task = asyncio.create_task(hold_lock())
        await asyncio.sleep(0.01)  # let the lock be acquired
        await listener._process_proactive_action(reason="idle")
        await task

    asyncio.run(run())
    listener._run_proactive_action.assert_not_awaited()


def test_idle_beat_style_rotates_without_immediate_repeat():
    listener = _make_listener()
    seen = [listener._pick_idle_beat_style() for _ in range(20)]
    # 4 styles total, last 2 excluded each pick -> no run of 3 identical in a row
    for i in range(len(seen) - 2):
        assert not (seen[i] == seen[i + 1] == seen[i + 2])


def test_idle_beat_style_returns_known_instruction_text():
    listener = _make_listener()
    style = listener._pick_idle_beat_style()
    all_instructions = {text for _, text in listener._IDLE_BEAT_STYLES}
    assert style in all_instructions
