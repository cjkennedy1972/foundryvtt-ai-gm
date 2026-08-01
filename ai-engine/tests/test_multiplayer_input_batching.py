"""Tests for multi-player input batching — debouncing simultaneous player
messages into one combined GM turn instead of one turn per message.

Drives the real _handle_chat_event dispatch and observes calls to _run_turn
(the extracted single-turn body), rather than re-testing the LLM/dispatch
chain itself.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import settings
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
    listener._run_turn = AsyncMock()
    listener.state_tracker.state.mode = "explore"
    return listener


async def _send(listener, speaker, message):
    await listener._handle_chat_event({"speaker": speaker, "message": message, "type": "general"})


@pytest.fixture(autouse=True)
def _short_debounce(monkeypatch):
    """Fast, deterministic debounce window for these tests only."""
    monkeypatch.setattr(settings, "input_batch_debounce_seconds", 0.05)
    yield


def test_single_active_player_bypasses_debounce_entirely():
    """A fresh listener's first message has only one active speaker — no
    benefit to waiting, so it must run immediately, not after a delay."""
    listener = _make_listener()

    asyncio.run(_send(listener, "Alice", "I check the door."))

    listener._run_turn.assert_called_once_with("I check the door.", "Alice")


def test_two_active_players_get_batched_into_one_turn():
    async def scenario():
        listener = _make_listener()
        # Seed two distinct active speakers first (each alone still runs
        # immediately, matching the single-player-bypass behavior above).
        await _send(listener, "Alice", "first message establishes Alice")
        await _send(listener, "Bob", "first message establishes Bob")
        listener._run_turn.reset_mock()

        # Now both are "active" within the window — the next two messages
        # should coalesce into one combined turn instead of two.
        await _send(listener, "Alice", "I check the door.")
        await _send(listener, "Bob", "I ready my bow.")
        assert listener._run_turn.call_count == 0  # still inside the debounce window

        await asyncio.sleep(0.15)  # let the debounce window close

        listener._run_turn.assert_called_once()
        content, speaker = listener._run_turn.call_args[0]
        assert speaker == "Table"
        assert "I check the door." in content
        assert "I ready my bow." in content

    asyncio.run(scenario())


def test_combat_bypasses_debounce_even_with_multiple_active_players():
    async def scenario():
        listener = _make_listener()
        await _send(listener, "Alice", "seed")
        await _send(listener, "Bob", "seed")
        listener._run_turn.reset_mock()

        listener.state_tracker.state.mode = "combat"
        listener._combat_loop = MagicMock()
        listener._combat_loop.is_running = True

        await _send(listener, "Alice", "I attack the goblin.")

        # Must run immediately — no waiting for a debounce window in combat.
        listener._run_turn.assert_called_once_with("I attack the goblin.", "Alice")

    asyncio.run(scenario())


def test_debounce_disabled_via_config_runs_immediately(monkeypatch):
    async def scenario():
        monkeypatch.setattr(settings, "input_batch_debounce_seconds", 0)
        listener = _make_listener()
        await _send(listener, "Alice", "seed")
        await _send(listener, "Bob", "seed")
        listener._run_turn.reset_mock()

        await _send(listener, "Alice", "I check the door.")

        listener._run_turn.assert_called_once_with("I check the door.", "Alice")

    asyncio.run(scenario())


def test_a_new_message_reschedules_the_batch_window():
    """A message arriving mid-window must extend the wait, not flush early —
    mirroring the existing idle-timer cancel-and-reschedule idiom."""
    async def scenario():
        listener = _make_listener()
        await _send(listener, "Alice", "seed")
        await _send(listener, "Bob", "seed")
        listener._run_turn.reset_mock()

        await _send(listener, "Alice", "first")
        await asyncio.sleep(0.03)  # well before the 0.05s window closes
        await _send(listener, "Bob", "second")  # reschedules the window

        await asyncio.sleep(0.03)
        assert listener._run_turn.call_count == 0  # original window would have fired by now

        await asyncio.sleep(0.1)
        listener._run_turn.assert_called_once()
        content, speaker = listener._run_turn.call_args[0]
        assert "first" in content
        assert "second" in content

    asyncio.run(scenario())
