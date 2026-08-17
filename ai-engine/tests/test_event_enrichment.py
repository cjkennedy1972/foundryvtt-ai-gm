"""Tests for event enrichment — NPC events carrying actor_uuid for location tracking.

Event payloads can optionally include actor_uuid to correlate NPCs with
Foundry actor tokens, enabling settlement queries and location tracking.

Run:
    cd ai-engine && python -m pytest tests/test_event_enrichment.py -v
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from events.store import EventStore
from events.types import NPC_MOVED, RELATIONSHIP_CHANGED, TIME_ADVANCED
from persistence.db import Database


class TestEventEnrichment:
    """Tests for NPC event enrichment with actor_uuid."""

    @pytest.mark.asyncio
    async def test_npc_moved_event_can_include_actor_uuid(self):
        """NPC_MOVED events can carry optional actor_uuid field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)

            session_id = "test-session"
            await db.create_session(session_id, campaign="Test Campaign")

            # Record NPC_MOVED with actor_uuid enrichment
            await store.append(session_id, NPC_MOVED, {
                "npc_id": "mara",
                "actor_uuid": "actor-uuid-123",
                "location": "tavern",
            })

            # Verify event was stored
            events = await db.get_events_full(session_id)
            assert len(events) == 1
            event = events[0]
            assert event["type"] == NPC_MOVED
            assert event["payload"]["npc_id"] == "mara"
            assert event["payload"]["actor_uuid"] == "actor-uuid-123"
            assert event["payload"]["location"] == "tavern"

            await db.close()

    @pytest.mark.asyncio
    async def test_npc_moved_reducer_ignores_optional_actor_uuid(self):
        """Reducer for NPC_MOVED ignores actor_uuid (used only for querying)."""
        from events.types import _reduce_npc_moved

        state = {"npcs": {}}

        # Event with actor_uuid
        payload_with_uuid = {
            "npc_id": "mara",
            "actor_uuid": "actor-123",
            "location": "tavern",
        }

        # Event without actor_uuid
        payload_without_uuid = {
            "npc_id": "mara",
            "location": "tavern",
        }

        # Both should produce the same state
        state1 = _reduce_npc_moved(state, payload_with_uuid)
        state2 = _reduce_npc_moved(state, payload_without_uuid)

        assert state1 == state2
        assert state1["npcs"]["mara"]["location"] == "tavern"

    @pytest.mark.asyncio
    async def test_relationship_changed_can_include_actor_uuids(self):
        """RELATIONSHIP_CHANGED events can carry actor_uuid for source and target."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)

            session_id = "test-session"
            await db.create_session(session_id, campaign="Test Campaign")

            # Record relationship with actor UUIDs for both NPCs
            await store.append(session_id, RELATIONSHIP_CHANGED, {
                "source_id": "mara",
                "source_actor_uuid": "actor-mara-123",
                "target_id": "kess",
                "target_actor_uuid": "actor-kess-456",
                "relationship_type": "ally",
                "strength": 0.8,
            })

            events = await db.get_events_full(session_id)
            assert len(events) == 1
            event = events[0]
            assert event["type"] == RELATIONSHIP_CHANGED
            assert event["payload"]["source_actor_uuid"] == "actor-mara-123"
            assert event["payload"]["target_actor_uuid"] == "actor-kess-456"

            await db.close()

    @pytest.mark.asyncio
    async def test_time_advanced_events_unchanged(self):
        """TIME_ADVANCED events don't carry NPC UUIDs (not NPC-specific)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)

            session_id = "test-session"
            await db.create_session(session_id, campaign="Test Campaign")

            # TIME_ADVANCED is not NPC-specific, so no actor_uuid needed
            await store.append(session_id, TIME_ADVANCED, {
                "duration_seconds": 3600,
            })

            events = await db.get_events_full(session_id)
            assert len(events) == 1
            event = events[0]
            assert event["type"] == TIME_ADVANCED
            assert "actor_uuid" not in event["payload"]

            await db.close()

    @pytest.mark.asyncio
    async def test_query_events_by_actor_uuid(self):
        """Can query events for a specific actor (by uuid in payload)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)

            session_id = "test-session"
            await db.create_session(session_id, campaign="Test Campaign")

            # Add events for two different NPCs with different actor UUIDs
            await store.append(session_id, NPC_MOVED, {
                "npc_id": "mara",
                "actor_uuid": "actor-uuid-1",
                "location": "tavern",
            })
            await store.append(session_id, NPC_MOVED, {
                "npc_id": "kess",
                "actor_uuid": "actor-uuid-2",
                "location": "market",
            })
            await store.append(session_id, NPC_MOVED, {
                "npc_id": "mara",
                "actor_uuid": "actor-uuid-1",
                "location": "inn",
            })

            # Query all events
            all_events = await db.get_events_full(session_id)
            assert len(all_events) == 3

            # Query events for a specific actor by filtering on actor_uuid
            mara_events = [
                e for e in all_events
                if e["payload"].get("actor_uuid") == "actor-uuid-1"
            ]
            assert len(mara_events) == 2
            assert all(e["payload"]["npc_id"] == "mara" for e in mara_events)

            await db.close()

    @pytest.mark.asyncio
    async def test_event_backward_compatibility_actor_uuid_optional(self):
        """Events work with or without actor_uuid (backward compatible)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)

            session_id = "test-session"
            await db.create_session(session_id, campaign="Test Campaign")

            # Mix of events: some with actor_uuid, some without
            await store.append(session_id, NPC_MOVED, {
                "npc_id": "mara",
                "location": "tavern",
                # No actor_uuid
            })
            await store.append(session_id, NPC_MOVED, {
                "npc_id": "kess",
                "actor_uuid": "actor-kess-123",
                "location": "market",
            })

            events = await db.get_events_full(session_id)
            assert len(events) == 2

            # First event (no UUID) should still be queryable
            assert events[0]["payload"]["npc_id"] == "mara"
            assert "actor_uuid" not in events[0]["payload"]

            # Second event (with UUID) should have it
            assert events[1]["payload"]["npc_id"] == "kess"
            assert events[1]["payload"]["actor_uuid"] == "actor-kess-123"

            await db.close()
