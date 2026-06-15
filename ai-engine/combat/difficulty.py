"""Dynamic difficulty scaling for D&D 5e encounters."""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class EncounterDifficulty(Enum):
    """Encounter difficulty ratings."""
    TRIVIAL = "trivial"      # XP threshold = avg_party_level * 10
    EASY = "easy"             # XP threshold = avg_party_level * 25
    MEDIUM = "medium"         # XP threshold = avg_party_level * 75
    HARD = "hard"             # XP threshold = avg_party_level * 125
    DEADLY = "deadly"         # XP threshold = avg_party_level * 250


@dataclass
class PartyComposition:
    """Composition of the player party."""
    num_players: int
    avg_level: float
    has_healer: bool = False
    has_tank: bool = False
    has_damage_dealer: bool = False
    has_controller: bool = False

    @property
    def party_power_rating(self) -> float:
        """Calculate party power rating (1.0 is baseline)."""
        rating = 1.0

        # More party members = higher power
        if self.num_players == 3:
            rating *= 0.8  # Small party less powerful
        elif self.num_players >= 5:
            rating *= 1.2  # Large party more powerful

        # Composition bonuses
        if self.has_healer:
            rating *= 1.15
        if self.has_tank:
            rating *= 1.1
        if self.has_damage_dealer:
            rating *= 1.1
        if self.has_controller:
            rating *= 1.05

        return rating


@dataclass
class EncounterProfile:
    """Profile of an encounter (monsters/NPCs to fight)."""
    monster_names: List[str]
    monster_crs: List[float]  # Challenge ratings
    total_xp: float = 0.0

    def __post_init__(self):
        # Calculate total XP
        xp_values = {
            0: 10, 1/8: 25, 1/4: 50, 1/2: 100,
            1: 200, 2: 450, 3: 700, 4: 1100,
            5: 1800, 6: 2300, 7: 2900, 8: 3900,
            9: 5000, 10: 5900, 11: 7200, 12: 8400,
            13: 10000, 14: 11500, 15: 13000, 16: 15000,
            17: 18000, 18: 20000, 19: 22000, 20: 25000,
        }
        self.total_xp = sum(xp_values.get(cr, 0) for cr in self.monster_crs)


