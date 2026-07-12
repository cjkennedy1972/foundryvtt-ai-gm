#!/usr/bin/env python3
"""
short_rest / long_rest: one relay call per party member, aggregated, and a
failure on one actor doesn't stop the rest of the party from resting.

Run:
    cd ai-engine && python -m pytest tests/test_rest_actions.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex


def _foundry():
    f = AsyncMock()
    f.request_short_rest = AsyncMock(return_value={"ok": True})
    f.request_long_rest = AsyncMock(return_value={"ok": True})
    f.chat_message = AsyncMock(return_value={"ok": True})
    return f


def test_short_rest_calls_relay_once_per_actor():
    f = _foundry()
    out = asyncio.run(ex.execute_short_rest(
        actor_uuids=["Actor.a", "Actor.b"], foundry=f
    ))
    assert out["success"] is True
    assert f.request_short_rest.await_count == 2
    f.request_short_rest.assert_any_await("Actor.a")
    f.request_short_rest.assert_any_await("Actor.b")
    f.chat_message.assert_awaited_once()


def test_long_rest_calls_relay_once_per_actor():
    f = _foundry()
    out = asyncio.run(ex.execute_long_rest(
        actor_uuids=["Actor.a", "Actor.b", "Actor.c"], foundry=f
    ))
    assert out["success"] is True
    assert f.request_long_rest.await_count == 3


def test_one_actor_failing_does_not_block_the_rest_of_the_party():
    f = _foundry()
    f.request_short_rest = AsyncMock(side_effect=[Exception("relay timeout"), {"ok": True}])
    out = asyncio.run(ex.execute_short_rest(
        actor_uuids=["Actor.a", "Actor.b"], foundry=f
    ))
    assert out["success"] is False  # not everyone rested
    results = out["results"]
    assert results[0]["success"] is False and "relay timeout" in results[0]["error"]
    assert results[1]["success"] is True
    f.chat_message.assert_awaited_once()  # still announced, doesn't blow up


if __name__ == "__main__":
    for fn in [
        test_short_rest_calls_relay_once_per_actor,
        test_long_rest_calls_relay_once_per_actor,
        test_one_actor_failing_does_not_block_the_rest_of_the_party,
    ]:
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll rest-action tests passed!")
