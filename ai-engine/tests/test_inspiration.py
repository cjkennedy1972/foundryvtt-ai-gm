#!/usr/bin/env python3
"""
execute_grant_inspiration announces the grant once, but not redundantly if
the actor already had inspiration.

Run:
    cd ai-engine && python -m pytest tests/test_inspiration.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex


def _foundry(already_had):
    f = AsyncMock()
    f.execute_js = AsyncMock(return_value={"result": {"ok": True, "alreadyHad": already_had}})
    f.chat_message = AsyncMock(return_value={"ok": True})
    f.get_actors = AsyncMock(return_value=[
        {"name": "Beringar", "uuid": "Actor.beringar", "has_player_owner": True},
    ])
    return f


def setup_function(_):
    ex._pc_uuid_cache = {}
    ex._pc_uuid_cache_at = 0.0


def test_grant_announces_with_reason():
    f = _foundry(already_had=False)
    out = asyncio.run(ex.execute_grant_inspiration(
        actor_uuid="Actor.beringar", reason="a brilliant bluff", foundry=f
    ))
    assert out["success"] is True
    f.chat_message.assert_awaited_once()
    prompt = f.chat_message.await_args.args[0]
    assert "Beringar" in prompt and "brilliant bluff" in prompt


def test_already_had_inspiration_does_not_re_announce():
    f = _foundry(already_had=True)
    out = asyncio.run(ex.execute_grant_inspiration(actor_uuid="Actor.beringar", foundry=f))
    assert out["success"] is True
    f.chat_message.assert_not_awaited()


if __name__ == "__main__":
    for fn in [test_grant_announces_with_reason, test_already_had_inspiration_does_not_re_announce]:
        setup_function(None)
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll inspiration tests passed!")
