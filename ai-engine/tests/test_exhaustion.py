#!/usr/bin/env python3
"""
execute_set_exhaustion writes the numeric attribute directly and announces
a change; a no-op (already at the clamp) doesn't spam chat.

Run:
    cd ai-engine && python -m pytest tests/test_exhaustion.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex


def _foundry(previous_level, new_level):
    f = AsyncMock()
    f.execute_js = AsyncMock(return_value={
        "result": {"ok": True, "previousLevel": previous_level, "newLevel": new_level}
    })
    f.chat_message = AsyncMock(return_value={"ok": True})
    return f


def test_gaining_exhaustion_announces_the_new_level():
    f = _foundry(previous_level=1, new_level=2)
    out = asyncio.run(ex.execute_set_exhaustion(
        actor_uuid="Actor.beringar", delta=1, reason="a forced march", foundry=f
    ))
    assert out["success"] is True
    assert out["newLevel"] == 2
    f.chat_message.assert_awaited_once()
    prompt = f.chat_message.await_args.args[0]
    assert "level 2" in prompt and "forced march" in prompt


def test_clamped_at_max_does_not_announce_a_change():
    # Already at 6, delta +1 clamps to 6 — no actual change.
    f = _foundry(previous_level=6, new_level=6)
    out = asyncio.run(ex.execute_set_exhaustion(actor_uuid="Actor.beringar", delta=1, foundry=f))
    assert out["success"] is True
    f.chat_message.assert_not_awaited()


def test_recovering_exhaustion_is_announced_too():
    f = _foundry(previous_level=2, new_level=1)
    out = asyncio.run(ex.execute_set_exhaustion(actor_uuid="Actor.beringar", delta=-1, foundry=f))
    assert out["success"] is True
    f.chat_message.assert_awaited_once()
    assert "recovers" in f.chat_message.await_args.args[0]


if __name__ == "__main__":
    for fn in [
        test_gaining_exhaustion_announces_the_new_level,
        test_clamped_at_max_does_not_announce_a_change,
        test_recovering_exhaustion_is_announced_too,
    ]:
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll exhaustion tests passed!")
