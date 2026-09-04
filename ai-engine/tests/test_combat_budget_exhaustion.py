"""Regression coverage for degraded combat at the session token cap."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from combat.loop import CombatLoop
from foundry.chat_listener import ChatListener
from llm.usage import TokenBudgetExceeded


class _BudgetProbe:
    def __init__(self, available):
        self._available = iter(available)
        self.checks = 0

    async def budget_available(self, session_id):
        self.checks += 1
        return next(self._available)


def _make_loop(npcs=None, budget=None):
    foundry = MagicMock()
    foundry.get_scene_tokens = AsyncMock(return_value=[])
    foundry.chat_message = AsyncMock()
    foundry.execute_js = AsyncMock(return_value={"result": ["Longsword"]})
    foundry.get_multiattack_count = AsyncMock(return_value={"count": 1})
    dispatcher = MagicMock()
    dispatcher.execute_batch = AsyncMock(
        return_value=[{"type": "attack_with_item", "success": True}]
    )
    db = MagicMock()
    db.get_active_session = AsyncMock(return_value="session-1")
    loop = CombatLoop(
        foundry=foundry,
        llm=MagicMock(),
        dispatcher=dispatcher,
        state_tracker=MagicMock(),
        db=db,
        token_usage=budget,
    )
    loop._running = True
    loop._turn_order = ["hero"] + [npc["id"] for npc in (npcs or [])]
    loop._pc_tokens = [{"id": "hero", "name": "Hero", "disposition": 1}]
    loop._npc_tokens = npcs or []
    return loop


def _wire_budget_exhausting_llm(loop):
    async def generate(*args, **kwargs):
        raise TokenBudgetExceeded("session", 99, 2, 100)

    loop.llm.generate = AsyncMock(side_effect=generate)


def test_budget_exhaustion_mid_npc_turn_keeps_combat_running_and_uses_real_attack():
    async def run():
        npc = {"id": "monster", "name": "Ogre", "actorUuid": "Actor.ogre"}
        loop = _make_loop([npc])
        listener = ChatListener(
            foundry=loop.foundry,
            llm=loop.llm,
            dispatcher=loop.dispatcher,
            state_tracker=loop.state_tracker,
            db=loop.db,
            combat_loop=loop,
        )
        listener._running = True

        async def generate(*args, **kwargs):
            error = TokenBudgetExceeded("session", 99, 2, 100)
            await listener.handle_budget_exhausted(error)
            raise error

        loop.llm.generate = AsyncMock(side_effect=generate)

        await loop._process_npc_turn(npc)

        assert loop.is_running is True
        assert loop._degraded_mode is True
        assert listener._running is True
        loop.llm.generate.assert_awaited_once()
        loop.dispatcher.execute_batch.assert_awaited_once_with([{
            "type": "attack_with_item",
            "attacker_uuid": "Actor.ogre",
            "item_name": "Longsword",
            "target_token_id": "hero",
        }])
        assert len(loop.foundry.chat_message.await_args_list) == 1
        assert "Combat continues mechanically" in loop.foundry.chat_message.call_args.args[0]
        assert "hesitates" not in loop.foundry.chat_message.call_args.args[0]

    asyncio.run(run())


def test_exhausted_budget_mid_round_makes_no_more_llm_calls_or_messages():
    async def run():
        npcs = [
            {"id": "goblin-1", "name": "Goblin 1", "actorUuid": "Actor.goblin1"},
            {"id": "goblin-2", "name": "Goblin 2", "actorUuid": "Actor.goblin2"},
        ]
        budget = _BudgetProbe([False, False])
        loop = _make_loop(npcs, budget)
        _wire_budget_exhausting_llm(loop)

        await loop._process_npc_turn(npcs[0])
        await loop._process_npc_turn(npcs[1])

        loop.llm.generate.assert_awaited_once()
        assert budget.checks == 1
        assert len(loop.foundry.chat_message.await_args_list) == 1
        assert loop.dispatcher.execute_batch.await_count == 2

    asyncio.run(run())


def test_budget_restoration_resumes_full_narration_on_next_npc_turn():
    async def run():
        npc = {"id": "monster", "name": "Ogre", "actorUuid": "Actor.ogre"}
        budget = _BudgetProbe([True])
        loop = _make_loop([npc], budget)
        _wire_budget_exhausting_llm(loop)
        loop.llm.generate.side_effect = [
            TokenBudgetExceeded("session", 99, 2, 100),
            {"actions": [{"type": "narrate", "text": "The ogre attacks."}]},
        ]

        await loop._process_npc_turn(npc)
        await loop._process_npc_turn(npc)

        assert loop.llm.generate.await_count == 2
        assert loop._degraded_mode is False
        messages = [call.args[0] for call in loop.foundry.chat_message.await_args_list]
        assert len(messages) == 2
        assert "narration is paused" in messages[0]
        assert "narration is restored" in messages[1]

    asyncio.run(run())


def test_degraded_npc_without_attack_item_holds_position_silently():
    async def run():
        npc = {"id": "ghost", "name": "Ghost", "actorUuid": "Actor.ghost"}
        loop = _make_loop([npc])
        loop.foundry.execute_js.return_value = {"result": []}
        await loop.enter_degraded_mode()

        await loop._execute_degraded_npc_turn(npc)

        loop.dispatcher.execute_batch.assert_not_awaited()
        assert len(loop.foundry.chat_message.await_args_list) == 1

    asyncio.run(run())
