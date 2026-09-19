#!/usr/bin/env python3
"""The session summary quietly forgot the session.

The GM's anti-drift summary is meant to be what the model relies on after the
early turns have scrolled out of the window. Two faults met in the middle and
made it much less than that.

1. ContextReinforcer._trigger_summarization halves its conversation log to
   keep it bounded, slicing at `len // 2`. The log alternates user, assistant,
   so an odd slice point leaves it starting on an assistant message.
   recent_turns() pairs strictly from even indices and requires a user message
   first, so a misaligned log yields NO pairs at all. Measured across 100
   turns, the log length runs 10, 15, 18, 19, 20, 20... and recent_turns()
   returns 5, 0, 9, 0, 10, 10 pairs. The summariser saw nothing at turn 20 and
   again at turn 40, and fell back to the "Session duration: N minutes" stub
   both times — early in a session, which is exactly when continuity is being
   established.

2. _write_summary prompts the model with "Summarise the session so far" and
   hands it only the surviving transcript. The previous summary is not
   included, and the pruning above holds the log at roughly ten exchanges, so
   each pass overwrote everything the last one knew. SUMMARY_MAX_TURNS = 40
   never came close to binding.

Run:
    cd ai-engine && python -m pytest tests/test_session_summary_retention.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context.reinforcement_manager import ContextReinforcementManager
from context.reinforcer import ContextReinforcer


# ── the log stays paired ──────────────────────────────────────────────────

def test_the_log_never_goes_blind_after_a_prune():
    """recent_turns() returning [] sends the summariser to the stub."""
    reinforcer = ContextReinforcer()

    blind = []
    for i in range(1, 101):
        reinforcer.record_turn(f"player {i}", f"gm {i}")
        if not reinforcer.recent_turns():
            blind.append(i)

    assert blind == [], f"the summariser saw no turns at all on turns {blind}"


def test_a_prune_keeps_the_log_starting_on_a_player_message():
    reinforcer = ContextReinforcer()
    for i in range(1, 41):
        reinforcer.record_turn(f"player {i}", f"gm {i}")
        log = list(reinforcer._conversation_log)
        assert log[0]["role"] == "user", (
            f"turn {i}: the log starts on {log[0]['role']}, so every pair is offset"
        )
        assert len(log) % 2 == 0, f"turn {i}: {len(log)} messages is half a pair"


def test_a_prune_drops_the_oldest_turns_not_the_newest():
    reinforcer = ContextReinforcer()
    for i in range(1, 31):
        reinforcer.record_turn(f"player {i}", f"gm {i}")

    kept = [u for u, _ in reinforcer.recent_turns()]

    assert "player 30" in kept, "the most recent exchange was pruned"
    assert "player 1" not in kept, "nothing was pruned at all"


# ── the summary carries forward ───────────────────────────────────────────

def _manager(previous_summary=""):
    llm = MagicMock()
    llm.generate = AsyncMock()
    llm.generate_text = AsyncMock(return_value="Halda owes the party a favour.")
    reinforcer = ContextReinforcer()
    reinforcer.session_summary = previous_summary
    llm._reinforcer = reinforcer
    manager = ContextReinforcementManager(
        llm_manager=llm, state_tracker=MagicMock(), foundry_client=MagicMock(), db=None
    )
    return manager, llm, reinforcer


def test_the_summariser_is_given_what_the_last_pass_learned():
    """Otherwise hour three of a session describes only the last ten
    exchanges and calls it "the session so far"."""
    manager, llm, reinforcer = _manager(
        previous_summary="The party burned the bone key in the chapel brazier."
    )
    reinforcer.record_turn("I search the altar.", "You find a sealed iron box.")

    asyncio.run(manager._write_summary())

    sent = " ".join(str(v) for v in llm.generate_text.await_args.kwargs.values())
    assert "bone key" in sent, "the previous summary was not carried forward"
    assert "sealed iron box" in sent, "the newest exchange was not included"


def test_the_first_pass_has_no_previous_summary_to_carry():
    manager, llm, reinforcer = _manager()
    reinforcer.record_turn("I knock.", "The door opens.")

    summary = asyncio.run(manager._write_summary())

    assert summary == "Halda owes the party a favour."
    sent = " ".join(str(v) for v in llm.generate_text.await_args.kwargs.values())
    assert "The door opens." in sent


def test_a_long_session_still_reaches_the_model_not_the_stub():
    """The stub says "Session duration: N minutes" and nothing else."""
    manager, llm, reinforcer = _manager()
    for i in range(1, 61):
        reinforcer.record_turn(f"player {i}", f"gm {i}")

    summary = asyncio.run(manager._write_summary())

    assert llm.generate_text.await_count == 1, "the summariser fell back to the stub"
    assert "Session duration" not in summary
