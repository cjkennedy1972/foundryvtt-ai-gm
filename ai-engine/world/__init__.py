"""World generation and management — settlements, locations, NPCs with schedules."""

from world.settlement import Settlement, Building, SettlementNPC, Faction
from world.settlement_generator import SettlementGenerator

__all__ = [
    "Settlement",
    "Building",
    "SettlementNPC",
    "Faction",
    "SettlementGenerator",
]
