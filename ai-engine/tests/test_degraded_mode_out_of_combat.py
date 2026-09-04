"""Degraded mode for out-of-combat budget exhaustion."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.chat_listener import ChatListener
from llm.usage import TokenBudgetExceeded


class _BudgetProbe:
    def __init__(self, available):
        self._available = iter(available)
        self.checks = 0

    async def budget_available(self, session_id):
        self.checks += 1
        return next(self._available)


def _make_listener(token_usage=None):
    foundry = MagicMock()
    foundry.chat_message = AsyncMock()
    foundry.get_scene_tokens = AsyncMock(return_value=[])
    foundry.get_player_actor_mapping = AsyncMock(return_value={"actor_names": {}})
    foundry.subscribe_to_channel = AsyncMock()
    foundry.subscribe = MagicMock()

    listener = ChatListener(
        foundry=foundry,
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
        combat_loop=None,
        token_usage=token_usage,
    )
    listener._running = True
    listener.narrative_sink = MagicMock()
    listener.narrative_sink.narration = AsyncMock()
    listener.db.get_active_session = AsyncMock(return_value="session-1")
    return listener


def test_budget_exhaustion_out_of_combat_enters_degraded_mode():
    async def run():
        listener = _make_listener()
        error = TokenBudgetExceeded("session", 99, 2, 100)

        await listener.handle_budget_exhausted(error)

        assert listener._degraded_mode_active is True
        listener.foundry.chat_message.assert_awaited_once()
        msg = listener.foundry.chat_message.call_args.args[0]
        assert "narration is paused" in msg
        assert "budget is exhausted" in msg

    asyncio.run(run())


def test_degraded_mode_exits_automatically_when_budget_available():
    async def run():
        budget = _BudgetProbe([True])
        listener = _make_listener(budget)
        await listener.handle_budget_exhausted(TokenBudgetExceeded("session", 99, 2, 100))

        # Reset the mock to see only the exit message
        listener.foundry.chat_message.reset_mock()

        await listener._process_normal_input(
            "I search the room",
            "Player",
            "game state",
            "extra context"
        )

        # Should have exited degraded mode
        assert listener._degraded_mode_active is False
        # Should have announced restoration
        listener.foundry.chat_message.assert_awaited_once()
        msg = listener.foundry.chat_message.call_args.args[0]
        assert "narration is restored" in msg

    asyncio.run(run())


def test_degraded_input_echoes_player_action_without_llm_call():
    async def run():
        listener = _make_listener()
        await listener.handle_budget_exhausted(TokenBudgetExceeded("session", 99, 2, 100))

        # Reset to track degraded input
        listener.narrative_sink.narration.reset_mock()
        listener.llm.generate = AsyncMock()

        await listener._process_degraded_input(
            "I search the room for clues",
            "Alice"
        )

        # Should echo the action without invoking LLM
        listener.narrative_sink.narration.assert_awaited_once()
        echo_call = listener.narrative_sink.narration.call_args
        assert "Alice: I search the room for clues" in echo_call.args[0]
        listener.llm.generate.assert_not_awaited()

    asyncio.run(run())


def test_degraded_mode_makes_no_llm_calls_until_budget_restored():
    async def run():
        budget = _BudgetProbe([False, False, True])
        listener = _make_listener(budget)
        await listener.handle_budget_exhausted(TokenBudgetExceeded("session", 99, 2, 100))

        listener.llm.generate = AsyncMock()

        # First two inputs: still degraded, no LLM calls
        await listener._process_degraded_input("Action 1", "Alice")
        await listener._process_degraded_input("Action 2", "Alice")

        listener.llm.generate.assert_not_awaited()
        assert listener._degraded_mode_active is True

    asyncio.run(run())


if __name__ == "__main__":
    test_budget_exhaustion_out_of_combat_enters_degraded_mode()
    test_degraded_mode_exits_automatically_when_budget_available()
    test_degraded_input_echoes_player_action_without_llm_call()
    test_degraded_mode_makes_no_llm_calls_until_budget_restored()
    print("All tests passed!")
