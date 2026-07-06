#!/usr/bin/env python3
"""
Regression test: entering/exiting combat must flip mode + combat.in_combat
atomically.

set_mode() and update_combat() each independently acquire/release
GameStateTracker's lock, so calling them back-to-back (the pattern every
combat-start/end call site used before this fix — actions/executors.py's
execute_start_encounter/execute_end_encounter and chat_listener.py's
_handle_combat_event) leaves a real window between the two awaited calls
where a concurrent task (e.g. a live /api/status request) can observe
mode="combat" with combat.in_combat still False, or the reverse on exit.

set_combat_mode() fixes this by mutating both fields under one lock
acquisition. This test proves: (1) the new atomic method never exposes an
inconsistent combination under a forced concurrent read, and (2) the old
split-call pattern it replaces genuinely could.

Run:
    cd ai-engine && python -m pytest tests/test_state_tracker_combat_mode.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.tracker import GameStateTracker
from state.models import GameState


def _tracker():
    t = GameStateTracker.__new__(GameStateTracker)  # bypass __init__, no real DB
    t.db = None
    t._state = GameState()
    t._state_lock = asyncio.Lock()
    t._combat_snapshot = None
    return t


def _is_consistent(tracker) -> bool:
    mode_is_combat = tracker.state.mode.value == "combat"
    return mode_is_combat == tracker.state.combat.in_combat


async def _race_atomic():
    tracker = _tracker()
    inconsistent_seen = False

    async def reader():
        nonlocal inconsistent_seen
        await asyncio.sleep(0)  # yield once, then race the writer
        if not _is_consistent(tracker):
            inconsistent_seen = True

    async def writer():
        await tracker.set_combat_mode(in_combat=True, turn_order=["a", "b"])

    await asyncio.gather(reader(), writer())
    return tracker, inconsistent_seen


async def _race_old_split_pattern():
    tracker = _tracker()
    inconsistent_seen = False

    async def reader():
        nonlocal inconsistent_seen
        await asyncio.sleep(0)
        if not _is_consistent(tracker):
            inconsistent_seen = True

    async def old_pattern_writer():
        # The exact two-call sequence set_combat_mode replaces.
        await tracker.set_mode("combat")
        await asyncio.sleep(0)  # simulates real-world gap between the two calls
        await tracker.update_combat(in_combat=True, turn_order=["a", "b"])

    await asyncio.gather(reader(), old_pattern_writer())
    return inconsistent_seen


def test_set_combat_mode_is_atomic_under_concurrent_read():
    tracker, inconsistent_seen = asyncio.run(_race_atomic())
    assert not inconsistent_seen, "set_combat_mode must never expose a mode/in_combat mismatch"
    assert tracker.state.mode.value == "combat"
    assert tracker.state.combat.in_combat is True
    assert tracker.state.combat.turn_order == ["a", "b"]


def test_set_combat_mode_exit_resets_combat_state():
    tracker = _tracker()
    asyncio.run(tracker.set_combat_mode(in_combat=True, turn_order=["a", "b"]))
    asyncio.run(tracker.set_combat_mode(in_combat=False))
    assert tracker.state.mode.value == "exploration"
    assert tracker.state.combat.in_combat is False
    assert tracker.state.combat.turn_order == []


def test_old_split_pattern_could_observe_inconsistent_state():
    """Demonstrates the bug set_combat_mode fixes: the old set_mode() +
    update_combat() sequence is not atomic and can expose a mismatched
    mode/in_combat pair to a concurrent reader."""
    inconsistent_seen = asyncio.run(_race_old_split_pattern())
    assert inconsistent_seen, (
        "expected the old split set_mode()+update_combat() pattern to expose "
        "an inconsistent window — if this fails, the repro no longer holds"
    )


if __name__ == "__main__":
    test_set_combat_mode_is_atomic_under_concurrent_read()
    print("PASS  set_combat_mode is atomic under concurrent read")
    test_set_combat_mode_exit_resets_combat_state()
    print("PASS  set_combat_mode(False) resets combat state")
    test_old_split_pattern_could_observe_inconsistent_state()
    print("PASS  old split pattern reproduces the inconsistent window")
    print("\nAll state-tracker combat-mode atomicity tests passed!")
