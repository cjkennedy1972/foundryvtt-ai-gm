#!/usr/bin/env python3
"""
Regression tests for two encounter-breaking bugs found in the logs:

1. place_token rejected every enemy because the LLM passed `uuid` and the
   schema forbade it (extra_forbidden). The schema must accept actor_name OR
   uuid, and the client must resolve an actor by uuid.
2. skill_check errored 'Unknown message type: request-skill-check'. The relay
   type is 'skill-check' with camelCase actorUuid + a skill abbreviation.

Run:
    cd ai-engine && python -m pytest tests/test_placetoken_uuid_skillcheck.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.schemas import PlaceTokenAction
from foundry.client import FoundryClient


def test_schema_accepts_uuid_without_name():
    a = PlaceTokenAction(uuid="Actor.jBqzidtI86kjW1r4", x=100, y=200, disposition=-1)
    assert a.uuid.endswith("jBqzidtI86kjW1r4") and a.actor_name is None


def test_schema_requires_an_identifier():
    with pytest.raises(Exception):
        PlaceTokenAction(x=100, y=200)


def test_place_token_resolves_by_uuid():
    c = FoundryClient()
    c.get_actors = AsyncMock(return_value=[
        {"name": "Skeleton", "uuid": "Actor.jBqzidtI86kjW1r4"},
    ])
    c.get_scene_tokens = AsyncMock(return_value=[])   # not yet on scene
    c.execute_js = AsyncMock(return_value={"result": None})
    c.canvas_create = AsyncMock(return_value={"created": True})
    c.move_token = AsyncMock()
    asyncio.run(c.place_token(uuid="Actor.jBqzidtI86kjW1r4", x=900, y=400, disposition=-1))
    c.canvas_create.assert_awaited_once()
    token_data = c.canvas_create.await_args.args[1]
    assert token_data["name"] == "Skeleton"           # resolved from uuid


def test_skill_check_uses_correct_message_and_abbrev():
    c = FoundryClient()
    c._send = AsyncMock(return_value={"ok": True})
    asyncio.run(c.request_skill_check("Actor.PC", "Athletics", dc=15))
    args, kwargs = c._send.await_args
    assert args[0] == "skill-check"
    assert kwargs.get("actorUuid") == "Actor.PC"
    assert kwargs.get("skill") == "ath"               # name -> abbreviation


if __name__ == "__main__":
    test_schema_accepts_uuid_without_name()
    test_schema_requires_an_identifier()
    test_place_token_resolves_by_uuid()
    test_skill_check_uses_correct_message_and_abbrev()
    print("All place-token-uuid / skill-check tests passed.")
