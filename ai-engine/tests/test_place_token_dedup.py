#!/usr/bin/env python3
"""
Regression test: place_token must not spawn a duplicate of an actor already on
the scene — it should move the existing token instead.

Live play kept dropping "another copy of my character" because place_token
always created a new Token document, and token<->actor matching was broken
(get_scene_tokens read 'actorUuid' while Foundry keys it 'actorId', so the uuid
came back empty and nothing matched).

Run:
    cd ai-engine && python -m pytest tests/test_place_token_dedup.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient


def _client(scene_tokens, actors):
    c = FoundryClient()
    c.get_actors = AsyncMock(return_value=actors)
    c.get_scene_tokens = AsyncMock(return_value=scene_tokens)
    c.move_token = AsyncMock(return_value={"ok": True})
    c.canvas_create = AsyncMock(return_value={"created": True})
    c.execute_js = AsyncMock(return_value={"result": None})
    return c


def test_existing_actor_token_is_moved_not_duplicated():
    c = _client(
        scene_tokens=[{"id": "HrfuNyKPqxoO4HZY", "actorUuid": "IMmMlM4zG7QSuMQ7", "name": "Beringar"}],
        actors=[{"name": "Beringar", "uuid": "Actor.IMmMlM4zG7QSuMQ7", "has_player_owner": True}],
    )
    out = asyncio.run(c.place_token("Beringar", 500, 600))
    c.move_token.assert_awaited_once_with("HrfuNyKPqxoO4HZY", 500, 600)
    c.canvas_create.assert_not_called()
    assert out.get("moved") is True


def test_absent_actor_is_created():
    c = _client(
        scene_tokens=[],  # empty scene
        actors=[{"name": "Goblin", "uuid": "Actor.GOB", "has_player_owner": False}],
    )
    asyncio.run(c.place_token("Goblin", 100, 200, disposition=-1))
    c.canvas_create.assert_awaited_once()
    c.move_token.assert_not_called()


if __name__ == "__main__":
    test_existing_actor_token_is_moved_not_duplicated()
    print("PASS  existing actor token moved, not duplicated")
    test_absent_actor_is_created()
    print("PASS  absent actor is created")
    print("All place-token-dedup tests passed.")
