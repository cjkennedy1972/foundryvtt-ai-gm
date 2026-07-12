"""Rules engine for D&D 5e reference and calculations."""

import logging
from typing import Optional, Dict, List, Tuple
from rules.database import (
    CONDITIONS, SPELLS, SKILL_ABILITIES, DC_BY_DIFFICULTY,
    CLASS_HIT_DICE, ABILITY_SCORES
)

logger = logging.getLogger(__name__)


class RulesEngine:
    """Access and interpret D&D 5e rules."""

    def __init__(self):
        self.conditions = CONDITIONS
        self.spells = SPELLS
        self.skill_abilities = SKILL_ABILITIES
        self.dc_by_difficulty = DC_BY_DIFFICULTY
        self.class_hit_dice = CLASS_HIT_DICE
        self.ability_scores = ABILITY_SCORES

    def get_spell(self, spell_name: str) -> Optional[Dict]:
        """Look up a spell by name."""
        return self.spells.get(spell_name.lower())

    def search_spells(self, query: str) -> List[Dict]:
        """Search spells by name or keyword."""
        query = query.lower()
        results = []
        for name, details in self.spells.items():
            if query in name or (details.get("description") and query in details["description"].lower()):
                results.append({**details, "name": name})
        return results

    def get_condition(self, condition_name: str) -> Optional[str]:
        """Look up a condition by name."""
        return self.conditions.get(condition_name.lower())

    def get_ability_modifier(self, ability_score: int) -> int:
        """Calculate ability modifier from an ability score."""
        return (ability_score - 10) // 2

    def suggest_dc(self, difficulty: str) -> int:
        """Suggest a DC based on difficulty."""
        return self.dc_by_difficulty.get(difficulty.lower(), 15)

    def calculate_proficiency_bonus(self, character_level: int) -> int:
        """Calculate proficiency bonus by character level."""
        return (character_level + 7) // 4

    def get_skill_ability(self, skill_name: str) -> Optional[str]:
        """Get the ability associated with a skill."""
        return self.skill_abilities.get(skill_name.lower())

    def calculate_skill_modifier(
        self, ability_modifier: int, is_proficient: bool, character_level: int
    ) -> int:
        """Calculate total skill modifier."""
        proficiency = self.calculate_proficiency_bonus(character_level) if is_proficient else 0
        return ability_modifier + proficiency

    def suggest_skill_dc(
        self, skill_name: str, difficulty: str = "medium", target_level: int = 5
    ) -> Dict:
        """Suggest a DC for a skill check."""
        base_dc = self.suggest_dc(difficulty)
        ability = self.get_skill_ability(skill_name)
        return {
            "skill": skill_name,
            "ability": ability,
            "dc": base_dc,
            "difficulty": difficulty,
            "notes": f"A typical {skill_name} check vs {ability}",
        }

    def get_hit_die(self, class_name: str) -> Optional[int]:
        """Get hit die size for a class."""
        return self.class_hit_dice.get(class_name.lower())

    def is_advantage_condition(self, condition: str) -> bool:
        """Check if a condition grants advantage on rolls."""
        advantage_conditions = {
            "prone",  # against melee
            "invisible",  # attack rolls against target
            "restrained",  # attack rolls against target
        }
        return condition.lower() in advantage_conditions

    def is_disadvantage_condition(self, condition: str) -> bool:
        """Check if a condition imposes disadvantage on rolls."""
        disadvantage_conditions = {
            "blinded",
            "charmed",
            "frightened",
            "paralyzed",
            "petrified",
            "poisoned",
            "prone",  # with its own attacks
            "restrained",  # with its own attacks
            "stunned",
            "unconscious",
        }
        return condition.lower() in disadvantage_conditions

    def reference_summary(self) -> Dict:
        """Return a summary of available rules."""
        return {
            "spells": len(self.spells),
            "conditions": len(self.conditions),
            "skills": len(self.skill_abilities),
            "classes": len(self.class_hit_dice),
            "abilities": list(self.ability_scores.keys()),
        }
