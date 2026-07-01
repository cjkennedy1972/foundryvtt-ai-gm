#!/usr/bin/env python3
"""
Regression test: get_scene_details()/get_scene_tokens() with no scene_name must
resolve the active scene, not silently return empty.

Live-verified against a running world: the relay's get-scene has NO default —
calling it with no `name` returns {"error": "Scene not found", "data": None}.
Every caller in this codebase that omits scene_name means "the current scene"
(combat loop, chat listener, reinforcement manager, and critically
place_token's duplicate-token dedup check, which calls get_scene_tokens() with
no name). Because the no-name call always returned [], the dedup check never
found the actor's existing token and place_token created a SECOND token instead
of moving the first — confirmed live: a player's actor ("Beringar") had two
token documents on the same scene.

Run:
    cd ai-engine && python -m pytest tests/test_scene_default_resolution.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient


def _client(active_scene_name="The Ruined Monastery — Courtyard"):
    fc = FoundryClient.__new__(FoundryClient)  # bypass __init__, no real connection

    async def fake_execute_js(code, _timeout=None):
        assert "game.scenes.active" in code
        return {"result": active_scene_name}

    async def fake_send(msg_type, **params):
        assert msg_type == "get-scene"
        assert params.get("name"), "get-scene must always be called with an explicit name"
        return {"data": {"tokens": [{"name": "Beringar", "id": "tok1"}]}}

    fc.execute_js = fake_execute_js
    fc._send = fake_send
    return fc


def test_get_scene_details_resolves_active_scene_when_name_omitted():
    fc = _client()
    result = asyncio.run(fc.get_scene_details())  # no name given
    assert result.get("data", {}).get("tokens")


def test_get_scene_tokens_no_longer_returns_empty_for_current_scene():
    fc = _client()
    tokens = asyncio.run(fc.get_scene_tokens())  # no name given — the broken path
    assert len(tokens) == 1
    assert tokens[0]["name"] == "Beringar"


def test_get_scene_details_passes_through_explicit_name_unchanged():
    """Explicit scene_name still bypasses active-scene resolution entirely."""
    fc = FoundryClient.__new__(FoundryClient)
    fc.execute_js = AsyncMock()  # must NOT be called
    fc._send = AsyncMock(return_value={"data": {"tokens": []}})

    asyncio.run(fc.get_scene_details("Some Other Scene"))
    fc.execute_js.assert_not_called()
    fc._send.assert_awaited_once_with("get-scene", name="Some Other Scene")


def test_get_scene_details_returns_empty_when_no_active_scene_resolvable():
    fc = FoundryClient.__new__(FoundryClient)
    fc.execute_js = AsyncMock(return_value={"result": None})
    fc._send = AsyncMock()

    result = asyncio.run(fc.get_scene_details())
    assert result == {}
    fc._send.assert_not_called()  # never calls get-scene without a name


if __name__ == "__main__":
    test_get_scene_details_resolves_active_scene_when_name_omitted()
    print("PASS  get_scene_details resolves active scene when name omitted")
    test_get_scene_tokens_no_longer_returns_empty_for_current_scene()
    print("PASS  get_scene_tokens no longer empty for current scene")
    test_get_scene_details_passes_through_explicit_name_unchanged()
    print("PASS  explicit scene_name bypasses resolution")
    test_get_scene_details_returns_empty_when_no_active_scene_resolvable()
    print("PASS  no active scene -> empty dict, no bad get-scene call")
    print("\nAll scene-default-resolution tests passed!")
