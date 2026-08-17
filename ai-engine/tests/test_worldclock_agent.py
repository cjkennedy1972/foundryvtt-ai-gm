#!/usr/bin/env python3
"""Tests for worldclock.agent.WorldClockAgent.

Run:
    cd ai-engine && python -m pytest tests/test_worldclock_agent.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from events.store import EventStore
from npc.goals import Goal
from npc.registry import NPCRegistry
from persistence.db import Database
from worldclock.agent import WorldClockAgent


def test_advance_records_time_advanced_event():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        clock = WorldClockAgent(store, NPCRegistry())

        await clock.advance("s1", 3600)

        state = await store.replay("s1")
        assert state["world_time_elapsed_seconds"] == 3600
        await db.close()

    asyncio.run(run())


def test_advance_activates_matching_pending_goal():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(
            description="seek revenge on the party",
            trigger_conditions={"event_type": "time_advanced"},
        ))
        clock = WorldClockAgent(store, reg)

        activated = await clock.advance("s1", 3600)

        assert activated == ["n1:seek revenge on the party"]
        assert reg.get_npc("n1").goals[0].status == "active"
        await db.close()

    asyncio.run(run())


def test_advance_ignores_goals_without_matching_trigger():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(description="idle goal"))  # no trigger_conditions

        clock = WorldClockAgent(store, reg)
        activated = await clock.advance("s1", 3600)

        assert activated == []
        assert reg.get_npc("n1").goals[0].status == "pending"
        await db.close()

    asyncio.run(run())


def test_advance_does_not_reactivate_already_active_goals():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(
            description="already active",
            status="active",
            trigger_conditions={"event_type": "time_advanced"},
        ))
        clock = WorldClockAgent(store, reg)

        activated = await clock.advance("s1", 3600)

        assert activated == []  # only 'pending' goals are activated, not re-fired
        await db.close()

    asyncio.run(run())
