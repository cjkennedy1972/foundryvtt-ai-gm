#!/usr/bin/env python3
"""Long sessions forgot, because nothing summarised them.

trim_messages_to_budget discards the oldest messages once history passes the
budget, so past roughly 40,000 tokens the GM loses the early session. Two
things were supposed to prevent that and neither called the LLM:

  ContextReinforcer.try_summarize matched keywords and emitted lines like
  "- GM response describing events".

  ContextReinforcementManager._trigger_summarization emitted
  "Key events: ...; Session duration: 47 minutes".

Both wrote the same session_summary slot, so they overwrote each other, and
what reached the prompt said nothing about what had happened.

The summary runs off the turn path (the periodic task) and through
generate_text, which builds its own messages with no history — so it cannot
recurse into the call that triggered it.

Run:
    cd ai-engine && python -m pytest tests/test_session_summarization.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context.reinforcement_manager import ContextReinforcementManager

TURNS = [
    ("I search the altar.", "You find a sealed iron box beneath the flagstones."),
    ("Open it.", "Inside: a bone key and a letter naming Baron Vex as the betrayer."),
    ("We ride for Oakhaven.", "Two days later the party reaches the burnt gates of Oakhaven."),
]


def _manager(summary="The party found a bone key and rode to Oakhaven."):
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value=summary)
    llm._reinforcer = MagicMock()
    llm._reinforcer.recent_turns = MagicMock(return_value=list(TURNS))
    manager = ContextReinforcementManager(
        llm_manager=llm,
        state_tracker=MagicMock(),
        foundry_client=MagicMock(),
        db=None,
    )
    for highlight in ("found a bone key", "reached Oakhaven"):
        manager._session_highlights.append(highlight)
    return manager, llm


def test_the_summary_is_written_by_the_model():
    manager, llm = _manager()

    summary = asyncio.run(manager._trigger_summarization())

    llm.generate_text.assert_awaited_once()
    assert "bone key" in summary


def test_the_summary_reaches_the_prompt():
    """session_summary is injected into the reinforcement block; a summary
    that never lands there changes nothing."""
    manager, llm = _manager()

    asyncio.run(manager._trigger_summarization())

    llm._reinforcer.update_session_summary.assert_called_once()
    assert "bone key" in llm._reinforcer.update_session_summary.call_args.args[0]


def test_summarising_never_replays_the_conversation_history():
    """A summary call that carried history would grow with what it is meant
    to be compressing, and could recurse into the turn that triggered it."""
    manager, llm = _manager()

    asyncio.run(manager._trigger_summarization())

    assert not llm.generate.called, "summarisation must not go through the turn path"
    # The kwargs set is the check that matters: `context` is the recent
    # transcript, and anything named for the conversation history would show
    # up as a fourth key. Scanning the transcript text for the word "history"
    # only ever told you what the fixture happened to say.
    kwargs = llm.generate_text.await_args.kwargs
    assert set(kwargs) == {"user_message", "system_prompt", "context"}


def test_a_failed_summary_falls_back_instead_of_breaking_the_session():
    manager, llm = _manager()
    llm.generate_text = AsyncMock(side_effect=RuntimeError("model offline"))

    summary = asyncio.run(manager._trigger_summarization())

    assert summary, "a dead model must not leave the session with no summary"
    assert "Oakhaven" in summary or "Session" in summary


def test_an_empty_model_reply_falls_back_too():
    manager, llm = _manager(summary="   ")

    summary = asyncio.run(manager._trigger_summarization())

    assert summary.strip()


def test_the_reinforcer_exposes_the_turns_to_summarise():
    """The manager needs the actual exchanges, not just its own highlights."""
    from context.reinforcer import ContextReinforcer

    reinforcer = ContextReinforcer()
    reinforcer.record_turn("I search the altar.", "You find a sealed iron box.")

    turns = reinforcer.recent_turns()

    assert turns and turns[0][0] == "I search the altar."


def test_the_keyword_stub_no_longer_overwrites_the_real_summary():
    """try_summarize used to set session_summary itself, so the placeholder
    text raced the model-written one for the same slot."""
    from context.reinforcer import ContextReinforcer

    reinforcer = ContextReinforcer()
    reinforcer.update_session_summary("The party found a bone key and rode to Oakhaven.")
    reinforcer.record_turn("a", "b")
    reinforcer.record_turn("c", "d")
    for _ in range(reinforcer.summarize_every_n_pairs + 2):
        reinforcer.record_turn("x", "y")

    assert "bone key" in reinforcer.session_summary
