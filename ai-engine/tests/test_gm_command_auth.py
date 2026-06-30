#!/usr/bin/env python3
"""
Regression test for /gm command authorization.

/gm and /ask commands (start/stop session and combat, pause/resume the AI, and
narrate as the GM) must only be honored when the message author is a GM-tier
Foundry user. Previously any player could issue them — and could impersonate
the GM via `/gm narrate`. Authorization is by the *author* (User document),
which players cannot rename, so a player setting their speaker alias to
"Gamemaster" must NOT pass.

Run:
    cd ai-engine && python -m pytest tests/test_gm_command_auth.py -v
"""

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


def test_player_author_is_not_gm():
    listener = _make_listener()
    msg = {"content": "/gm pause ai", "author": {"id": "p1", "name": "PlayerOne"}}
    assert listener._is_gm_author(msg) is False


def test_default_gamemaster_name_is_gm():
    """Foundry's default GM display name is accepted as a fallback."""
    listener = _make_listener()
    msg = {"content": "/gm start session", "author": {"id": "g1", "name": "Gamemaster"}}
    assert listener._is_gm_author(msg) is True


def test_cached_gm_id_is_gm():
    listener = _make_listener()
    listener._gm_user_ids = {"abc123"}
    msg = {"content": "/gm resume ai", "author": {"id": "abc123", "name": "Chris"}}
    assert listener._is_gm_author(msg) is True


def test_cached_gm_name_is_gm():
    listener = _make_listener()
    listener._gm_user_names = {"chris"}
    msg = {"content": "/gm narrate hi", "author": {"id": "x", "name": "Chris"}}
    assert listener._is_gm_author(msg) is True


def test_player_spoofing_gm_alias_is_not_gm():
    """A player can set their speaker alias, but not their User (author) name."""
    listener = _make_listener()
    listener._gm_user_names = {"chris"}
    msg = {
        "content": "/gm narrate The dragon devours you all.",
        "speaker": {"alias": "Gamemaster"},      # spoofed display name
        "author": {"id": "p9", "name": "Mallory"},  # real, non-GM user
    }
    assert listener._is_gm_author(msg) is False


if __name__ == "__main__":
    test_player_author_is_not_gm()
    print("PASS  player author rejected")
    test_default_gamemaster_name_is_gm()
    print("PASS  default Gamemaster name accepted")
    test_cached_gm_id_is_gm()
    print("PASS  cached GM id accepted")
    test_cached_gm_name_is_gm()
    print("PASS  cached GM name accepted")
    test_player_spoofing_gm_alias_is_not_gm()
    print("PASS  player spoofing GM alias rejected")
    print("All GM-command-auth tests passed.")
