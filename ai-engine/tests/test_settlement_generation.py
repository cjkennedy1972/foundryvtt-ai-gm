"""Tests for settlement generation and queries.

Settlements are towns with NPCs, buildings, factions, and daily schedules.
Key capability: query "who is in the tavern at dusk?" based on NPC schedules.

Run:
    cd ai-engine && python -m pytest tests/test_settlement_generation.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from world.settlement import Settlement, Building, SettlementNPC, Faction
from world.settlement_generator import SettlementGenerator


class TestSettlementSchema:
    """Tests for settlement data structures."""

    def test_building_creation_and_properties(self):
        """Building can be created with all properties."""
        building = Building(
            id="tavern_main",
            name="The Drunken Griffin",
            building_type="tavern",
            services=["lodging", "ale", "rumors"],
            occupants=["mara", "barkeep"],
            inventory={"ale": 50, "wine": 20},
            description="A lively tavern filled with adventurers",
        )

        assert building.id == "tavern_main"
        assert building.name == "The Drunken Griffin"
        assert "ale" in building.services
        assert len(building.occupants) == 2

    def test_settlement_npc_with_schedule(self):
        """SettlementNPC includes occupation and time-of-day schedule."""
        npc = SettlementNPC(
            npc_id="mara",
            npc_name="Mara the Wise",
            occupation="tavern keeper",
            primary_building="tavern_main",
            personality="shrewd, observant",
            secret="secretly runs the local thieves guild",
            schedule={
                "dawn": "residence",
                "morning": "tavern_main",
                "noon": "tavern_main",
                "afternoon": "market",
                "dusk": "tavern_main",
                "night": "tavern_main",
            },
        )

        assert npc.occupation == "tavern keeper"
        assert npc.schedule["dusk"] == "tavern_main"
        assert npc.schedule["afternoon"] == "market"

    def test_settlement_query_location_at_time(self):
        """Can query which NPCs are at each location at a specific time."""
        settlement = Settlement(
            id="redmarch",
            name="Redmarch",
            region="The Coast",
            population=500,
            character="A bustling trade town",
        )

        # Add NPCs with schedules
        mara = SettlementNPC(
            npc_id="mara",
            npc_name="Mara",
            occupation="tavern keeper",
            primary_building="tavern",
            personality="shrewd",
            schedule={"dusk": "tavern", "night": "residence", "morning": "residence"},
        )
        kess = SettlementNPC(
            npc_id="kess",
            npc_name="Kess",
            occupation="blacksmith",
            primary_building="smithy",
            personality="gruff",
            schedule={"dusk": "smithy", "night": "tavern", "morning": "smithy"},
        )

        settlement.npcs["mara"] = mara
        settlement.npcs["kess"] = kess

        # Query dusk
        dusk_locations = settlement.query_location_at_time("dusk")
        assert "tavern" in dusk_locations
        assert "mara" in dusk_locations["tavern"]
        assert "kess" in dusk_locations["smithy"]

        # Query night
        night_locations = settlement.query_location_at_time("night")
        assert night_locations["tavern"] == ["kess"]
        assert "mara" not in night_locations.get("tavern", [])

    def test_settlement_npc_occupation_lookup(self):
        """Can look up NPC's occupation by ID."""
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
        )
        settlement.npcs["mara"] = npc

        assert settlement.npc_occupation("mara") == "tavern keeper"
        assert settlement.npc_occupation("unknown") is None

    def test_settlement_building_occupants_at_time(self):
        """Can query occupants of a specific building at a time."""
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
            schedule={"dusk": "tavern", "night": "tavern"},
        )
        kess = SettlementNPC(
            npc_id="kess",
            npc_name="Kess",
            occupation="blacksmith",
            primary_building="smithy",
            personality="gruff",
            schedule={"dusk": "smithy", "night": "tavern"},
        )

        settlement.npcs["mara"] = mara
        settlement.npcs["kess"] = kess

        # At dusk, tavern has mara only
        dusk_tavern = settlement.building_occupants_at_time("tavern", "dusk")
        assert dusk_tavern == ["mara"]

        # At night, tavern has both
        night_tavern = settlement.building_occupants_at_time("tavern", "night")
        assert set(night_tavern) == {"mara", "kess"}

    def test_settlement_faction_structure(self):
        """Faction can represent power groups with relationships."""
        faction = Faction(
            id="thieves_guild",
            name="The Silent Syndicate",
            description="A shadowy organization controlling the underworld",
            power_level=8,
            leader="mara",
            members=["mara", "kess", "street_urchin"],
            rivals=["merchants_guild"],
            goals=["control the port", "eliminate rival factions"],
        )

        assert faction.power_level == 8
        assert "mara" in faction.members
        assert len(faction.goals) == 2

    def test_settlement_describe_npc_brief(self):
        """Can generate brief NPC description for GM narration."""
        settlement = Settlement(
            id="redmarch",
            name="Redmarch",
            region="The Coast",
            population=500,
            character="A bustling trade town",
        )

        tavern = Building(
            id="tavern",
            name="The Drunken Griffin",
            building_type="tavern",
        )
        settlement.buildings["tavern"] = tavern

        npc = SettlementNPC(
            npc_id="mara",
            npc_name="Mara the Wise",
            occupation="tavern keeper",
            primary_building="tavern",
            personality="shrewd",
        )
        settlement.npcs["mara"] = npc

        description = settlement.describe_npc_brief("mara")
        assert "Mara the Wise" in description
        assert "tavern keeper" in description
        assert "Drunken Griffin" in description


