#!/usr/bin/env python3
"""Tests for npc.agent.NPCAgent — the autonomous NPC turn.

Run:
    cd ai-engine && python -m pytest tests/test_npc_agent.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from events.store import EventStore
from llm.router import ModelRouter
from npc.agent import NPCAgent
from npc.goals import Goal
from npc.memory import NPCMemory
from npc.registry import NPCRegistry
from persistence.db import Database
from referee.agent import RefereeAgent


def _npc_with_active_goal(description="seek revenge on the party"):
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight")
    reg.add_goal("n1", Goal(description=description, status="active"))
    return reg.get_npc("n1")


def test_no_active_goals_returns_empty_without_calling_llm():
    async def run():
        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(description="idle", status="pending"))
        npc = reg.get_npc("n1")

        llm = MagicMock()
        llm.generate = AsyncMock()
        router = ModelRouter(llm)
        db = Database(":memory:")
        await db.init()
        memory = NPCMemory(EventStore(db))

        agent = NPCAgent(npc, router, RefereeAgent(), memory)
        rulings = await agent.act("s1", {"type": "time_advanced", "payload": {}})

        assert rulings == []
        llm.generate.assert_not_called()
        await db.close()

    asyncio.run(run())


def test_active_goal_calls_llm_and_returns_approved_rulings():
    async def run():
        npc = _npc_with_active_goal()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value={"actions": [{"type": "narrate", "text": "Mara scowls."}]})
        router = ModelRouter(llm)
        db = Database(":memory:")
        await db.init()
        memory = NPCMemory(EventStore(db))

        agent = NPCAgent(npc, router, RefereeAgent(), memory)
        rulings = await agent.act("s1", {"type": "action_resolved", "payload": {}})

        llm.generate.assert_called_once()
        assert len(rulings) == 1
        assert rulings[0].action["type"] == "narrate"
        await db.close()

    asyncio.run(run())


def test_rejected_ruling_is_filtered_out():
    async def run():
        npc = _npc_with_active_goal()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value={"actions": [{"type": "narrate", "text": "..."}]})
        router = ModelRouter(llm)
        db = Database(":memory:")
        await db.init()
        memory = NPCMemory(EventStore(db))

        referee = MagicMock()
        from referee.models import Ruling
        referee.adjudicate_batch = AsyncMock(return_value=[Ruling(approved=False, action={"type": "narrate"})])

        agent = NPCAgent(npc, router, referee, memory)
        rulings = await agent.act("s1", {"type": "action_resolved", "payload": {}})

        assert rulings == []
        await db.close()

    asyncio.run(run())


def test_llm_failure_fails_open_to_empty_list():
    async def run():
        npc = _npc_with_active_goal()
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("model host unreachable"))
        router = ModelRouter(llm)
        db = Database(":memory:")
        await db.init()
        memory = NPCMemory(EventStore(db))

        agent = NPCAgent(npc, router, RefereeAgent(), memory)
        rulings = await agent.act("s1", {"type": "action_resolved", "payload": {}})

        assert rulings == []
        await db.close()

    asyncio.run(run())


def test_context_includes_goal_and_memory():
    async def run():
        npc = _npc_with_active_goal()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value={"actions": []})
        router = ModelRouter(llm)
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        memory = NPCMemory(store)
        await store.append("s1", "npc_moved", {"npc_id": "n1", "location": "tavern"})

        agent = NPCAgent(npc, router, RefereeAgent(), memory)
        await agent.act("s1", {"type": "action_resolved", "payload": {}})

        _, kwargs = llm.generate.call_args
        assert "seek revenge on the party" in kwargs["extra_context"]
        assert "Mara" in kwargs["extra_context"]
        await db.close()

    asyncio.run(run())
