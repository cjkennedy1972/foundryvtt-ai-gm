"""Settlement schema — towns as queryable, living entities with NPCs, buildings, schedules.

A settlement is generated once per campaign and persisted. It contains:
- Buildings with types, services, occupants, and inventories
- NPCs with occupations, daily schedules, and relationships
- Factions representing power groups
- Time-of-day location mappings for schedule-based NPC queries

Key capability: "Who is in the tavern at dusk?" → queryable by time-of-day.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Building:
    """A structure in a settlement (tavern, blacksmith, temple, etc.)."""

    id: str  # unique within settlement
    name: str
    building_type: str  # "tavern", "blacksmith", "temple", "shop", "residence", etc.
    services: List[str] = field(default_factory=list)  # ["lodging", "ale", "information"]
    occupants: List[str] = field(default_factory=list)  # NPC IDs who work here normally
    inventory: Dict[str, int] = field(default_factory=dict)  # item -> quantity (for shops)
    description: str = ""  # flavor text
    notes: List[str] = field(default_factory=list)

    def __hash__(self):
        return hash(self.id)


@dataclass
class SettlementNPC:
    """An NPC as part of a living settlement (with occupation and schedule)."""

    npc_id: str
    npc_name: str
    occupation: str  # "tavern keeper", "blacksmith", "street urchin", "hedge wizard"
    primary_building: str  # building_id where they work
    personality: str  # short descriptor
    secret: Optional[str] = None  # a hook for the GM
    relationships: Dict[str, str] = field(default_factory=dict)  # npc_id -> relationship_type
    # Schedule: time_of_day -> building_id (where they are at that time)
    schedule: Dict[str, str] = field(default_factory=dict)
    goals: List[str] = field(default_factory=list)  # motivations/secrets


@dataclass
class Faction:
    """A power group within the settlement."""

    id: str
    name: str
    description: str
    power_level: int  # 1-10, where 10 is total control
    leader: Optional[str] = None  # NPC ID
    members: List[str] = field(default_factory=list)  # NPC IDs
    rivals: List[str] = field(default_factory=list)  # Faction IDs
    goals: List[str] = field(default_factory=list)


@dataclass
class Settlement:
    """A town/village as a queryable, living entity."""

    id: str
    name: str
    region: str  # part of campaign world
    population: int  # approximate
    character: str  # cultural/thematic description
    buildings: Dict[str, Building] = field(default_factory=dict)  # building_id -> Building
    npcs: Dict[str, SettlementNPC] = field(default_factory=dict)  # npc_id -> SettlementNPC
    factions: Dict[str, Faction] = field(default_factory=dict)  # faction_id -> Faction
    time_of_day_cycle: List[str] = field(
        default_factory=lambda: ["dawn", "morning", "noon", "afternoon", "dusk", "night"]
    )
    notes: List[str] = field(default_factory=list)

    def query_location_at_time(self, time_of_day: str) -> Dict[str, List[str]]:
        """Return {location: [npc_ids]} for a given time of day.

        Example: {"tavern": ["mara", "kess"], "market": ["elder_tobias"]}
        """
        result: Dict[str, List[str]] = {}

        for npc_id, npc in self.npcs.items():
            location = npc.schedule.get(time_of_day)
            if location:
                if location not in result:
                    result[location] = []
                result[location].append(npc_id)

        return result

    def npc_occupation(self, npc_id: str) -> Optional[str]:
        """Get the occupation of an NPC in this settlement."""
        npc = self.npcs.get(npc_id)
        return npc.occupation if npc else None

    def npc_building(self, npc_id: str) -> Optional[str]:
        """Get the primary building ID for an NPC."""
        npc = self.npcs.get(npc_id)
        return npc.primary_building if npc else None

    def building_occupants_at_time(self, building_id: str, time_of_day: str) -> List[str]:
        """Get all NPCs at a specific building at a specific time."""
        locations = self.query_location_at_time(time_of_day)
        return locations.get(building_id, [])

    def describe_npc_brief(self, npc_id: str) -> str:
        """One-liner description of an NPC for GM narration."""
        npc = self.npcs.get(npc_id)
        if not npc:
            return ""
        building = self.buildings.get(npc.primary_building)
        building_name = building.name if building else npc.primary_building
        return f"{npc.npc_name}, {npc.occupation} at the {building_name}"
