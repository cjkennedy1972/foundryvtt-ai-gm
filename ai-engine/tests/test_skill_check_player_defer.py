#!/usr/bin/env python3
"""
Regression test: the GM must not roll skill checks for player characters either.

execute_roll already deferred PC rolls (see test_player_rolls.py), but
execute_skill_check called foundry.request_skill_check unconditionally for ANY
actor_uuid — including PCs. request_skill_check auto-rolls server-side (applies
proficiency/expertise and returns a result directly; no player interaction),
so any LLM-issued skill_check against a player's actor silently rolled for
them, bypassing "players roll their own dice" entirely. execute_skill_check
must defer to the player exactly like execute_roll does; NPCs/monsters still
auto-roll via request_skill_check.

Run:
    cd ai-engine && python -m pytest tests/test_skill_check_player_defer.py -v
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
        {"name": "Beringar", "uuid": "Actor.IMmMlM4zG7QSuMQ7", "has_player_owner": True},
        {"name": "Goblin", "uuid": "Actor.gobxyz", "has_player_owner": False},
    ])
    f.request_skill_check = AsyncMock(return_value={"total": 15, "success": True})
    f.chat_message = AsyncMock(return_value={"ok": True})
    return f


def setup_function(_):
    ex._pc_uuid_cache = {}
    ex._pc_uuid_cache_at = 0.0


def test_pc_skill_check_is_deferred_to_player():
    f = _foundry()
    out = asyncio.run(ex.execute_skill_check(
        actor_uuid="Actor.IMmMlM4zG7QSuMQ7", skill="persuasion", dc=15, foundry=f
    ))
    assert out.get("deferred_to_player") is True
    f.request_skill_check.assert_not_called()   # GM did NOT roll for the PC
    f.chat_message.assert_awaited_once()        # player was prompted instead
    prompt = f.chat_message.await_args.args[0]
    assert "Beringar" in prompt and "Persuasion" in prompt and "15" in prompt


def test_pc_skill_check_matches_by_short_uuid():
    """LLM sometimes hands back a bare actor id instead of the full 'Actor.xxx' uuid."""
    f = _foundry()
    out = asyncio.run(ex.execute_skill_check(
        actor_uuid="IMmMlM4zG7QSuMQ7", skill="stealth", dc=12, foundry=f
    ))
    assert out.get("deferred_to_player") is True
    f.request_skill_check.assert_not_called()


def test_npc_skill_check_still_auto_rolls():
    f = _foundry()
    out = asyncio.run(ex.execute_skill_check(
        actor_uuid="Actor.gobxyz", skill="stealth", dc=12, foundry=f
    ))
    assert out.get("deferred_to_player") is None
    f.request_skill_check.assert_awaited_once()   # GM rolls for the monster
    f.chat_message.assert_not_awaited()


def test_skill_check_respects_players_roll_own_false():
    """When the setting is disabled, PCs auto-roll too (opt-out config)."""
    from config import settings
    f = _foundry()
    original = settings.players_roll_own
    settings.players_roll_own = False
    try:
        out = asyncio.run(ex.execute_skill_check(
            actor_uuid="Actor.IMmMlM4zG7QSuMQ7", skill="persuasion", dc=15, foundry=f
        ))
        assert out.get("deferred_to_player") is None
        f.request_skill_check.assert_awaited_once()
    finally:
        settings.players_roll_own = original


if __name__ == "__main__":
    for fn in [
        test_pc_skill_check_is_deferred_to_player,
        test_pc_skill_check_matches_by_short_uuid,
        test_npc_skill_check_still_auto_rolls,
        test_skill_check_respects_players_roll_own_false,
    ]:
        setup_function(None)
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll skill-check player-defer tests passed!")
