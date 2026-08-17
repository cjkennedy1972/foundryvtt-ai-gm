"""Settlement integration — Generate and persist settlements from campaign data.

Wires settlements into the campaign build pipeline:
1. Extract settlement names from campaign locations
2. Generate complete Settlement objects with buildings, NPCs, schedules
3. Store in campaign vault
4. Load and register with WorldClockAgent at session start
"""

import json
import logging
from typing import Dict, List, Optional, Any

from world.settlement import Settlement
from world.settlement_generator import SettlementGenerator

logger = logging.getLogger(__name__)


class SettlementIntegration:
    """Generates and integrates settlements into campaigns."""

    def __init__(self, llm_manager):
        self.llm = llm_manager
        self.generator = SettlementGenerator(llm_manager)

    async def generate_settlements_from_campaign(
        self,
        campaign_data: Dict[str, Any],
        campaign_context: str,
        max_settlements: int = 3,
    ) -> Dict[str, Settlement]:
        """Generate settlements from campaign data.

        Extracts settlement names from campaign locations, generates full
        Settlement objects with buildings, NPCs, schedules, factions.

        Args:
            campaign_data: Campaign dict with 'locations' or 'scenes'
            campaign_context: Campaign lore/setting for LLM context
            max_settlements: Max settlements to generate

        Returns:
            Dict of settlement_id -> Settlement
        """
        settlements = {}

        # Extract settlement names from locations or scenes
        settlement_names = self._extract_settlement_names(campaign_data, max_settlements)
        if not settlement_names:
            logger.info("No settlements to generate")
            return {}

        logger.info(f"Generating {len(settlement_names)} settlement(s)")
        for name in settlement_names:
            try:
                settlement = await self.generator.generate(
                    settlement_name=name,
                    campaign_context=campaign_context,
                    population_hint=self._infer_population_hint(campaign_data, name),
                )
                settlements[settlement.id] = settlement
                logger.info(f"Generated settlement: {name} ({settlement.id})")
            except Exception as e:
                logger.warning(f"Failed to generate settlement '{name}': {e}")

        return settlements

    def _extract_settlement_names(
        self, campaign_data: Dict[str, Any], limit: int = 3
    ) -> List[str]:
        """Extract settlement names from campaign locations or scenes.

        Looks for location names that sound like settlements (not dungeons,
        natural features, etc.) and returns up to `limit` names.
        """
        names = set()

        # Try locations first
        locations = campaign_data.get("locations", [])
        if isinstance(locations, list):
            for loc in locations:
                if isinstance(loc, dict):
                    name = loc.get("name", "")
                    location_type = loc.get("type", "").lower()
                    # Include towns, villages, cities, settlements; skip dungeons, ruins, etc.
                    if self._is_settlement_type(location_type):
                        names.add(name)

        # Fall back to scenes if no locations
        if not names:
            scenes = campaign_data.get("scenes", [])
            if isinstance(scenes, list):
                for scene in scenes:
                    if isinstance(scene, dict):
                        name = scene.get("name", "")
                        description = scene.get("description", "").lower()
                        # Simple heuristic: tavern, town, village, city, settlement mentions
                        if any(
                            word in description
                            for word in ["tavern", "town", "village", "city", "settlement", "market"]
                        ):
                            names.add(name)

        return list(names)[: limit]

    def _is_settlement_type(self, location_type: str) -> bool:
        """Check if location_type indicates a settlement."""
        settlement_indicators = {
            "town",
            "village",
            "city",
            "settlement",
            "outpost",
            "fort",
            "stronghold",
            "marketplace",
        }
        return any(indicator in location_type for indicator in settlement_indicators)

    def _infer_population_hint(self, campaign_data: Dict[str, Any], name: str) -> str:
        """Infer settlement size from campaign data."""
        # Simple heuristic: check location description for size indicators
        locations = campaign_data.get("locations", [])
        if isinstance(locations, list):
            for loc in locations:
                if isinstance(loc, dict) and loc.get("name") == name:
                    desc = loc.get("description", "").lower()
                    if any(word in desc for word in ["major", "capital", "great"]):
                        return "large city"
                    if any(word in desc for word in ["small", "tiny", "minor"]):
                        return "small village"
                    if any(word in desc for word in ["trade", "crossing", "hub"]):
                        return "trade town"
        return "trade town"  # default


