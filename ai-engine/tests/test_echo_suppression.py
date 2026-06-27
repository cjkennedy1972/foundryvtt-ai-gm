#!/usr/bin/env python3
"""
Regression test for the AI self-echo re-narration loop.

The relay echoes every message the AI posts (narrate/speak) back as a PUBLIC
chat event. Those echoes carry an EMPTY speaker.alias (Foundry only sets alias
for user-typed messages; the relay puts our name in `author`). When the engine
mistook an echo for player input it fired another LLM turn, producing the same
beat again ~one round-trip later — the "overlapping repeats" seen in play.

_is_player_message must reject:
  - AI echoes (empty speaker alias)
  - GM/Gamemaster-authored messages
while still accepting genuine player chat (non-empty alias).

Run:
    cd ai-engine && python -m pytest tests/test_echo_suppression.py -v
"""

import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import ChatListener


def _make_listener():
    return ChatListener(
        foundry=MagicMock(),
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
    )


def test_ai_narration_echo_is_rejected():
    """A narrate/speak echo (empty speaker alias) must not be treated as input."""
    listener = _make_listener()
    echo = {
        "content": "The Summit Gatehouse stands as a weathered sentinel atop Gravewatch.",
        "speaker": {"actor": None, "scene": None, "token": None},  # no alias
        "author": {"id": "L3tgYXwHmAIPbGvA", "name": "Gamemaster"},
        "whisper": [],
    }
    assert asyncio.run(listener._is_player_message(echo)) is False


def test_gm_authored_message_is_rejected():
    """Even with an alias present, a GM/Gamemaster-authored message is not input."""
    listener = _make_listener()
    msg = {
        "content": "Some GM-side note",
        "speaker": {"alias": "Gamemaster"},
        "author": {"name": "Gamemaster"},
        "whisper": [],
    }
    assert asyncio.run(listener._is_player_message(msg)) is False


def test_real_player_message_is_accepted():
    """A genuine player message (non-empty alias, player author) is accepted."""
    listener = _make_listener()
    msg = {
        "content": "<p>climb onto the catwalk</p>",
        "speaker": {"alias": "Beringar", "actor": "Actor.x", "token": "Token.y"},
        "author": {"name": "PlayerOne"},
        "whisper": [],
    }
    assert asyncio.run(listener._is_player_message(msg)) is True


if __name__ == "__main__":
    test_ai_narration_echo_is_rejected()
    print("PASS  AI narration echo (empty alias) rejected")
    test_gm_authored_message_is_rejected()
    print("PASS  GM/Gamemaster-authored message rejected")
    test_real_player_message_is_accepted()
    print("PASS  genuine player message accepted")
    print("All echo-suppression tests passed.")
