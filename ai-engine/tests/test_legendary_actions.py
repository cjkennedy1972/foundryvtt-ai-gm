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


# ── one action, not one decrement ─────────────────────────────────────────
#
# RAW (MM p.11): "Only one legendary action can be used at a time and only at
# the end of another creature's turn." The prompt asks the model for one. The
# code executed everything the model returned and decremented exactly once, so
# a reply carrying three attacks bought three attacks for one legendary action.

def test_only_one_mechanical_action_is_taken_however_many_the_model_returns():
    f = _foundry(legact_value=3)
    loop = _loop(f, llm_actions=[
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Claw", "target_token_id": "tok1"},
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Claw", "target_token_id": "tok1"},
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Tail", "target_token_id": "tok1"},
    ])

    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Goblin"}))

    dispatched = loop.dispatcher.execute_batch.await_args.args[0]
    mechanical = [a for a in dispatched if a.get("type") != "narrate"]
    assert len(mechanical) == 1, f"three attacks for one legendary action: {mechanical}"


def test_narration_accompanying_the_action_is_kept():
    """Flavour text costs nothing and should still reach the table."""
    f = _foundry(legact_value=3)
    loop = _loop(f, llm_actions=[
        {"type": "narrate", "text": "The dragon's tail sweeps the hall."},
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Tail", "target_token_id": "tok1"},
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Claw", "target_token_id": "tok1"},
    ])

    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Goblin"}))

    dispatched = loop.dispatcher.execute_batch.await_args.args[0]
    assert [a["type"] for a in dispatched] == ["narrate", "attack_with_item"]


def test_the_action_kept_is_the_first_one_offered():
    f = _foundry(legact_value=3)
    loop = _loop(f, llm_actions=[
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Tail", "target_token_id": "tok1"},
        {"type": "move_token", "token_id": "tok2", "x": 100, "y": 100},
    ])

    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Goblin"}))

    dispatched = loop.dispatcher.execute_batch.await_args.args[0]
    assert dispatched[0]["item_name"] == "Tail"
    assert all(a.get("type") != "move_token" for a in dispatched)


def test_a_narration_only_reply_still_costs_nothing():
    f = _foundry(legact_value=3)
    loop = _loop(f, llm_actions=[{"type": "narrate", "text": "The dragon watches, waiting."}])

    asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Goblin"}))

    spends = [c for c in f.execute_js.await_args_list if "legact" in str(c.args) and "= 2" in str(c.args)]
    assert spends == [], "passing spent a legendary action"


def test_three_end_of_turn_offers_exhaust_three_legendary_actions():
    """The resource is the only thing standing between a dragon and an
    unlimited number of extra attacks per round."""
    spent = []

    def _respond(js):
        if "legact" in js and "update" in js.lower():
            spent.append(js)
            return {"result": {"ok": True}}
        if "legact" in js:
            remaining = max(0, 3 - len(spent))
            is_dragon = '"Actor.dragon"' in js
            return {"result": {"value": remaining if is_dragon else 0, "max": 3 if is_dragon else 0}}
        return {"result": {"ok": True}}

    f = AsyncMock()
    f.execute_js = AsyncMock(side_effect=_respond)
    loop = _loop(f, llm_actions=[
        {"type": "attack_with_item", "attacker_uuid": "Actor.dragon", "item_name": "Claw", "target_token_id": "tok1"},
    ])

    for _ in range(5):
        asyncio.run(loop._maybe_legendary_actions({"id": "tok1", "name": "Goblin"}))

    assert loop.dispatcher.execute_batch.await_count == 3, (
        f"the dragon acted {loop.dispatcher.execute_batch.await_count} times on three legendary actions"
    )


# ── lair actions: one per round ───────────────────────────────────────────

def test_the_lair_is_asked_for_one_action_a_round_not_three():
    """RAW (MM): "On initiative count 20 ... takes a lair action to cause one
    of the following effects ... can't use the same effect two rounds in a
    row." The prompt asked for 1-3, so a lair ran at up to triple rate."""
    f = _foundry()
    loop = _loop(f, llm_actions=[{"type": "narrate", "text": "The floor cracks."}])
    loop._round_number = 2

    asyncio.run(loop._maybe_lair_actions())

    context = loop.llm.generate.await_args.kwargs["extra_context"]
    assert "1-3" not in context, "the lair is still offered up to three effects a round"
    assert "one lair action per round" in context.lower()
