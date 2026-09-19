"""Dynamic difficulty scaling for D&D 5e encounters."""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# DMG experience point value per challenge rating.
XP_BY_CR: Dict[float, int] = {
    0: 10, 1 / 8: 25, 1 / 4: 50, 1 / 2: 100,
    1: 200, 2: 450, 3: 700, 4: 1100,
    5: 1800, 6: 2300, 7: 2900, 8: 3900,
    9: 5000, 10: 5900, 11: 7200, 12: 8400,
    13: 10000, 14: 11500, 15: 13000, 16: 15000,
    17: 18000, 18: 20000, 19: 22000, 20: 25000,
}


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
    ai_companions: int = 0

    @property
    def effective_num_players(self) -> int:
        """Number of combatants contributing to encounter action economy."""
        return max(1, self.num_players + max(0, self.ai_companions))

    @property
    def party_power_rating(self) -> float:
        """Calculate party power rating (1.0 is baseline)."""
        rating = 1.0

        # DMG encounter math assumes four characters. Solo and duet parties
        # have less redundancy and action economy, so rate them conservatively.
        if self.effective_num_players == 1:
            rating *= 0.5
        elif self.effective_num_players == 2:
            rating *= 0.7
        elif self.effective_num_players == 3:
            rating *= 0.8  # Small party less powerful
        elif self.effective_num_players >= 5:
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
        self.total_xp = sum(XP_BY_CR.get(cr, 0) for cr in self.monster_crs)


class DynamicDifficulty:
    """Calculate and adjust encounter difficulty dynamically."""

    # DMG XP thresholds PER CHARACTER, by character level. A party's budget is
    # the sum across its characters, so both level and headcount move it.
    #
    # The previous table was keyed by player count while holding per-level
    # numbers, and calculate_difficulty read party.avg_level into a local it
    # never used — so level did not reach the rating at all and four wolves
    # scored the same against level 1 as against level 20.
    XP_THRESHOLDS_BY_LEVEL: Dict[int, Dict[str, float]] = {
        1:  {"easy": 25,   "medium": 50,   "hard": 75,   "deadly": 100},
        2:  {"easy": 50,   "medium": 100,  "hard": 150,  "deadly": 200},
        3:  {"easy": 75,   "medium": 150,  "hard": 225,  "deadly": 400},
        4:  {"easy": 125,  "medium": 250,  "hard": 375,  "deadly": 500},
        5:  {"easy": 250,  "medium": 500,  "hard": 750,  "deadly": 1100},
        6:  {"easy": 300,  "medium": 600,  "hard": 900,  "deadly": 1400},
        7:  {"easy": 350,  "medium": 750,  "hard": 1100, "deadly": 1700},
        8:  {"easy": 450,  "medium": 900,  "hard": 1400, "deadly": 2100},
        9:  {"easy": 550,  "medium": 1100, "hard": 1600, "deadly": 2400},
        10: {"easy": 600,  "medium": 1200, "hard": 1900, "deadly": 2800},
        11: {"easy": 800,  "medium": 1600, "hard": 2400, "deadly": 3600},
        12: {"easy": 1000, "medium": 2000, "hard": 3000, "deadly": 4500},
        13: {"easy": 1100, "medium": 2200, "hard": 3400, "deadly": 5100},
        14: {"easy": 1250, "medium": 2500, "hard": 3800, "deadly": 5700},
        15: {"easy": 1400, "medium": 2800, "hard": 4300, "deadly": 6400},
        16: {"easy": 1600, "medium": 3200, "hard": 4800, "deadly": 7200},
        17: {"easy": 2000, "medium": 3900, "hard": 5900, "deadly": 8800},
        18: {"easy": 2100, "medium": 4200, "hard": 6300, "deadly": 9500},
        19: {"easy": 2400, "medium": 4900, "hard": 7300, "deadly": 10900},
        20: {"easy": 2800, "medium": 5700, "hard": 8500, "deadly": 12700},
    }

    def party_budget(self, party: PartyComposition) -> Dict[str, float]:
        """XP thresholds for this whole party, summed across its characters.

        "trivial" is half the easy threshold: the DMG publishes no such band,
        but EncounterDifficulty has one and suggest_encounters is reachable
        from /api/combat/encounter-suggestions, where a missing key was a 500.
        """
        level = max(1, min(20, int(party.avg_level)))
        per_character = self.XP_THRESHOLDS_BY_LEVEL[level]
        headcount = party.effective_num_players
        budget = {k: v * headcount for k, v in per_character.items()}
        budget["trivial"] = budget["easy"] / 2
        return budget

    def __init__(self):
        pass

    def get_party_composition(
        self, player_count: int, avg_level: float,
        roles: Optional[List[str]] = None, ai_companions: int = 0
    ) -> PartyComposition:
        """Create party composition profile."""
        roles = roles or []
        return PartyComposition(
            num_players=player_count,
            avg_level=avg_level,
            ai_companions=ai_companions,
            has_healer="cleric" in roles or "druid" in roles or "bard" in roles,
            has_tank="fighter" in roles or "paladin" in roles or "barbarian" in roles,
            has_damage_dealer="rogue" in roles or "sorcerer" in roles or "ranger" in roles,
            has_controller="wizard" in roles or "warlock" in roles,
        )

    @staticmethod
    def encounter_multiplier(monster_count: int) -> float:
        """DMG encounter multiplier for fighting several monsters at once.

        Two CR-1 monsters are harder than their XP sum because they get two
        turns. Without this the raw sum under-rated every group fight, which
        is most of what the GM generates.
        """
        if monster_count <= 1:
            return 1.0
        if monster_count == 2:
            return 1.5
        if monster_count <= 6:
            return 2.0
        if monster_count <= 10:
            return 2.5
        if monster_count <= 14:
            return 3.0
        return 4.0

    def calculate_difficulty(
        self, encounter: EncounterProfile, party: PartyComposition
    ) -> EncounterDifficulty:
        """Rate an encounter against this party's own XP budget."""
        adjusted_xp = (
            encounter.total_xp * self.encounter_multiplier(len(encounter.monster_crs))
            / party.party_power_rating
        )
        budget = self.party_budget(party)

        # A band starts AT its threshold. The old ladder tested `<=` going
        # upward, so an encounter sitting exactly on the deadly threshold came
        # back HARD and every band read one softer than the DMG defines it.
        if adjusted_xp >= budget["deadly"]:
            return EncounterDifficulty.DEADLY
        elif adjusted_xp >= budget["hard"]:
            return EncounterDifficulty.HARD
        elif adjusted_xp >= budget["medium"]:
            return EncounterDifficulty.MEDIUM
        elif adjusted_xp >= budget["easy"]:
            return EncounterDifficulty.EASY
        else:
            return EncounterDifficulty.TRIVIAL

    def suggest_encounters(
        self, party: PartyComposition, difficulty: EncounterDifficulty,
        num_suggestions: int = 3
    ) -> List[Dict]:
        """Suggest encounters appropriate for a party."""
        level = int(party.avg_level)
        xp_budget = self.party_budget(party)[difficulty.value]

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
        if len(encounter.monster_names) < party.effective_num_players / 2:
            recommendations.append("Few monsters vs. many players - consider adding minions")

        if len(encounter.monster_names) > party.effective_num_players * 2:
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
