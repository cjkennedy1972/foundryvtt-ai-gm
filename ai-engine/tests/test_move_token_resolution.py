#!/usr/bin/env python3
"""
Regression test: move_token must resolve actor-uuid/name to a scene token id,
and the dispatcher must surface a relay 'error' result as a failure.

Live play showed the AI emit move_token with token_id='Actor.IMmMlM4zG7QSuMQ7'
(an actor uuid), which the relay rejected with 'Entity not found: undefined' —
yet the dispatcher reported success=True (the relay error dict had an 'error'
key but no success flag), so the failure was never retried.

Run:
    cd ai-engine && python -m pytest tests/test_move_token_resolution.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.dispatcher import ActionDispatcher

SCENE_TOKENS = [
    {"id": "HrfuNyKPqxoO4HZY", "actorUuid": "Actor.IMmMlM4zG7QSuMQ7", "name": "Beringar"},
]


def _dispatcher(update_result):
    foundry = SimpleNamespace(
        get_scene_tokens=AsyncMock(return_value=SCENE_TOKENS),
        update_entity=AsyncMock(return_value=update_result),
    )
    return ActionDispatcher(foundry), foundry


def test_move_token_resolves_actor_uuid_to_token_id():
    dispatcher, foundry = _dispatcher({"success": True})
    asyncio.run(dispatcher.execute({
        "type": "move_token", "token_id": "Actor.IMmMlM4zG7QSuMQ7", "x": 100, "y": 200,
    }))
    # The relay update must target the real scene token id, not the actor uuid.
    _, kwargs = foundry.update_entity.await_args
    assert kwargs.get("token_id") == "HrfuNyKPqxoO4HZY"


def test_move_token_resolves_by_name():
    dispatcher, foundry = _dispatcher({"success": True})
    asyncio.run(dispatcher.execute({
        "type": "move_token", "token_id": "Beringar", "x": 50, "y": 60,
    }))
    _, kwargs = foundry.update_entity.await_args
    assert kwargs.get("token_id") == "HrfuNyKPqxoO4HZY"


def test_relay_error_result_surfaces_as_failure():
    # Relay error shape: has "error" but NO success flag — must NOT be success.
    dispatcher, _ = _dispatcher(
        {"error": "Entity not found: undefined", "type": "update-result"}
    )
    out = asyncio.run(dispatcher.execute({
        "type": "move_token", "token_id": "HrfuNyKPqxoO4HZY", "x": 1, "y": 2,
    }))
    assert out.get("success") is False
    assert "Entity not found" in str(out.get("error", ""))


if __name__ == "__main__":
    test_move_token_resolves_actor_uuid_to_token_id()
    print("PASS  move_token resolves actor uuid -> token id")
    test_move_token_resolves_by_name()
    print("PASS  move_token resolves name -> token id")
    test_relay_error_result_surfaces_as_failure()
    print("PASS  relay error result surfaces as failure")
    print("All move-token-resolution tests passed.")
