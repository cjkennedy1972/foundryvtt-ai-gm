#!/usr/bin/env python3
"""
Regression test for the failure-retry re-narration loop.

When an action fails (e.g. update_hp against a hallucinated actor UUID), the
engine asks the LLM to fix it. The model usually re-emits the WHOLE turn,
including narration/dialogue that already played — and re-dispatching it makes
the same beat speak again. In play that surfaced as "the same scene looping
3-4 times with staggered start points" (one repeat per ~15s LLM round-trip).

_notify_llm_of_failures must drop narrate/speak whose text already played this
turn, while still re-issuing the genuinely-failed actions and any new narration.

Run:
    cd ai-engine && python -m pytest tests/test_retry_dedup.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import ChatListener


def _make_listener(retry_actions):
    """Build a ChatListener with mocked collaborators; llm returns retry_actions."""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value={"actions": retry_actions})

    dispatcher = MagicMock()
    dispatcher.execute_batch = AsyncMock(
        side_effect=lambda actions: [{"type": a.get("type"), "success": True} for a in actions]
    )

    state_tracker = MagicMock()
    state_tracker.get_snapshot = MagicMock(return_value="state")

    listener = ChatListener(
        foundry=MagicMock(),
        llm=llm,
        dispatcher=dispatcher,
        state_tracker=state_tracker,
        db=MagicMock(),
    )
    return listener, dispatcher


async def _scenario():
    # A narration that already played this turn, plus the failed action.
    delivered = "The Death Knight's blade screams as it bites into the stone."
    failed_results = [
        {"type": "update_hp", "success": False, "error": "actor not found: Actor.9x8Y7v6u5t4s3r2q"},
    ]
    # The model re-emits the already-played narrate (must be dropped) plus a
    # corrected update_hp and a brand-new line (both must be kept).
    retry_actions = [
        {"type": "narrate", "text": delivered},
        {"type": "update_hp", "actor_uuid": "Actor.IMmMlM4zG7QSuMQ7", "damage": -8},
        {"type": "narrate", "text": "The blow lands true this time."},
    ]
    listener, dispatcher = _make_listener(retry_actions)

    # Simulate that the narration was already delivered this turn.
    await listener._record_sent(delivered)

    await listener._notify_llm_of_failures(failed_results)

    # execute_batch must have run on the filtered set: the re-delivered narrate
    # is gone; the corrected action and the new narration remain.
    assert dispatcher.execute_batch.await_count == 1, "retry should dispatch exactly once"
    dispatched = dispatcher.execute_batch.await_args.args[0]
    types_texts = [(a.get("type"), a.get("text")) for a in dispatched]
    assert ("narrate", delivered) not in types_texts, \
        "already-delivered narration was re-dispatched (re-narration loop)"
    assert ("update_hp", None) in [(a.get("type"), a.get("text")) for a in dispatched], \
        "failed action was not retried"
    assert ("narrate", "The blow lands true this time.") in types_texts, \
        "genuinely-new narration was wrongly dropped"


def test_retry_drops_redelivered_narration():
    asyncio.run(_scenario())


async def _scenario_all_dropped():
    # If the retry is ONLY already-delivered narration, nothing should dispatch.
    delivered = "A cold wind moves through the nave."
    retry_actions = [{"type": "narrate", "text": delivered}]
    listener, dispatcher = _make_listener(retry_actions)
    await listener._record_sent(delivered)
    out = await listener._notify_llm_of_failures(
        [{"type": "narrate", "success": False, "error": "x"}]
    )
    assert dispatcher.execute_batch.await_count == 0, \
        "should not dispatch when every retry action is already-delivered narration"
    assert out == []


def test_retry_all_redelivered_dispatches_nothing():
    asyncio.run(_scenario_all_dropped())


if __name__ == "__main__":
    test_retry_drops_redelivered_narration()
    print("PASS  retry drops re-delivered narration, keeps fixes + new lines")
    test_retry_all_redelivered_dispatches_nothing()
    print("PASS  retry of only re-delivered narration dispatches nothing")
    print("All retry-dedup tests passed.")
