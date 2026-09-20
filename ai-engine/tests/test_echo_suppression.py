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


def _oocsg(author, **extra):
    """A player's own out-of-character message in v14: no speaker.alias at all."""
    return {"content": "<p>what enemies can I see?</p>",
            "speaker": {"actor": None, "scene": None, "token": None},
            "author": author, "whisper": [], **extra}


def test_a_players_alias_less_ooc_message_is_accepted_once_roles_are_loaded():
    """Real player chat in v14 has no speaker.alias. Requiring one meant the AI GM
    ignored every actual player; only the AI's own (GM-tier) posts stay dropped."""
    listener = _make_listener()
    listener._gm_user_ids = {"gm-id", "ai-id"}
    listener._gm_user_names = {"gamemaster", "ai-gm"}

    assert asyncio.run(listener._is_player_message(_oocsg({"id": "chris-id", "name": "Chris"}))) is True
    assert listener._speaker_name(_oocsg({"id": "chris-id", "name": "Chris"})) == "Chris"
    # the relay's own post: alias-less, authored by the GM-tier AI user
    assert asyncio.run(listener._is_player_message(_oocsg({"id": "ai-id", "name": "ai-gm"}))) is False


def test_an_alias_less_message_is_dropped_while_the_role_list_is_unloaded():
    listener = _make_listener()
    assert not (listener._gm_user_ids or listener._gm_user_names)

    assert asyncio.run(listener._is_player_message(_oocsg({"id": "chris-id", "name": "Chris"}))) is False


def test_plain_text_strips_the_html_wrapper_but_not_comparison_signs():
    from foundry.chat_listener import _plain_text

    assert _plain_text("<p>Grazen &amp; co look around</p>") == "Grazen & co look around"
    assert _plain_text("if HP < 5 and AC > 10, retreat") == "if HP < 5 and AC > 10, retreat"


if __name__ == "__main__":
    test_ai_narration_echo_is_rejected()
    print("PASS  AI narration echo (empty alias) rejected")
    test_gm_authored_message_is_rejected()
    print("PASS  GM/Gamemaster-authored message rejected")
    test_real_player_message_is_accepted()
    print("PASS  genuine player message accepted")
    print("All echo-suppression tests passed.")