class DynamicDifficulty:
    """Calculate and adjust encounter difficulty dynamically."""

    def __init__(self):
        self.encounter_budget: Dict[int, Dict[str, float]] = {
            # Adjusted XP per player per difficulty level
            1: {"easy": 25, "medium": 75, "hard": 125, "deadly": 250},
            2: {"easy": 50, "medium": 150, "hard": 250, "deadly": 500},
            3: {"easy": 60, "medium": 180, "hard": 300, "deadly": 600},
            4: {"easy": 75, "medium": 225, "hard": 375, "deadly": 750},
            5: {"easy": 90, "medium": 270, "hard": 450, "deadly": 900},
            6: {"easy": 105, "medium": 315, "hard": 525, "deadly": 1050},
        }

    def get_party_composition(
        self, player_count: int, avg_level: float,
        roles: Optional[List[str]] = None
    ) -> PartyComposition:
        """Create party composition profile."""
        roles = roles or []
        return PartyComposition(
            num_players=player_count,
            avg_level=avg_level,
            has_healer="cleric" in roles or "druid" in roles or "bard" in roles,
            has_tank="fighter" in roles or "paladin" in roles or "barbarian" in roles,
            has_damage_dealer="rogue" in roles or "sorcerer" in roles or "ranger" in roles,
            has_controller="wizard" in roles or "warlock" in roles,
        )

    def calculate_difficulty(
        self, encounter: EncounterProfile, party: PartyComposition
    ) -> EncounterDifficulty:
        """Calculate encounter difficulty based on party and monsters."""
        # Adjust total XP by party power rating
        adjusted_xp = encounter.total_xp / party.party_power_rating

        # Get difficulty budget for this party
        player_count = min(party.num_players, 6)  # Cap at 6 for budget lookup
        level = int(party.avg_level)
        budget = self.encounter_budget.get(player_count, self.encounter_budget[4])

        # Compare adjusted XP to thresholds
        if adjusted_xp <= budget["easy"]:
            return EncounterDifficulty.TRIVIAL
        elif adjusted_xp <= budget["medium"]:
            return EncounterDifficulty.EASY
        elif adjusted_xp <= budget["hard"]:
            return EncounterDifficulty.MEDIUM
        elif adjusted_xp <= budget["deadly"]:
            return EncounterDifficulty.HARD
        else:
            return EncounterDifficulty.DEADLY

    def suggest_encounters(
        self, party: PartyComposition, difficulty: EncounterDifficulty,
        num_suggestions: int = 3
    ) -> List[Dict]:
        """Suggest encounters appropriate for a party."""
        player_count = min(party.num_players, 6)
        level = int(party.avg_level)
        budget = self.encounter_budget.get(player_count, self.encounter_budget[4])
        xp_budget = budget[difficulty.value]

        suggestions = []

        # Suggest various monster combinations
        suggestions.append({
            "name": f"Single {difficulty.value.title()} Boss",
            "description": f"One challenging monster worth ~{xp_budget} XP",
            "suggested_cr": level + (1 if difficulty == EncounterDifficulty.DEADLY else 0),
            "xp": xp_budget,
        })

        suggestions.append({
            "name": f"Small Group ({difficulty.value.title()})",
            "description": f"4-6 smaller monsters totaling ~{xp_budget} XP",
            "monster_count": 4,
            "suggested_cr_each": max(0.25, level - 2),
            "xp": xp_budget,
        })

        suggestions.append({
            "name": f"Mixed Encounter ({difficulty.value.title()})",
            "description": f"2-3 medium monsters totaling ~{xp_budget} XP",
            "monster_count": 3,
            "suggested_cr_each": level - 1,
            "xp": xp_budget,
        })

        return suggestions[:num_suggestions]

    def get_action_recommendations(
        self, encounter: EncounterProfile, party: PartyComposition
    ) -> List[str]:
        """Get recommendations for adjusting encounter difficulty mid-combat."""
        difficulty = self.calculate_difficulty(encounter, party)
        recommendations = []

        if difficulty == EncounterDifficulty.TRIVIAL:
            recommendations.append("Consider adding more monsters or a tougher enemy")
            recommendations.append("This encounter is too easy for the party")

        elif difficulty == EncounterDifficulty.DEADLY:
            recommendations.append("Consider removing some monsters to balance difficulty")
            recommendations.append("This encounter is deadly - ensure party is prepared")

        elif difficulty == EncounterDifficulty.HARD:
            recommendations.append("This is a challenging encounter - monitor party health")
            recommendations.append("Consider using environmental hazards to increase tension")

        # Add action economy recommendations
        if len(encounter.monster_names) < party.num_players / 2:
            recommendations.append("Few monsters vs. many players - consider adding minions")

        if len(encounter.monster_names) > party.num_players * 2:
            recommendations.append("Many monsters vs. few players - consider reducing enemy count")

        return recommendations

    def scale_encounter_hp(
        self, current_difficulty: EncounterDifficulty,
        desired_difficulty: EncounterDifficulty
    ) -> float:
        """Get HP scaling factor to adjust encounter difficulty."""
        difficulty_order = [
            EncounterDifficulty.TRIVIAL,
            EncounterDifficulty.EASY,
            EncounterDifficulty.MEDIUM,
            EncounterDifficulty.HARD,
            EncounterDifficulty.DEADLY,
        ]

        current_idx = difficulty_order.index(current_difficulty)
        desired_idx = difficulty_order.index(desired_difficulty)
        diff = desired_idx - current_idx

        # 0.7x HP for each step easier, 1.4x HP for each step harder
        return 1.4 ** diff
