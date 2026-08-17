"""Tests for WorldClockAgent settlement-aware location tracking.

When time advances, NPCs move to their scheduled locations and NPC_MOVED
events are logged (with actor_uuid if mapped). This enables "who is in the
tavern at dusk?" queries across settlements.

Run:
    cd ai-engine && python -m pytest tests/test_worldclock_settlement_tracking.py -v
"""

import pytest
import tempfile
from pathlib import Path

from events.store import EventStore
from events.types import TIME_ADVANCED, NPC_MOVED
from npc.registry import NPCRegistry
from persistence.db import Database
from worldclock.agent import WorldClockAgent
from world.settlement import Settlement, SettlementNPC


class TestWorldClockSettlementTracking:
    """Tests for time advancement and NPC location tracking."""

    @pytest.mark.asyncio
    async def test_worldclock_advances_time_of_day(self):
        """Advancing time updates the current time-of-day cycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()

            agent = WorldClockAgent(store, registry)
            assert agent.get_current_time() == "dawn"

            # Advance 3600 seconds (1 cycle)
            await agent.advance("session-1", 3600)
            assert agent.get_current_time() == "morning"

            # Advance another 3600 seconds
            await agent.advance("session-1", 3600)
            assert agent.get_current_time() == "noon"

            await db.close()

    @pytest.mark.asyncio
    async def test_worldclock_wraps_time_cycle(self):
        """Time cycle wraps around after the last time-of-day."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()

            agent = WorldClockAgent(store, registry)

            # Advance through entire day (6 cycles × 3600 = 21600 seconds)
            await agent.advance("session-1", 21600)
            assert agent.get_current_time() == "dawn"  # Wrapped back to start

            await db.close()

    @pytest.mark.asyncio
    async def test_worldclock_registers_settlements(self):
        """Settlements can be registered for location tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()

            agent = WorldClockAgent(store, registry)
            settlement = Settlement(
                id="redmarch",
                name="Redmarch",
                region="The Coast",
                population=500,
                character="A bustling trade town",
            )

            agent.register_settlement(settlement)
            assert len(agent.list_settlements()) == 1
            assert agent.list_settlements()[0].name == "Redmarch"

            await db.close()

    @pytest.mark.asyncio
    async def test_worldclock_logs_npc_moved_events(self):
        """Advancing time logs NPC_MOVED events for location changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()
            session_id = "session-1"
            await db.create_session(session_id, campaign="Test Campaign")

            # Create settlement and NPC with schedule
            settlement = Settlement(
                id="redmarch",
                name="Redmarch",
                region="The Coast",
                population=500,
                character="A bustling trade town",
            )

            npc = SettlementNPC(
                npc_id="mara",
                npc_name="Mara",
                occupation="tavern keeper",
                primary_building="tavern",
                personality="shrewd",
                schedule={
                    "dawn": "residence",
                    "morning": "tavern",
                    "noon": "tavern",
                    "afternoon": "tavern",
                    "dusk": "tavern",
                    "night": "residence",
                },
            )
            settlement.npcs["mara"] = npc

            # Register NPC and settlement
            registry.register_npc("mara", "Mara", "A tavern keeper")
            agent = WorldClockAgent(store, registry, {"redmarch": settlement})

            # Advance to morning (when Mara should be at tavern)
            await agent.advance(session_id, 3600)

            # Check that NPC_MOVED event was logged
            events = await db.get_events_full(session_id)
            moved_events = [e for e in events if e["type"] == NPC_MOVED]

            assert len(moved_events) > 0
            moved_event = moved_events[0]
            assert moved_event["payload"]["npc_id"] == "mara"
            assert moved_event["payload"]["location"] == "tavern"

            await db.close()

    @pytest.mark.asyncio
    async def test_worldclock_includes_actor_uuid_in_npc_moved(self):
        """NPC_MOVED events include actor_uuid if NPC is mapped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()
            session_id = "session-1"
            await db.create_session(session_id, campaign="Test Campaign")

            # Register NPC and map to actor
            registry.register_npc("mara", "Mara", "A tavern keeper")
            registry.map_actor_to_npc("actor-uuid-123", "mara")

            # Create settlement and NPC with schedule
            settlement = Settlement(
                id="redmarch",
                name="Redmarch",
                region="The Coast",
                population=500,
                character="A bustling trade town",
            )

            npc = SettlementNPC(
                npc_id="mara",
                npc_name="Mara",
                occupation="tavern keeper",
                primary_building="tavern",
                personality="shrewd",
                schedule={"morning": "tavern"},
            )
            settlement.npcs["mara"] = npc

            agent = WorldClockAgent(store, registry, {"redmarch": settlement})

            # Advance to morning
            await agent.advance(session_id, 3600)

            # Check that actor_uuid is in the event
            events = await db.get_events_full(session_id)
            moved_events = [e for e in events if e["type"] == NPC_MOVED]

            assert len(moved_events) > 0
            moved_event = moved_events[0]
            assert moved_event["payload"]["actor_uuid"] == "actor-uuid-123"

            await db.close()

    @pytest.mark.asyncio
    async def test_worldclock_query_location_at_time(self):
        """Can query NPC locations at specific times."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()

            # Create settlement with NPCs
            settlement = Settlement(
                id="redmarch",
                name="Redmarch",
                region="The Coast",
                population=500,
                character="A bustling trade town",
            )

            mara = SettlementNPC(
                npc_id="mara",
                npc_name="Mara",
                occupation="tavern keeper",
                primary_building="tavern",
                personality="shrewd",
                schedule={"morning": "tavern", "dusk": "tavern", "night": "residence"},
            )
            kess = SettlementNPC(
                npc_id="kess",
                npc_name="Kess",
                occupation="blacksmith",
                primary_building="smithy",
                personality="gruff",
                schedule={"morning": "smithy", "dusk": "smithy", "night": "tavern"},
            )

            settlement.npcs["mara"] = mara
            settlement.npcs["kess"] = kess

            agent = WorldClockAgent(store, registry, {"redmarch": settlement})

            # Query at dusk
            dusk_locations = await agent.query_location_at_time("redmarch", "dusk")
            assert "tavern" in dusk_locations
            assert "mara" in dusk_locations["tavern"]
            assert "kess" in dusk_locations["smithy"]

            # Query at night
            night_locations = await agent.query_location_at_time("redmarch", "night")
            assert night_locations["tavern"] == ["kess"]
            assert "mara" not in night_locations.get("tavern", [])

            await db.close()

    @pytest.mark.asyncio
    async def test_worldclock_query_current_time(self):
        """Query location at current time (no explicit time argument)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()

            settlement = Settlement(
                id="redmarch",
                name="Redmarch",
                region="The Coast",
                population=500,
                character="A bustling trade town",
            )

            mara = SettlementNPC(
                npc_id="mara",
                npc_name="Mara",
                occupation="tavern keeper",
                primary_building="tavern",
                personality="shrewd",
                schedule={"dawn": "residence", "morning": "tavern"},
            )
            settlement.npcs["mara"] = mara

            agent = WorldClockAgent(store, registry, {"redmarch": settlement})

            # At dawn: mara should be at residence
            locations = await agent.query_location_at_time("redmarch")
            assert locations.get("residence") == ["mara"]

            # Advance to morning
            await agent.advance("session-1", 3600)
            locations = await agent.query_location_at_time("redmarch")
            assert locations.get("tavern") == ["mara"]

            await db.close()

    @pytest.mark.asyncio
    async def test_worldclock_handles_missing_settlement(self):
        """Querying unknown settlement returns empty dict gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "test.db"))
            await db.init()
            store = EventStore(db)
            registry = NPCRegistry()

            agent = WorldClockAgent(store, registry)

            locations = await agent.query_location_at_time("nonexistent")
            assert locations == {}

            await db.close()