def serialize_settlements(settlements: Dict[str, Settlement]) -> Dict[str, Any]:
    """Convert Settlement objects to JSON-serializable dicts for vault storage."""
    result = {}
    for sid, settlement in settlements.items():
        result[sid] = {
            "id": settlement.id,
            "name": settlement.name,
            "region": settlement.region,
            "population": settlement.population,
            "character": settlement.character,
            "buildings": {
                bid: {
                    "id": b.id,
                    "name": b.name,
                    "building_type": b.building_type,
                    "services": b.services,
                    "occupants": b.occupants,
                    "inventory": b.inventory,
                    "description": b.description,
                    "notes": b.notes,
                }
                for bid, b in settlement.buildings.items()
            },
            "npcs": {
                nid: {
                    "npc_id": npc.npc_id,
                    "npc_name": npc.npc_name,
                    "occupation": npc.occupation,
                    "primary_building": npc.primary_building,
                    "personality": npc.personality,
                    "secret": npc.secret,
                    "relationships": npc.relationships,
                    "schedule": npc.schedule,
                    "goals": npc.goals,
                }
                for nid, npc in settlement.npcs.items()
            },
            "factions": {
                fid: {
                    "id": f.id,
                    "name": f.name,
                    "description": f.description,
                    "power_level": f.power_level,
                    "leader": f.leader,
                    "members": f.members,
                    "rivals": f.rivals,
                    "goals": f.goals,
                }
                for fid, f in settlement.factions.items()
            },
            "time_of_day_cycle": settlement.time_of_day_cycle,
            "notes": settlement.notes,
        }
    return result


def deserialize_settlements(data: Dict[str, Any]) -> Dict[str, Settlement]:
    """Reconstruct Settlement objects from vault JSON."""
    from world.settlement import Building, SettlementNPC, Faction

    settlements = {}
    for sid, s_data in data.items():
        # Rebuild buildings
        buildings = {}
        for bid, b_data in s_data.get("buildings", {}).items():
            buildings[bid] = Building(
                id=b_data["id"],
                name=b_data["name"],
                building_type=b_data["building_type"],
                services=b_data.get("services", []),
                occupants=b_data.get("occupants", []),
                inventory=b_data.get("inventory", {}),
                description=b_data.get("description", ""),
                notes=b_data.get("notes", []),
            )

        # Rebuild NPCs
        npcs = {}
        for nid, n_data in s_data.get("npcs", {}).items():
            npcs[nid] = SettlementNPC(
                npc_id=n_data["npc_id"],
                npc_name=n_data["npc_name"],
                occupation=n_data["occupation"],
                primary_building=n_data["primary_building"],
                personality=n_data.get("personality", ""),
                secret=n_data.get("secret"),
                relationships=n_data.get("relationships", {}),
                schedule=n_data.get("schedule", {}),
                goals=n_data.get("goals", []),
            )

        # Rebuild factions
        factions = {}
        for fid, f_data in s_data.get("factions", {}).items():
            factions[fid] = Faction(
                id=f_data["id"],
                name=f_data["name"],
                description=f_data["description"],
                power_level=f_data.get("power_level", 5),
                leader=f_data.get("leader"),
                members=f_data.get("members", []),
                rivals=f_data.get("rivals", []),
                goals=f_data.get("goals", []),
            )

        # Rebuild settlement
        settlements[sid] = Settlement(
            id=s_data["id"],
            name=s_data["name"],
            region=s_data.get("region", ""),
            population=s_data.get("population", 0),
            character=s_data.get("character", ""),
            buildings=buildings,
            npcs=npcs,
            factions=factions,
            time_of_day_cycle=s_data.get("time_of_day_cycle", []),
            notes=s_data.get("notes", []),
        )

    return settlements
