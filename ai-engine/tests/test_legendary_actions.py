#!/usr/bin/env python3
"""
Legendary actions: a legendary NPC may act at the end of any OTHER
creature's turn, spending from a real per-actor resource read off the
sheet (system.resources.legact) — never on its own turn, never past zero.

Run:
    cd ai-engine && python -m pytest tests/test_legendary_actions.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.loop import CombatLoop


def _loop(foundry, legact_value=3, llm_actions=None):
    llm = MagicMock()
    llm.generate = AsyncMock(return_value={"actions": llm_actions if llm_actions is not None else [
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Bite", "target_token_id": "tok1"}
    ]})
    dispatcher = MagicMock()
    dispatcher.execute_batch = AsyncMock(return_value=[{"success": True}])
    loop = CombatLoop(
        foundry=foundry, llm=llm, dispatcher=dispatcher,
        state_tracker=MagicMock(), db=MagicMock(),
    )
    loop._npc_tokens = [
        {"id": "tok1", "name": "Goblin", "actorUuid": "Actor.goblin", "hp": 7},
        {"id": "tok2", "name": "Ancient Red Dragon", "actorUuid": "Actor.dragon", "hp": 200},
    ]
    return loop


def _foundry(legact_value=3, legendary_actor_uuid="Actor.dragon"):
    """Only legendary_actor_uuid has legendary actions — everyone else is a
    normal monster with legact.max == 0, matching how a real sheet reads.
    """
    def _respond(js):
        is_legendary_actor = f'"{legendary_actor_uuid}"' in js
        if "legact" in js:
            return {"result": {"value": legact_value, "max": 3} if is_legendary_actor else {"value": 0, "max": 0}}
        return {"result": {"ok": True}}

    f = AsyncMock()
    f.execute_js = AsyncMock(side_effect=_respond)
    return f


def test_legendary_npc_acts_after_another_creatures_turn():
    f = _foundry(legact_value=3)
    loop = _loop(f)
    acted_token = {"id": "tok1", "name": "Goblin"}  # a normal NPC's turn just ended
    asyncio.run(loop._maybe_legendary_actions(acted_token))
    loop.llm.generate.assert_awaited_once()
    loop.dispatcher.execute_batch.assert_awaited_once()


def test_legendary_creature_does_not_act_on_its_own_turn():
    f = _foundry(legact_value=3)
    loop = _loop(f)
    acted_token = {"id": "tok2", "name": "Ancient Red Dragon"}  # the dragon's own turn just ended
    asyncio.run(loop._maybe_legendary_actions(acted_token))
    loop.llm.generate.assert_not_awaited()


def test_zero_remaining_legendary_actions_skips_llm_call():
    f = _foundry(legact_value=0)
    loop = _loop(f)
    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Beringar"}))
    loop.llm.generate.assert_not_awaited()


def test_dead_legendary_npc_is_skipped():
    f = _foundry(legact_value=3)
    loop = _loop(f)
    loop._npc_tokens[1]["hp"] = 0  # dragon is down
    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Beringar"}))
    loop.llm.generate.assert_not_awaited()


def test_passing_does_not_decrement_the_resource():
    f = _foundry(legact_value=3)
    loop = _loop(f, llm_actions=[{"type": "narrate", "text": "The dragon watches, biding its time."}])
    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Beringar"}))
    loop.llm.generate.assert_awaited_once()
    set_calls = [c for c in f.execute_js.await_args_list if "legact.value': 2" in c.args[0]]
    assert not set_calls  # narrate-only = pass, resource untouched


def test_acting_decrements_the_resource():
    f = _foundry(legact_value=3)
    loop = _loop(f)
    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Beringar"}))
    set_calls = [c for c in f.execute_js.await_args_list if "legact.value': 2" in c.args[0]]
    assert set_calls  # 3 -> 2 after spending one


if __name__ == "__main__":
    for fn in [
        test_legendary_npc_acts_after_another_creatures_turn,
        test_legendary_creature_does_not_act_on_its_own_turn,
        test_zero_remaining_legendary_actions_skips_llm_call,
        test_dead_legendary_npc_is_skipped,
        test_passing_does_not_decrement_the_resource,
        test_acting_decrements_the_resource,
    ]:
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll legendary-action tests passed!")
