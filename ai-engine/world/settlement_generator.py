"""Settlement generator — LLM-powered generation of towns with NPCs, buildings, schedules."""

import json
import logging
from typing import List, Optional

from world.settlement import Settlement, Building, SettlementNPC, Faction

logger = logging.getLogger(__name__)


class SettlementGenerator:
    """Generate settlements for a campaign with LLM."""

    def __init__(self, llm_manager):
        self.llm = llm_manager

    async def generate(
        self,
        settlement_name: str,
        campaign_context: str,
        population_hint: str = "small village",
        faction_hooks: Optional[List[str]] = None,
    ) -> Settlement:
        """Generate a settlement for a campaign.

        Args:
            settlement_name: Name of the settlement to generate
            campaign_context: Campaign lore/setting (injected from vault)
            population_hint: "small village", "trade town", "city quarter", etc.
            faction_hooks: Optional faction names to weave in (e.g., ["Thieves Guild", "Church of the Sun"])

        Returns:
            Settlement object with full structure (buildings, NPCs, schedules, factions)
        """
        faction_context = ""
        if faction_hooks:
            faction_context = f"\nExisting factions to include: {', '.join(faction_hooks)}"

        prompt = f"""Generate a complete settlement for a fantasy D&D campaign.

Campaign Setting:
{campaign_context}

Settlement Details:
- Name: {settlement_name}
- Size: {population_hint}
- Population: [estimate a number appropriate for the size]
- Character: [1-2 sentence cultural/thematic description]{faction_context}

Generate a JSON object with this structure:
{{
  "settlement_id": "lowercase_snake_case_id",
  "name": "{settlement_name}",
  "region": "region from campaign",
  "population": NUMBER,
  "character": "brief description",
  "buildings": [
    {{
      "id": "building_id",
      "name": "Building Name",
      "building_type": "tavern|shop|temple|residence|smithy|market|etc",
      "services": ["service1", "service2"],
      "occupants": ["npc_id1"],
      "inventory": {{"item": quantity}},
      "description": "flavor text"
    }}
  ],
  "npcs": [
    {{
      "npc_id": "npc_id",
      "npc_name": "NPC Name",
      "occupation": "occupation/role",
      "primary_building": "building_id",
      "personality": "1-2 word descriptor",
      "secret": "optional rumor or hook",
      "relationships": {{"other_npc_id": "relationship_type"}},
      "schedule": {{"dawn": "building_id", "morning": "building_id", ...}},
      "goals": ["motivation1", "motivation2"]
    }}
  ],
  "factions": [
    {{
      "id": "faction_id",
      "name": "Faction Name",
      "description": "brief description",
      "power_level": 1-10,
      "leader": "npc_id",
      "members": ["npc_id1", "npc_id2"],
      "rivals": ["other_faction_id"],
      "goals": ["goal1", "goal2"]
    }}
  ],
  "notes": ["note1", "note2"]
}}

IMPORTANT:
- Generate 5-10 buildings, 8-12 NPCs, 2-4 factions
- Make it coherent: NPCs work in buildings, factions have leaders/members
- Schedule MUST have entries for all time-of-day values: dawn, morning, noon, afternoon, dusk, night
- Each NPC must be assigned to buildings in their schedule matching their occupation
- Relationships should form a web, not isolated NPCs
- Create interesting hooks and secrets for GM narration
- Keep descriptions brief but evocative
- Return ONLY the JSON object, no markdown or explanations"""

        try:
            result = await self.llm.generate(prompt, temperature=0.8)
            # Parse response (could be wrapped in markdown)
            text = result.get("text", result) if isinstance(result, dict) else result
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            gen_dict = json.loads(text)
            return self._materialize(gen_dict)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse settlement JSON: {e}")
            raise ValueError(f"Settlement generator returned invalid JSON: {e}")
        except Exception as e:
            logger.error(f"Settlement generation failed: {e}")
            raise

    def _materialize(self, gen_dict: dict) -> Settlement:
        """Convert LLM output dict to Settlement object."""
        # Build buildings
        buildings = {}
        for b_data in gen_dict.get("buildings", []):
            building = Building(
                id=b_data.get("id", ""),
                name=b_data.get("name", ""),
                building_type=b_data.get("building_type", ""),
                services=b_data.get("services", []),
                occupants=b_data.get("occupants", []),
                inventory=b_data.get("inventory", {}),
                description=b_data.get("description", ""),
            )
            buildings[building.id] = building

        # Build NPCs
        npcs = {}
        for n_data in gen_dict.get("npcs", []):
            npc = SettlementNPC(
                npc_id=n_data.get("npc_id", ""),
                npc_name=n_data.get("npc_name", ""),
                occupation=n_data.get("occupation", ""),
                primary_building=n_data.get("primary_building", ""),
                personality=n_data.get("personality", ""),
                secret=n_data.get("secret"),
                relationships=n_data.get("relationships", {}),
                schedule=n_data.get("schedule", {}),
                goals=n_data.get("goals", []),
            )
            npcs[npc.npc_id] = npc

        # Build factions
        factions = {}
        for f_data in gen_dict.get("factions", []):
            faction = Faction(
                id=f_data.get("id", ""),
                name=f_data.get("name", ""),
                description=f_data.get("description", ""),
                power_level=f_data.get("power_level", 5),
                leader=f_data.get("leader"),
                members=f_data.get("members", []),
                rivals=f_data.get("rivals", []),
                goals=f_data.get("goals", []),
            )
            factions[faction.id] = faction

        settlement = Settlement(
            id=gen_dict.get("settlement_id", "settlement"),
            name=gen_dict.get("name", "Unknown Settlement"),
            region=gen_dict.get("region", "Unknown Region"),
            population=gen_dict.get("population", 100),
            character=gen_dict.get("character", ""),
            buildings=buildings,
            npcs=npcs,
            factions=factions,
            notes=gen_dict.get("notes", []),
        )

        logger.info(
            f"Generated settlement '{settlement.name}' with {len(npcs)} NPCs, "
            f"{len(buildings)} buildings, {len(factions)} factions"
        )
        return settlement
