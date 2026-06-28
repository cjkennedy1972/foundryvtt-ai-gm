#!/usr/bin/env python3
"""
Regression test: the GM must not roll for player characters.

Logs showed the AI auto-rolling '1d20+3 by Beringar' (a PC) — players never got
to roll, which is the point of D&D. execute_roll must defer PC rolls (prompt the
player) while still rolling for NPCs/monsters.

Run:
    cd ai-engine && python -m pytest tests/test_player_rolls.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex


def _foundry():
    f = AsyncMock()
    f.get_actors = AsyncMock(return_value=[
        {"name": "Beringar", "has_player_owner": True},
        {"name": "Skeleton", "has_player_owner": False},
    ])
    f.roll = AsyncMock(return_value={"result": 17, "total": 17})
    f.chat_message = AsyncMock(return_value={"ok": True})
    return f


def setup_function(_):
    # Reset the PC-name cache between tests.
    ex._pc_names_cache = set()
    ex._pc_names_cache_at = 0.0


def test_pc_roll_is_deferred_to_player():
    f = _foundry()
    out = asyncio.run(ex.execute_roll("1d20+3", speaker="Beringar", foundry=f))
    assert out.get("deferred_to_player") is True
    f.roll.assert_not_called()              # GM did NOT roll for the PC
    f.chat_message.assert_awaited_once()     # player was prompted to roll


def test_npc_roll_still_rolls():
    f = _foundry()
    out = asyncio.run(ex.execute_roll("1d20+4", speaker="Skeleton", foundry=f))
    assert out.get("deferred_to_player") is None
    f.roll.assert_awaited()                  # GM rolls for the monster


if __name__ == "__main__":
    setup_function(None)
    test_pc_roll_is_deferred_to_player()
    print("PASS  PC roll deferred to player")
    setup_function(None)
    test_npc_roll_still_rolls()
    print("PASS  NPC roll still rolls")
    print("All player-roll tests passed.")
