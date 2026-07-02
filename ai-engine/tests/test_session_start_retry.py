#!/usr/bin/env python3
"""
Regression test: a failed session_start beat retries once.

The session opening (setup_scene + PC token placement) died on a transient
LLM 400 and was swallowed by the error handler, leaving the table with no
visible scene. Idle beats stay fire-and-forget; only session_start retries.

Run:
    cd ai-engine && python -m pytest tests/test_session_start_retry.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import ChatListener


def _listener_with_failing_snapshot():
    """Listener whose beat body fails immediately (snapshot raises)."""
    listener = ChatListener.__new__(ChatListener)
    listener.state_tracker = SimpleNamespace(
        get_snapshot=MagicMock(side_effect=RuntimeError("LLM down"))
    )
    return listener


def test_session_start_retries_once():
    listener = _listener_with_failing_snapshot()
    asyncio.run(listener._run_proactive_action("session_start"))
    # 1 original + 1 retry, then stop — no infinite loop
    assert listener.state_tracker.get_snapshot.call_count == 2


def test_idle_beat_does_not_retry():
    listener = _listener_with_failing_snapshot()
    asyncio.run(listener._run_proactive_action("idle"))
    assert listener.state_tracker.get_snapshot.call_count == 1


if __name__ == "__main__":
    test_session_start_retries_once()
    print("PASS  session_start retries once on failure")
    test_idle_beat_does_not_retry()
    print("PASS  idle beats do not retry")
    print("All session start retry tests passed.")
