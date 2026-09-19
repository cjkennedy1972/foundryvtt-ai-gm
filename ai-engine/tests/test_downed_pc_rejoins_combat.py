#!/usr/bin/env python3
"""Healing Word on a downed ally left them conscious and out of the fight.

_check_combat_end rebuilds _pc_tokens by filtering the list it already has.
A PC at 0 HP who is dead or stable is moved to _dead_pc_tokens and dropped
from _pc_tokens — and the list only ever shrinks, so nothing puts them back.

Stabilising a downed ally is routine: Spare the Dying is a cantrip, and three
death-save successes do it on their own. Healing Word then brings them up.
Driving the real method:

    after stabilise   _pc_tokens = []   queue = {'pc1': 'Aria'}
    after healing     _pc_tokens = []   queue = {'pc1': 'Aria'}

Aria is at 5 HP and holds a slot in _turn_order. Each round the loop builds
her a stand-in token from the queue, which carries a name and no actorUuid
and no HP, so _maybe_death_save reads 0, logs "Aria is down — skipping turn",
and she never acts again for the rest of the combat.

Run:
    cd ai-engine && python -m pytest tests/test_downed_pc_rejoins_combat.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.loop import CombatLoop

ARIA = {"id": "pc1", "name": "Aria", "actorUuid": "Actor.aria", "hp": 12}
ORC = {"id": "orc", "name": "Orc", "actorUuid": "Actor.orc", "hp": 15}


def _loop(hp, *, is_dead=False, is_stable=False):
    foundry = AsyncMock()
    foundry.get_scene_tokens = AsyncMock(return_value=[dict(ARIA, hp=hp), dict(ORC)])
    foundry.execute_js = AsyncMock(
        return_value={"result": {"isDead": is_dead, "isStable": is_stable}}
    )
    loop = CombatLoop(
        foundry=foundry, llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    )
    loop._pc_tokens = [dict(ARIA)]
    loop._npc_tokens = [dict(ORC)]
    loop._turn_order = ["pc1", "orc"]
    return loop, foundry


def _heal(loop, foundry, hp):
    foundry.get_scene_tokens = AsyncMock(return_value=[dict(ARIA, hp=hp), dict(ORC)])
    asyncio.run(loop._check_combat_end())


def _names(loop):
    return [t["name"] for t in loop._pc_tokens]


# ── coming back ───────────────────────────────────────────────────────────

def test_a_stabilised_pc_healed_above_zero_rejoins_the_turn_order():
    loop, foundry = _loop(0, is_stable=True)
    asyncio.run(loop._check_combat_end())
    assert _names(loop) == [], "precondition: a stable PC leaves the active list"

    _heal(loop, foundry, 5)

    assert _names(loop) == ["Aria"], "Healing Word left her out of the fight"


def test_coming_back_clears_the_downed_queue():
    loop, foundry = _loop(0, is_stable=True)
    asyncio.run(loop._check_combat_end())

    _heal(loop, foundry, 5)

    assert "pc1" not in loop._dead_pc_tokens


def test_a_revived_pc_rejoins():
    """Revivify puts a dead character back on 1 HP."""
    loop, foundry = _loop(0, is_dead=True)
    asyncio.run(loop._check_combat_end())

    _heal(loop, foundry, 1)

    assert _names(loop) == ["Aria"]


def test_the_restored_token_carries_its_actor_uuid():
    """The stand-in built from the queue had a name and nothing else, so
    every later HP or death-save read on it was blind."""
    loop, foundry = _loop(0, is_stable=True)
    asyncio.run(loop._check_combat_end())

    _heal(loop, foundry, 5)

    assert loop._pc_tokens[0]["actorUuid"] == "Actor.aria"
    assert loop._pc_tokens[0]["hp"] == 5


# ── staying out ───────────────────────────────────────────────────────────

def test_a_stabilised_pc_still_at_zero_stays_out():
    loop, foundry = _loop(0, is_stable=True)
    asyncio.run(loop._check_combat_end())

    _heal(loop, foundry, 0)

    assert _names(loop) == []
    assert "pc1" in loop._dead_pc_tokens


def test_a_dying_pc_keeps_its_turn_and_its_real_token():
    """Still dying means still rolling death saves, which needs the uuid."""
    loop, _ = _loop(0, is_dead=False, is_stable=False)

    asyncio.run(loop._check_combat_end())

    assert _names(loop) == ["Aria"]
    assert loop._pc_tokens[0]["actorUuid"] == "Actor.aria"
    assert loop._dead_pc_tokens == {}


def test_combat_does_not_end_while_a_downed_pc_is_being_healed():
    loop, foundry = _loop(0, is_stable=True)
    ended_while_down = asyncio.run(loop._check_combat_end())
    assert ended_while_down is True, "precondition: the last PC going down ends it"

    foundry.get_scene_tokens = AsyncMock(return_value=[dict(ARIA, hp=5), dict(ORC)])
    assert asyncio.run(loop._check_combat_end()) is False


def test_a_queued_pcs_turn_token_carries_its_actor_uuid():
    """_process_turns builds a stand-in from the queue when a downed PC's slot
    comes round. It used to hold only the name, so _maybe_death_save took the
    no-uuid branch and read HP off a token that had none."""
    loop, _ = _loop(0, is_dead=True)
    asyncio.run(loop._check_combat_end())

    queued = loop._dead_pc_tokens["pc1"]

    assert queued["actorUuid"] == "Actor.aria"
    assert queued["name"] == "Aria"
