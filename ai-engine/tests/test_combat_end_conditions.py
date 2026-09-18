"""_check_combat_end decides when a fight stops. It was largely untested.

Getting this wrong either strands the table in a combat that will not end, or
cuts a fight short while people are still standing. It also owns the rule that
a PC at 0 HP is unconscious rather than removed, which is what gives them a
death save on their own turn.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from combat.loop import CombatLoop


def _loop(npcs=(), pcs=()):
    loop = CombatLoop.__new__(CombatLoop)
    loop.foundry = AsyncMock()
    loop._npc_tokens = list(npcs)
    loop._pc_tokens = list(pcs)
    loop._dead_pc_tokens = {}
    return loop


def _token(tid, name, hp, uuid=None):
    return {
        "id": tid, "name": name, "actorUuid": uuid or f"Actor.{tid}",
        "data": {"attributes": {"hp": {"value": hp}}},
    }


@pytest.mark.asyncio
async def test_combat_continues_while_both_sides_stand():
    loop = _loop(npcs=[_token("n1", "Goblin", 7)], pcs=[_token("p1", "Thalia", 12)])
    loop.foundry.get_scene_tokens.return_value = [
        _token("n1", "Goblin", 7), _token("p1", "Thalia", 12),
    ]

    assert await loop._check_combat_end() is False


@pytest.mark.asyncio
async def test_combat_ends_when_every_npc_is_down():
    loop = _loop(npcs=[_token("n1", "Goblin", 7)], pcs=[_token("p1", "Thalia", 12)])
    loop.foundry.get_scene_tokens.return_value = [
        _token("n1", "Goblin", 0), _token("p1", "Thalia", 12),
    ]

    assert await loop._check_combat_end() is True


@pytest.mark.asyncio
async def test_an_empty_encounter_does_not_end_combat_spuriously():
    """had_npc/had_pc are captured before the lists are rewritten; without
    that, a fight with no NPCs would read as 'all NPCs defeated'."""
    loop = _loop(npcs=[], pcs=[_token("p1", "Thalia", 12)])
    loop.foundry.get_scene_tokens.return_value = [_token("p1", "Thalia", 12)]

    assert await loop._check_combat_end() is False


@pytest.mark.asyncio
async def test_a_pc_at_zero_hp_stays_in_the_fight_pending_a_death_save():
    """Removing them immediately would skip the death save entirely."""
    loop = _loop(npcs=[_token("n1", "Goblin", 7)], pcs=[_token("p1", "Thalia", 0)])
    loop.foundry.get_scene_tokens.return_value = [
        _token("n1", "Goblin", 7), _token("p1", "Thalia", 0),
    ]
    loop.foundry.execute_js.return_value = {"result": {"isDead": False, "isStable": False}}

    ended = await loop._check_combat_end()

    assert ended is False
    assert [t["id"] for t in loop._pc_tokens] == ["p1"]
    assert loop._dead_pc_tokens == {}


@pytest.mark.asyncio
async def test_a_confirmed_dead_pc_moves_to_the_death_save_queue_by_name():
    loop = _loop(npcs=[_token("n1", "Goblin", 7)], pcs=[_token("p1", "Thalia", 0)])
    loop.foundry.get_scene_tokens.return_value = [
        _token("n1", "Goblin", 7), _token("p1", "Thalia", 0),
    ]
    loop.foundry.execute_js.return_value = {"result": {"isDead": True, "isStable": False}}

    await loop._check_combat_end()

    assert loop._dead_pc_tokens == {"p1": "Thalia"}
    assert loop._pc_tokens == []


@pytest.mark.asyncio
async def test_a_stable_pc_also_leaves_the_active_list():
    loop = _loop(npcs=[_token("n1", "Goblin", 7)], pcs=[_token("p1", "Thalia", 0)])
    loop.foundry.get_scene_tokens.return_value = [
        _token("n1", "Goblin", 7), _token("p1", "Thalia", 0),
    ]
    loop.foundry.execute_js.return_value = {"result": {"isDead": False, "isStable": True}}

    await loop._check_combat_end()

    assert "p1" in loop._dead_pc_tokens


@pytest.mark.asyncio
async def test_an_unreadable_death_save_keeps_the_pc_dying(caplog):
    """Failing open here would kill a PC on a relay hiccup."""
    loop = _loop(npcs=[_token("n1", "Goblin", 7)], pcs=[_token("p1", "Thalia", 0)])
    loop.foundry.get_scene_tokens.return_value = [
        _token("n1", "Goblin", 7), _token("p1", "Thalia", 0),
    ]
    loop.foundry.execute_js.side_effect = ConnectionError("relay down")

    with caplog.at_level(logging.WARNING):
        await loop._check_combat_end()

    assert loop._dead_pc_tokens == {}
    assert [t["id"] for t in loop._pc_tokens] == ["p1"]
    assert any("Death-save status unreadable" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_a_failed_refresh_prunes_tokens_foundry_no_longer_has():
    """Otherwise a deleted token stays in the turn order as a zombie."""
    loop = _loop(npcs=[_token("n1", "Goblin", 7), _token("n2", "Ghost", 5)],
                 pcs=[_token("p1", "Thalia", 12)])
    loop.foundry.get_scene_tokens.side_effect = [
        ConnectionError("relay hiccup"),
        [_token("n1", "Goblin", 7), _token("p1", "Thalia", 12)],
    ]

    ended = await loop._check_combat_end()

    assert ended is False
    assert [t["id"] for t in loop._npc_tokens] == ["n1"], "the missing token should be pruned"


@pytest.mark.asyncio
async def test_a_failed_prune_keeps_the_lists_rather_than_emptying_them(caplog):
    """The same outage fails both RPCs; emptying the lists would end combat."""
    loop = _loop(npcs=[_token("n1", "Goblin", 7)], pcs=[_token("p1", "Thalia", 12)])
    loop.foundry.get_scene_tokens.side_effect = ConnectionError("relay down")

    with caplog.at_level(logging.WARNING):
        ended = await loop._check_combat_end()

    assert ended is False
    assert len(loop._npc_tokens) == 1 and len(loop._pc_tokens) == 1