class TestSettlementGenerator:
    """Tests for LLM-powered settlement generation."""

    @pytest.mark.asyncio
    async def test_settlement_generator_parses_llm_output(self):
        """Generator parses LLM JSON output into Settlement object."""
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value={
                "text": """{
                    "settlement_id": "redmarch",
                    "name": "Redmarch",
                    "region": "The Coast",
                    "population": 500,
                    "character": "A bustling trade town",
                    "buildings": [
                        {
                            "id": "tavern",
                            "name": "The Drunken Griffin",
                            "building_type": "tavern",
                            "services": ["lodging", "ale"],
                            "occupants": ["mara"],
                            "inventory": {"ale": 50},
                            "description": "A lively tavern"
                        }
                    ],
                    "npcs": [
                        {
                            "npc_id": "mara",
                            "npc_name": "Mara the Wise",
                            "occupation": "tavern keeper",
                            "primary_building": "tavern",
                            "personality": "shrewd",
                            "schedule": {
                                "dawn": "residence",
                                "morning": "tavern",
                                "noon": "tavern",
                                "afternoon": "tavern",
                                "dusk": "tavern",
                                "night": "residence"
                            }
                        }
                    ],
                    "factions": [
                        {
                            "id": "guild",
                            "name": "Merchants Guild",
                            "description": "Local merchants",
                            "power_level": 5,
                            "members": ["mara"]
                        }
                    ]
                }"""
            }
        )

        generator = SettlementGenerator(llm)
        settlement = await generator.generate(
            "Redmarch",
            "A coastal campaign setting",
        )

        assert settlement.name == "Redmarch"
        assert settlement.population == 500
        assert len(settlement.buildings) == 1
        assert len(settlement.npcs) == 1
        assert len(settlement.factions) == 1

    @pytest.mark.asyncio
    async def test_settlement_generator_handles_markdown_wrapped_json(self):
        """Generator handles JSON wrapped in markdown code blocks."""
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value={
                "text": """```json
{
    "settlement_id": "redmarch",
    "name": "Redmarch",
    "region": "The Coast",
    "population": 500,
    "character": "A bustling trade town",
    "buildings": [],
    "npcs": [],
    "factions": []
}
```"""
            }
        )

        generator = SettlementGenerator(llm)
        settlement = await generator.generate(
            "Redmarch",
            "A coastal campaign setting",
        )

        assert settlement.name == "Redmarch"

    @pytest.mark.asyncio
    async def test_settlement_generator_raises_on_invalid_json(self):
        """Generator raises error if LLM returns invalid JSON."""
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value={"text": "{ invalid json"})

        generator = SettlementGenerator(llm)

        with pytest.raises(ValueError, match="invalid JSON"):
            await generator.generate("Redmarch", "A coastal campaign setting")

    @pytest.mark.asyncio
    async def test_settlement_generator_injects_campaign_context(self):
        """Generator includes campaign context in prompt."""
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value={
                "text": """{
                    "settlement_id": "test",
                    "name": "Test",
                    "region": "Test Region",
                    "population": 100,
                    "character": "Test",
                    "buildings": [],
                    "npcs": [],
                    "factions": []
                }"""
            }
        )

        generator = SettlementGenerator(llm)
        campaign_context = "The Shattered Coast: a land of sea-faring merchants and pirates"

        await generator.generate(
            "Redmarch",
            campaign_context,
            population_hint="trade town",
        )

        # Verify campaign context was included in the LLM call
        call_args = llm.generate.call_args
        prompt = call_args[0][0]
        assert campaign_context in prompt
        assert "trade town" in prompt

    @pytest.mark.asyncio
    async def test_settlement_generator_uses_faction_hooks(self):
        """Generator includes specified faction hooks in prompt."""
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value={
                "text": """{
                    "settlement_id": "test",
                    "name": "Test",
                    "region": "Test Region",
                    "population": 100,
                    "character": "Test",
                    "buildings": [],
                    "npcs": [],
                    "factions": []
                }"""
            }
        )

        generator = SettlementGenerator(llm)
        faction_hooks = ["Thieves Guild", "Church of the Sun"]

        await generator.generate(
            "Redmarch",
            "Campaign context",
            faction_hooks=faction_hooks,
        )

        call_args = llm.generate.call_args
        prompt = call_args[0][0]
        assert "Thieves Guild" in prompt
        assert "Church of the Sun" in prompt
