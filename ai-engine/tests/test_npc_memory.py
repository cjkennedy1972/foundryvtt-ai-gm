#!/usr/bin/env python3
"""Tests for npc.memory.NPCMemory.recall.

Run:
    cd ai-engine && python -m pytest tests/test_npc_memory.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from events.store import EventStore
from events.types import NPC_MOVED, RELATIONSHIP_CHANGED, TIME_ADVANCED
from npc.memory import NPCMemory
from persistence.db import Database


def test_recall_finds_events_by_npc_id():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        memory = NPCMemory(store)

        await store.append("s1", NPC_MOVED, {"npc_id": "n1", "location": "tavern"})
        await store.append("s1", NPC_MOVED, {"npc_id": "n2", "location": "market"})

        events = await memory.recall("s1", "n1")
        assert len(events) == 1
        assert events[0]["payload"]["location"] == "tavern"
        await db.close()

    asyncio.run(run())


def test_recall_finds_events_by_source_or_target():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        memory = NPCMemory(store)

        await store.append("s1", RELATIONSHIP_CHANGED, {
            "source_id": "n1", "target_id": "pc-1",
            "relationship_type": "enemy", "strength": 0.1,
        })

        events = await memory.recall("s1", "pc-1")
        assert len(events) == 1
        assert events[0]["type"] == RELATIONSHIP_CHANGED

    asyncio.run(run())


def test_recall_excludes_unrelated_events():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        memory = NPCMemory(store)

        await store.append("s1", TIME_ADVANCED, {"duration_seconds": 3600})

        events = await memory.recall("s1", "n1")
        assert events == []

    asyncio.run(run())


def test_recall_respects_limit_keeping_most_recent():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        memory = NPCMemory(store)

        for i in range(5):
            await store.append("s1", NPC_MOVED, {"npc_id": "n1", "location": f"loc-{i}"})

        events = await memory.recall("s1", "n1", limit=2)
        assert [e["payload"]["location"] for e in events] == ["loc-3", "loc-4"]

    asyncio.run(run())


def test_recall_limit_zero_returns_nothing_not_everything():
    """Regression: `limit=0` used to be falsy-checked as 'no limit', and
    even fixing that naively (`relevant[-0:]`) still returns everything
    since -0 == 0 in Python slicing."""
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        memory = NPCMemory(store)

        await store.append("s1", NPC_MOVED, {"npc_id": "n1", "location": "tavern"})

        events = await memory.recall("s1", "n1", limit=0)
        assert events == []

    asyncio.run(run())
