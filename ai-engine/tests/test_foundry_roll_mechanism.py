#!/usr/bin/env python3
"""
Regression test: rolls must go through Foundry's real roll-to-chat path so a
3D dice addon (Dice So Nice) animates them.

Logs showed the relay doing silent manual rolls (chatMessageCreated:false) that
no dice addon sees. foundry.roll must set createChatMessage=True, and
advantage/disadvantage must be a single 2d20kh1/kl1 Foundry roll (one animation,
correct die kept) rather than two separate silent rolls.

Run:
    cd ai-engine && python -m pytest tests/test_foundry_roll_mechanism.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex
from actions.executors import _advantage_formula
from foundry.client import FoundryClient


def test_roll_sets_create_chat_message():
    c = FoundryClient()
    c._send = AsyncMock(return_value={"total": 17})
    asyncio.run(c.roll("1d20+5", speaker="Skeleton"))
    _, kwargs = c._send.await_args
    assert kwargs.get("createChatMessage") is True


def test_advantage_formula():
    assert _advantage_formula("1d20+3", True) == "2d20kh1+3"
    assert _advantage_formula("1d20+3", False) == "2d20kl1+3"
    assert _advantage_formula("1d20", True) == "2d20kh1"
    assert _advantage_formula("2d6+3", None) == "2d6+3"   # no change without advantage


def test_npc_advantage_is_single_roll():
    # Reset PC cache; Skeleton is not a PC so it rolls.
    ex._pc_names_cache, ex._pc_names_cache_at = set(), 0.0
    f = AsyncMock()
    f.get_actors = AsyncMock(return_value=[{"name": "Skeleton", "has_player_owner": False}])
    f.roll = AsyncMock(return_value={"total": 18})
    asyncio.run(ex.execute_roll("1d20+6", speaker="Skeleton", advantage=True, foundry=f))
    f.roll.assert_awaited_once()                 # ONE roll, not two
    assert f.roll.await_args.args[0] == "2d20kh1+6"


if __name__ == "__main__":
    test_roll_sets_create_chat_message()
    test_advantage_formula()
    test_npc_advantage_is_single_roll()
    print("All foundry-roll-mechanism tests passed.")
