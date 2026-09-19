#!/usr/bin/env python3
"""#182 put an LLM call on the player's turn and said it had not.

That PR replaced a keyword-matching stub with a model-written session
summary, and its description said the work "runs off the turn path (the
periodic task)". Only half true: _trigger_summarization is reached from
_periodic_summarize AND from record_turn, which chat_listener awaits inline
while resolving a player's message. So every summarize_interval-th turn (10
by default) the table waited on an extra LLM round trip that used to be a
synchronous string build.

The periodic task is the right home. record_turn now starts the pass and
returns, and a pass already running is not started a second time.

Also caps the transcript. It happens to be small today because
ContextReinforcer halves its own log every ten pairs, but recent_turns() is
bounded only by a 500-message deque, so 250 pairs of real turns would be
roughly 57,000 tokens — past the context window — if that halving ever
changed.

Run:
    cd ai-engine && python -m pytest tests/test_summarization_off_the_turn_path.py -v
"""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context.reinforcement_manager import ContextReinforcementManager


def _manager(summary="The party found a bone key.", delay=0.0):
    llm = MagicMock()

    async def _slow(**kwargs):
        if delay:
            await asyncio.sleep(delay)
        return summary

    llm.generate_text = AsyncMock(side_effect=_slow)
    llm._reinforcer = MagicMock()
    llm._reinforcer.recent_turns = MagicMock(return_value=[("a", "b")])
    manager = ContextReinforcementManager(
        llm_manager=llm, state_tracker=MagicMock(),
        foundry_client=MagicMock(), db=None, summarize_interval=2,
    )
    return manager, llm


def test_a_turn_does_not_wait_for_the_model():
    """chat_listener awaits record_turn while the player waits for a reply."""
    async def run():
        manager, llm = _manager(delay=0.5)
        for _ in range(manager.summarize_interval):
            start = time.perf_counter()
            await manager.record_turn("I search the altar.", "You find a box.")
            assert time.perf_counter() - start < 0.2, "the turn blocked on summarisation"
        # let the spawned pass finish so the test does not leak a task
        await asyncio.sleep(0.8)
        llm.generate_text.assert_awaited()

    asyncio.run(run())


def test_the_summary_still_happens():
    async def run():
        manager, llm = _manager()
        for _ in range(manager.summarize_interval):
            await manager.record_turn("x", "y")
        await asyncio.sleep(0.1)

        llm.generate_text.assert_awaited_once()
        llm._reinforcer.update_session_summary.assert_called_once()

    asyncio.run(run())


def test_a_second_pass_does_not_start_while_one_is_running():
    """The periodic task and a turn can reach this at the same time."""
    async def run():
        manager, llm = _manager(delay=0.3)
        await asyncio.gather(
            manager._trigger_summarization(),
            manager._trigger_summarization(),
            manager._trigger_summarization(),
        )
        assert llm.generate_text.await_count == 1

    asyncio.run(run())


def test_a_later_pass_can_still_run():
    """The guard must clear, not latch."""
    async def run():
        manager, llm = _manager()
        await manager._trigger_summarization()
        await manager._trigger_summarization()

        assert llm.generate_text.await_count == 2

    asyncio.run(run())


def test_a_failing_pass_releases_the_guard():
    async def run():
        manager, llm = _manager()
        llm.generate_text = AsyncMock(side_effect=RuntimeError("model down"))
        await manager._trigger_summarization()

        llm.generate_text = AsyncMock(return_value="ok now")
        await manager._trigger_summarization()

        llm.generate_text.assert_awaited_once()

    asyncio.run(run())


def test_the_transcript_handed_to_the_model_is_capped():
    """Bounded only by a 500-message deque today; 250 real pairs would be
    roughly 57,000 tokens."""
    async def run():
        manager, llm = _manager()
        llm._reinforcer.recent_turns = MagicMock(
            return_value=[(f"player line {i}", "GM line " * 80) for i in range(250)]
        )

        await manager._trigger_summarization()

        from utils.token_counter import estimate_tokens
        sent = llm.generate_text.await_args.kwargs["context"]
        assert estimate_tokens(sent) < 12000, f"{estimate_tokens(sent):,} tokens"

    asyncio.run(run())


def test_the_cap_keeps_the_most_recent_turns():
    """A summary of only the opening of a long session is the wrong half."""
    async def run():
        manager, llm = _manager()
        llm._reinforcer.recent_turns = MagicMock(
            return_value=[(f"turn {i}", "reply") for i in range(250)]
        )

        await manager._trigger_summarization()

        sent = llm.generate_text.await_args.kwargs["context"]
        assert "turn 249" in sent
        assert "turn 0\n" not in sent

    asyncio.run(run())
