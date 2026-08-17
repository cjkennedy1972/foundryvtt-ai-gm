"""Tests for events/replay.py — session transcript and audit trail APIs.

Run:
    cd ai-engine && python -m pytest tests/test_session_replay.py -v
"""

import asyncio
from events.store import EventStore
from events.replay import SessionReplay
from events.types import (
    NPC_MOVED, RELATIONSHIP_CHANGED, FACT_CANONIZED,
    TIME_ADVANCED, ACTION_RESOLVED
)
from persistence.db import Database


def test_transcript_humanizes_events(tmp_path):
    """Events are converted to readable format: npc names, action summaries, etc."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)
        replay = SessionReplay(store)

        # Add mixed events
        await store.append("s1", ACTION_RESOLVED, {
            "action_type": "narrate", "success": True
        })
        await store.append("s1", NPC_MOVED, {
            "npc_id": "mara", "location": "tavern"
        })
        await store.append("s1", FACT_CANONIZED, {
            "fact": "The king is a shapeshifter"
        })

        events = await replay.get_session_transcript("s1")
        assert len(events) == 3
        assert events[0]["event"] == "action"
        assert events[1]["event"] == "npc_moved"
        assert "mara" in str(events[1])
        assert events[2]["event"] == "canon"
        assert "shapeshifter" in str(events[2])

        await db.close()

    asyncio.run(run())


def test_transcript_respects_limit(tmp_path):
    """limit parameter returns only most-recent N events."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)
        replay = SessionReplay(store)

        for i in range(5):
            await store.append("s1", NPC_MOVED, {
                "npc_id": "n1", "location": f"loc-{i}"
            })

        all_events = await replay.get_session_transcript("s1")
        limited = await replay.get_session_transcript("s1", limit=2)

        assert len(all_events) == 5
        assert len(limited) == 2
        assert limited[0]["location"] == "loc-3"
        assert limited[1]["location"] == "loc-4"

        await db.close()

    asyncio.run(run())


def test_state_at_time_projects_world_state(tmp_path):
    """Replaying up to event N gives world state at that point."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)
        replay = SessionReplay(store)

        await store.append("s1", NPC_MOVED, {"npc_id": "mara", "location": "tavern"})
        await store.append("s1", TIME_ADVANCED, {"duration_seconds": 3600})
        await store.append("s1", NPC_MOVED, {"npc_id": "mara", "location": "market"})

        # State after first event: Mara at tavern
        state_0 = await replay.get_state_at_time("s1", 0)
        assert state_0.get("npcs", {}).get("mara", {}).get("location") == "tavern"

        # State after third event: Mara at market, 1h passed
        state_2 = await replay.get_state_at_time("s1", 2)
        assert state_2.get("npcs", {}).get("mara", {}).get("location") == "market"
        assert state_2.get("world_time_elapsed_seconds") == 3600

        await db.close()

    asyncio.run(run())


def test_find_events_by_type(tmp_path):
    """find_events_by_type returns all matching events."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)
        replay = SessionReplay(store)

        await store.append("s1", NPC_MOVED, {"npc_id": "n1", "location": "a"})
        await store.append("s1", ACTION_RESOLVED, {"action_type": "narrate", "success": True})
        await store.append("s1", NPC_MOVED, {"npc_id": "n1", "location": "b"})

        moves = await replay.find_events_by_type("s1", "npc_moved")
        actions = await replay.find_events_by_type("s1", "action_resolved")

        assert len(moves) == 2
        assert len(actions) == 1
        assert moves[0]["location"] == "a"
        assert moves[1]["location"] == "b"

        await db.close()

    asyncio.run(run())


def test_find_events_by_npc(tmp_path):
    """find_events_by_npc finds events where NPC appears as npc_id, source, or target."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)
        replay = SessionReplay(store)

        # Mara moves
        await store.append("s1", NPC_MOVED, {"npc_id": "mara", "location": "tavern"})
        # Mara → PC-1 relationship
        await store.append("s1", RELATIONSHIP_CHANGED, {
            "source_id": "mara", "target_id": "pc-1",
            "relationship_type": "ally", "strength": 0.8
        })
        # PC-2 → Mara relationship
        await store.append("s1", RELATIONSHIP_CHANGED, {
            "source_id": "pc-2", "target_id": "mara",
            "relationship_type": "suspicious", "strength": 0.3
        })
        # Unrelated event
        await store.append("s1", TIME_ADVANCED, {"duration_seconds": 100})

        mara_events = await replay.find_events_by_npc("s1", "mara")
        assert len(mara_events) == 3  # move + 2 relationships
        assert any(e["event"] == "npc_moved" for e in mara_events)
        assert sum(1 for e in mara_events if e["event"] == "relationship") == 2

        await db.close()

    asyncio.run(run())


def test_format_transcript_for_chat(tmp_path):
    """Transcript is formatted as readable markdown for Foundry chat."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        store = EventStore(db)
        replay = SessionReplay(store)

        await store.append("s1", ACTION_RESOLVED, {
            "action_type": "narrate", "success": True
        })
        await store.append("s1", NPC_MOVED, {
            "npc_id": "mara", "location": "tavern"
        })

        events = await replay.get_session_transcript("s1")
        text = replay.format_transcript_for_chat(events)

        assert "Session Transcript" in text
        assert "narrate" in text.lower()
        assert "mara" in text.lower()
        assert "tavern" in text
        assert "✅" in text  # success marker

        await db.close()

    asyncio.run(run())
