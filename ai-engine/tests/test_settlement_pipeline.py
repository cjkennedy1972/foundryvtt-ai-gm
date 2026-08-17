"""Integration test for settlement pipeline: generation -> serialization -> deserialization."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from campaign.settlement_integration import (
    SettlementIntegration,
    serialize_settlements,
    deserialize_settlements,
)


class TestSettlementPipeline(unittest.TestCase):
    """Test the full settlement pipeline from generation to persistence."""

    def test_serialize_deserialize_roundtrip(self):
        """Settlements survive serialization -> deserialization roundtrip."""
        # Create a mock settlement with all fields populated
        from world.settlement import Settlement, Building, SettlementNPC, Faction

        settlement = Settlement(
            id="test_town",
            name="Test Town",
            region="Test Region",
            population=500,
            character="A bustling trade town",
            buildings={
                "tavern_1": Building(
                    id="tavern_1",
                    name="The Wandering Star",
                    building_type="tavern",
                    services=["lodging", "ale"],
                    occupants=["mara", "kess"],
                    inventory={"ale": 20, "bread": 50},
                    description="Cozy tavern",
                )
            },
            npcs={
                "mara": SettlementNPC(
                    npc_id="mara",
                    npc_name="Mara",
                    occupation="tavern keeper",
                    primary_building="tavern_1",
                    personality="sharp-witted",
                    secret="smuggles rare goods",
                    relationships={"kess": "sibling"},
                    schedule={"dawn": "tavern_1", "noon": "market", "dusk": "tavern_1"},
                    goals=["expand tavern", "find lost relic"],
                )
            },
            factions={
                "thieves_guild": Faction(
                    id="thieves_guild",
                    name="Thieves Guild",
                    description="Underground crime syndicate",
                    power_level=7,
                    leader="mara",
                    members=["mara", "kess"],
                    rivals=["town_guard"],
                    goals=["expand territory"],
                )
            },
            notes=["Important settlement for Act I"],
        )

        # Serialize
        serialized = serialize_settlements({"test_town": settlement})
        # Verify it's JSON-serializable
        json.dumps(serialized)

        # Deserialize
        deserialized = deserialize_settlements(serialized)

        # Verify structure is preserved
        assert "test_town" in deserialized
        s = deserialized["test_town"]
        assert s.name == "Test Town"
        assert s.population == 500
        assert len(s.buildings) == 1
        assert len(s.npcs) == 1
        assert len(s.factions) == 1
        assert s.npcs["mara"].npc_name == "Mara"
        assert s.npcs["mara"].schedule["dusk"] == "tavern_1"
        assert s.factions["thieves_guild"].power_level == 7

    def test_json_roundtrip(self):
        """Settlements can be JSON serialized and reconstructed."""
        from world.settlement import Settlement, Building, SettlementNPC

        settlement = Settlement(
            id="red_march",
            name="Redmarch",
            region="Northern Territories",
            population=1200,
            character="Fortress town on the river",
            buildings={"keep": Building(id="keep", name="Keep", building_type="castle")},
            npcs={
                "commander": SettlementNPC(
                    npc_id="commander",
                    npc_name="Commander",
                    occupation="garrison commander",
                    primary_building="keep",
                    personality="stern",
                    schedule={"morning": "keep", "afternoon": "keep", "dusk": "tavern"},
                )
            },
        )

        # Full JSON roundtrip
        settlements_dict = {"red_march": settlement}
        serialized = serialize_settlements(settlements_dict)
        json_str = json.dumps(serialized)
        deserialized_dict = json.loads(json_str)
        final_settlements = deserialize_settlements(deserialized_dict)

        assert "red_march" in final_settlements
        s = final_settlements["red_march"]
        assert s.name == "Redmarch"
        assert s.npcs["commander"].schedule["dusk"] == "tavern"


if __name__ == "__main__":
    unittest.main()
