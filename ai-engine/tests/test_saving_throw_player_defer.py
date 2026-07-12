#!/usr/bin/env python3
"""
Regression test: saving throws must defer to the player, same as skill checks.

Mirrors test_skill_check_player_defer.py for the new saving_throw and
use_save_item actions (execute_saving_throw / execute_use_save_item).

Run:
    cd ai-engine && python -m pytest tests/test_saving_throw_player_defer.py -v
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
    f.request_saving_throw = AsyncMock(return_value={"total": 15, "success": True})
    f.chat_message = AsyncMock(return_value={"ok": True})
    f.execute_js = AsyncMock(return_value={"result": {
        "ok": True, "itemName": "Breath Weapon", "ability": "dex", "dc": 15,
        "results": [{"tokenId": "npc1", "targetName": "Goblin", "ability": "dex", "dc": 15,
                     "saveTotal": 10, "success": False, "damageDealt": 20}],
    }})
    f.get_scene_tokens = AsyncMock(return_value=[
        {"id": "pc1", "name": "Beringar", "actorUuid": "Actor.IMmMlM4zG7QSuMQ7"},
        {"id": "npc1", "name": "Goblin", "actorUuid": "Actor.gobxyz"},
    ])
    return f


def setup_function(_):
    ex._pc_uuid_cache = {}
    ex._pc_uuid_cache_at = 0.0


def test_pc_saving_throw_is_deferred_to_player():
    f = _foundry()
    out = asyncio.run(ex.execute_saving_throw(
        actor_uuid="Actor.IMmMlM4zG7QSuMQ7", ability="dexterity", dc=15, foundry=f
    ))
    assert out.get("deferred_to_player") is True
    f.request_saving_throw.assert_not_called()
    f.chat_message.assert_awaited_once()
    prompt = f.chat_message.await_args.args[0]
    assert "Beringar" in prompt and "Dexterity" in prompt and "15" in prompt


def test_npc_saving_throw_still_auto_rolls():
    f = _foundry()
    out = asyncio.run(ex.execute_saving_throw(
        actor_uuid="Actor.gobxyz", ability="constitution", dc=12, foundry=f
    ))
    assert out.get("deferred_to_player") is None
    f.request_saving_throw.assert_awaited_once()
    f.chat_message.assert_not_awaited()


def test_use_save_item_defers_pc_targets_and_resolves_npc_targets():
    f = _foundry()
    out = asyncio.run(ex.execute_use_save_item(
        caster_uuid="Actor.dragon", item_name="Breath Weapon",
        target_token_ids=["pc1", "npc1"], foundry=f
    ))
    assert out["success"] is True
    assert out["deferred_players"] == ["Beringar"]
    # Only the NPC target's token id reaches the JS auto-resolve set.
    call_args = f.execute_js.await_args
    assert call_args is not None
    f.chat_message.assert_awaited_once()
    prompt = f.chat_message.await_args.args[0]
    assert "Beringar" in prompt and "Dex" in prompt and "15" in prompt


def test_use_save_item_no_pc_targets_no_defer_prompt():
    f = _foundry()
    out = asyncio.run(ex.execute_use_save_item(
        caster_uuid="Actor.dragon", item_name="Breath Weapon",
        target_token_ids=["npc1"], foundry=f
    ))
    assert out["deferred_players"] == []
    f.chat_message.assert_not_awaited()


def test_environmental_save_defers_pc_targets_and_resolves_npc_targets():
    f = _foundry()
    out = asyncio.run(ex.execute_environmental_save(
        ability="dexterity", dc=15, target_token_ids=["pc1", "npc1"],
        damage_formula="2d6", reason="a poison gas trap", foundry=f
    ))
    assert out["success"] is True
    assert out["deferred_players"] == ["Beringar"]
    f.chat_message.assert_awaited_once()
    prompt = f.chat_message.await_args.args[0]
    assert "Beringar" in prompt and "Dexterity" in prompt and "15" in prompt and "poison gas trap" in prompt


def test_environmental_save_no_pc_targets_no_defer_prompt():
    f = _foundry()
    out = asyncio.run(ex.execute_environmental_save(
        ability="constitution", dc=13, target_token_ids=["npc1"], foundry=f
    ))
    assert out["deferred_players"] == []
    f.chat_message.assert_not_awaited()


if __name__ == "__main__":
    for fn in [
        test_pc_saving_throw_is_deferred_to_player,
        test_npc_saving_throw_still_auto_rolls,
        test_use_save_item_defers_pc_targets_and_resolves_npc_targets,
        test_use_save_item_no_pc_targets_no_defer_prompt,
        test_environmental_save_defers_pc_targets_and_resolves_npc_targets,
        test_environmental_save_no_pc_targets_no_defer_prompt,
    ]:
        setup_function(None)
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll saving-throw player-defer tests passed!")
