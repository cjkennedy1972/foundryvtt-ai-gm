"""Regression coverage for the session token cap during NPC combat turns."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from combat.loop import CombatLoop
from foundry.chat_listener import ChatListener
from llm.usage import TokenBudgetExceeded


def _make_loop():
    foundry = MagicMock()
    foundry.get_scene_tokens = AsyncMock(return_value=[])
    foundry.chat_message = AsyncMock()
    return CombatLoop(
        foundry=foundry,
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
    )


def test_budget_exhaustion_stops_npc_turn_without_hesitation():
    async def run():
        loop = _make_loop()
        loop._running = True
        loop._turn_order = ["pc", "monster"]
        loop._npc_tokens = [{"id": "monster", "name": "Ogre", "actorUuid": "Actor.ogre"}]
        loop._pc_tokens = [{"id": "pc", "name": "Hero", "disposition": 1}]
        loop.llm.generate = AsyncMock(
            side_effect=TokenBudgetExceeded("session", 99, 2, 100)
        )

        await loop._process_npc_turn(loop._npc_tokens[0])

        assert loop.is_running is False
        assert not any("hesitates" in call.args[0] for call in loop.foundry.chat_message.await_args_list)

    asyncio.run(run())


def test_budget_exhaustion_handler_stops_linked_combat_and_announces_once():
    async def run():
        loop = _make_loop()
        loop._running = True
        listener = ChatListener(
            foundry=loop.foundry,
            llm=MagicMock(),
            dispatcher=MagicMock(),
            state_tracker=MagicMock(),
            db=MagicMock(),
            combat_loop=loop,
        )
        listener._running = True

        await listener.handle_budget_exhausted(
            TokenBudgetExceeded("session", 99, 2, 100)
        )

        assert listener._running is False
        assert loop.is_running is False
        messages = [call.args[0] for call in loop.foundry.chat_message.await_args_list]
        assert len(messages) == 1
        assert "reserves are spent" in messages[0]

    asyncio.run(run())
