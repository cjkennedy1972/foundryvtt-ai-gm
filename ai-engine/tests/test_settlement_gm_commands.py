"""Tests for GM settlement query commands.

Tests that `/gm settlement list` and `/gm settlement query` work correctly
through the ChatListener interface.

Run:
    cd ai-engine && python -m pytest tests/test_settlement_gm_commands.py -v
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from npc.registry import NPCRegistry
from persistence.db import Database
from events.store import EventStore
from worldclock.agent import WorldClockAgent
from world.settlement import Settlement, SettlementNPC


class TestSettlementGMCommands:
    """Tests for settlement GM command handlers."""

    def test_settlement_list_empty(self):
        """List command returns appropriate message for empty settlements."""
        registry = NPCRegistry()
        agent = WorldClockAgent(EventStore(None), registry, {})

        settlements = agent.list_settlements()
        assert len(settlements) == 0

    def test_settlement_list_multiple(self):
        """List command can list multiple settlements."""
        registry = NPCRegistry()

        settlements = {
            "redmarch": Settlement(
                id="redmarch",
                name="Redmarch",
                region="The Coast",
                population=500,
                character="A bustling trade town",
            ),
            "thornwood": Settlement(
                id="thornwood",
                name="Thornwood",
                region="The Forest",
                population=200,
                character="A quiet forest village",
            ),
        }

        agent = WorldClockAgent(EventStore(None), registry, settlements)
        listed = agent.list_settlements()

        assert len(listed) == 2
        assert any(s.name == "Redmarch" for s in listed)
        assert any(s.name == "Thornwood" for s in listed)

    @pytest.mark.asyncio
    async def test_settlement_query_at_current_time(self):
        """Query without explicit time returns locations at current time."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara", "A tavern keeper")

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
            schedule={"dawn": "residence", "morning": "tavern", "noon": "tavern"},
        )
        settlement.npcs["mara"] = npc

        agent = WorldClockAgent(EventStore(None), registry, {"redmarch": settlement})

        # At dawn, mara should be at residence
        locations = await agent.query_location_at_time("redmarch")
        assert "residence" in locations
        assert "mara" in locations["residence"]

    @pytest.mark.asyncio
    async def test_settlement_query_at_specific_time(self):
        """Query with explicit time returns locations for that time."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara", "A tavern keeper")

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
            schedule={"dawn": "residence", "morning": "tavern", "dusk": "tavern"},
        )
        settlement.npcs["mara"] = npc

        agent = WorldClockAgent(EventStore(None), registry, {"redmarch": settlement})

        # Query at dusk
        locations = await agent.query_location_at_time("redmarch", "dusk")
        assert "tavern" in locations
        assert "mara" in locations["tavern"]

    @pytest.mark.asyncio
    async def test_settlement_query_multiple_npcs_at_location(self):
        """Query returns all NPCs at each location."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara", "Tavern keeper")
        registry.register_npc("kess", "Kess", "Blacksmith")

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
            schedule={"dusk": "tavern"},
        )
        kess = SettlementNPC(
            npc_id="kess",
            npc_name="Kess",
            occupation="blacksmith",
            primary_building="smithy",
            personality="gruff",
            schedule={"dusk": "tavern"},  # Both at tavern at dusk
        )

        settlement.npcs["mara"] = mara
        settlement.npcs["kess"] = kess

        agent = WorldClockAgent(EventStore(None), registry, {"redmarch": settlement})

        locations = await agent.query_location_at_time("redmarch", "dusk")
        assert set(locations["tavern"]) == {"mara", "kess"}

    @pytest.mark.asyncio
    async def test_settlement_query_unknown_settlement(self):
        """Query for unknown settlement returns empty dict."""
        registry = NPCRegistry()
        agent = WorldClockAgent(EventStore(None), registry, {})

        locations = await agent.query_location_at_time("nonexistent")
        assert locations == {}

    @pytest.mark.asyncio
    async def test_settlement_query_npcs_spread_across_locations(self):
        """Query shows NPCs distributed across multiple locations."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara", "Tavern keeper")
        registry.register_npc("kess", "Kess", "Blacksmith")
        registry.register_npc("elder", "Elder Tobias", "Village elder")

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
            schedule={"morning": "tavern"},
        )
        kess = SettlementNPC(
            npc_id="kess",
            npc_name="Kess",
            occupation="blacksmith",
            primary_building="smithy",
            personality="gruff",
            schedule={"morning": "smithy"},
        )
        elder = SettlementNPC(
            npc_id="elder",
            npc_name="Elder Tobias",
            occupation="village elder",
            primary_building="town_hall",
            personality="wise",
            schedule={"morning": "market"},
        )

        settlement.npcs["mara"] = mara
        settlement.npcs["kess"] = kess
        settlement.npcs["elder"] = elder

        agent = WorldClockAgent(EventStore(None), registry, {"redmarch": settlement})

        locations = await agent.query_location_at_time("redmarch", "morning")
        assert locations["tavern"] == ["mara"]
        assert locations["smithy"] == ["kess"]
        assert locations["market"] == ["elder"]
