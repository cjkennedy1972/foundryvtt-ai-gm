#!/usr/bin/env python3
"""Tests for events.store.EventStore — append/replay/project.

Run:
    cd ai-engine && python -m pytest tests/test_event_store.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from events.store import EventStore
from events.types import FACT_CANONIZED, NPC_MOVED, RELATIONSHIP_CHANGED, TIME_ADVANCED
from persistence.db import Database


def test_append_and_replay_npc_moved(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)

        await store.append("s1", NPC_MOVED, {"npc_id": "npc-1", "location": "tavern"})
        await store.append("s1", NPC_MOVED, {"npc_id": "npc-1", "location": "market"})

        state = await store.replay("s1")
        assert state["npcs"]["npc-1"]["location"] == "market"
        await db.close()

    asyncio.run(run())


def test_replay_accumulates_canon_facts_in_order(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)

        await store.append("s1", FACT_CANONIZED, {"fact": "The king is a doppelganger."})
        await store.append("s1", FACT_CANONIZED, {"fact": "The tavern burned down."})

        state = await store.replay("s1")
        assert state["canon_facts"] == [
            "The king is a doppelganger.",
            "The tavern burned down.",
        ]
        await db.close()

    asyncio.run(run())


def test_replay_sums_time_advanced():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)

        await store.append("s1", TIME_ADVANCED, {"duration_seconds": 3600})
        await store.append("s1", TIME_ADVANCED, {"duration_seconds": 1800})

        state = await store.replay("s1")
        assert state["world_time_elapsed_seconds"] == 5400
        await db.close()

    asyncio.run(run())


def test_legacy_note_projects_as_noop():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)

        # Simulates a pre-Phase-2 row: no typed payload, description only.
        await db.record_event("s1", "The party entered the tavern.")

        state = await store.replay("s1")
        assert state == {}
        await db.close()

    asyncio.run(run())


def test_replay_isolated_per_session():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)

        await store.append("s1", RELATIONSHIP_CHANGED, {
            "source_id": "npc-1", "target_id": "pc-1",
            "relationship_type": "ally", "strength": 0.8,
        })
        await store.append("s2", RELATIONSHIP_CHANGED, {
            "source_id": "npc-2", "target_id": "pc-1",
            "relationship_type": "enemy", "strength": 0.1,
        })

        state_s1 = await store.replay("s1")
        state_s2 = await store.replay("s2")
        assert "npc-1->pc-1" in state_s1["relationships"]
        assert "npc-1->pc-1" not in state_s2["relationships"]
        assert "npc-2->pc-1" in state_s2["relationships"]
        await db.close()

    asyncio.run(run())


def test_get_events_with_limit_returns_most_recent_not_oldest():
    """Regression: Database.get_events_full applied LIMIT after ORDER BY id
    ASC, which returns the OLDEST N rows — the opposite of what any normal
    caller means by "give me the last N events"."""
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)

        for i in range(5):
            await store.append("s1", NPC_MOVED, {"npc_id": "n1", "location": f"loc-{i}"})

        events = await store.get_events("s1", limit=2)
        assert [e["payload"]["location"] for e in events] == ["loc-3", "loc-4"]
        await db.close()

    asyncio.run(run())


def test_unknown_event_type_does_not_break_replay():
    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)

        await db.record_typed_event("s1", "some_future_type", {"whatever": 1})
        await store.append("s1", NPC_MOVED, {"npc_id": "npc-1", "location": "market"})

        state = await store.replay("s1")
        assert state["npcs"]["npc-1"]["location"] == "market"
        await db.close()

    asyncio.run(run())
