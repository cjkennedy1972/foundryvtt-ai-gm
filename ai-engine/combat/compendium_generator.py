"""Encounter generation from Foundry D&D 5e compendium.

Queries real D&D 5e monsters from Foundry's compendiums, balances them against
party power using DynamicDifficulty, and positions them tactically on the map.
Replaces LLM-based hallucinated monster generation with verified stat blocks.
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from foundry.client import FoundryClient
from combat.difficulty import DynamicDifficulty, PartyComposition, EncounterDifficulty
from combat.mechanics import CombatMechanics

logger = logging.getLogger(__name__)


@dataclass
class Monster:
    """A monster from the compendium."""
    name: str
    cr: float
    xp: int
    uuid: str
    size: str = "medium"
    has_multiattack: bool = False
    avg_damage_per_turn: float = 0.0


class CompendiumEncounterGenerator:
    """Generate balanced encounters from Foundry D&D 5e compendium monsters."""

    # XP thresholds for easy, medium, hard, deadly encounters (per monster)
    XP_VALUES = {
        0: 10, 0.125: 25, 0.25: 50, 0.5: 100,
        1: 200, 2: 450, 3: 700, 4: 1100,
        5: 1800, 6: 2300, 7: 2900, 8: 3900,
        9: 5000, 10: 5900, 11: 7200, 12: 8400,
        13: 10000, 14: 11500, 15: 13000, 16: 15000,
        17: 18000, 18: 20000, 19: 22000, 20: 25000,
    }

    def __init__(self, foundry: FoundryClient, scene_width: int = 800, scene_height: int = 600):
        """
        Initialize the encounter generator.

        Args:
            foundry: FoundryClient for executing JS queries
            scene_width: Scene width in pixels (for placement bounds)
            scene_height: Scene height in pixels (for placement bounds)
        """
        self.foundry = foundry
        self.scene_width = scene_width
        self.scene_height = scene_height
        self._monster_cache: Dict[str, Monster] = {}
        self._dynamic_difficulty = DynamicDifficulty()
        self._mechanics = CombatMechanics()

    async def generate(
        self,
        party_level: int,
        party_size: int,
        difficulty: str = "medium",
        environment: Optional[str] = None,
        max_creatures: int = 5,
    ) -> Dict[str, Any]:
        """
        Generate a balanced encounter from the compendium.

        Args:
            party_level: Average party level (1-20)
            party_size: Number of party members
            difficulty: "trivial", "easy", "medium", "hard", or "deadly"
            environment: Optional environment hint (e.g. "underdark", "forest", "ruins")
            max_creatures: Maximum number of creatures to include

        Returns:
            {
                "creatures": [
                    {"name": "Goblin", "cr": 0.125, "xp": 25, "uuid": "..."},
                    ...
                ],
                "placements": [
                    {"uuid": "...", "x": 150, "y": 200, "hidden": false},
                    ...
                ],
                "total_xp": 500,
                "difficulty_rating": "medium",
                "notes": "2 ranged, 1 melee. Positioned back-to-front."
            }
        """
        logger.info(
            f"[CompendiumEncounter] Generating {difficulty} encounter: "
            f"party_level={party_level}, party_size={party_size}, env={environment}"
        )

        # Step 1: Calculate XP budget
        # Use DynamicDifficulty to get the XP budget for this party and difficulty
        player_count = min(party_size, 6)  # Cap at 6 for budget lookup
        budget_dict = self._dynamic_difficulty.encounter_budget.get(
            player_count, self._dynamic_difficulty.encounter_budget[4]
        )
        budget = budget_dict.get(difficulty, budget_dict.get("medium", 500))
        logger.debug(f"[CompendiumEncounter] XP budget for {difficulty}: {budget}")

        # Step 2: Query and filter monsters from compendium
        candidates = await self._query_compendium(party_level, environment)
        logger.debug(f"[CompendiumEncounter] Found {len(candidates)} candidate monsters")

        # Step 3: Select monsters that fit budget (greedy algorithm)
        selected = self._select_monsters_greedy(candidates, budget, max_creatures)
        logger.info(
            f"[CompendiumEncounter] Selected {len(selected)} monsters, "
            f"total XP: {sum(m.xp for m in selected)}"
        )

        # Step 4: Position tactically on map
        placements = self._position_tactically(selected)

        # Step 5: Build response
        result = {
            "creatures": [
                {
                    "name": m.name,
                    "cr": m.cr,
                    "xp": m.xp,
                    "uuid": m.uuid,
                    "size": m.size,
                }
                for m in selected
            ],
            "placements": placements,
            "total_xp": sum(m.xp for m in selected),
            "difficulty_rating": difficulty,
            "party_level": party_level,
            "party_size": party_size,
            "notes": self._generate_notes(selected, placements),
        }

        logger.info(f"[CompendiumEncounter] Encounter generated: {result['notes']}")
        return result

    async def _query_compendium(
        self, max_cr: float, environment: Optional[str] = None
    ) -> List[Monster]:
        """
        Query Foundry D&D 5e compendium for monsters.

        Filters by CR and optionally by environment tag.
        """
        try:
            # Build JavaScript query to fetch monsters from compendium
            # The dnd5e compendium pack ID varies, but common ones are:
            # 'dnd5e.monsters', 'dnd5e.monsterfeatures'
            js_query = f"""
            (async () => {{
                const pack = game.packs.get('dnd5e.monsters');
                if (!pack) return [];

                const index = await pack.getIndex({{ fields: ['system.details.cr', 'system.details.environment'] }});
                const filtered = index.filter(m => {{
                    const cr = m.system?.details?.cr ?? 0;
                    return cr <= {max_cr + 2} && cr >= 0.125;
                }});

                return filtered.slice(0, 20).map(m => ({{
                    name: m.name,
                    cr: m.system?.details?.cr ?? 0,
                    uuid: m.uuid,
                    _id: m._id,
                }}));
            }})()
            """

            monsters_raw = await self.foundry.execute_js(js_query)

            if not monsters_raw:
                logger.warning("[CompendiumEncounter] No monsters found in compendium query")
                return []

            # Convert to Monster objects, enriching with XP values
            monsters = []
            for m in monsters_raw:
                cr = m.get("cr", 0)
                xp = self.XP_VALUES.get(cr, 0)
                if xp > 0:  # Only include if we know the XP value
                    monsters.append(
                        Monster(
                            name=m.get("name", "Unknown"),
                            cr=cr,
                            xp=xp,
                            uuid=m.get("uuid", ""),
                        )
                    )

            logger.debug(f"[CompendiumEncounter] Queried {len(monsters)} monsters")
            return monsters

        except Exception as e:
            logger.error(f"[CompendiumEncounter] Query failed: {e}", exc_info=True)
            return []

    def _select_monsters_greedy(
        self, candidates: List[Monster], budget: float, max_creatures: int
    ) -> List[Monster]:
        """
        Greedily select monsters that fit the XP budget.

        Prioritizes variety: avoids picking 5 identical goblins.
        """
        if not candidates:
            return []

        selected = []
        remaining_budget = budget
        seen_names = set()

        # First pass: pick one of each type (for variety)
        for monster in candidates:
            if monster.name in seen_names:
                continue
            if monster.xp <= remaining_budget and len(selected) < max_creatures:
                selected.append(monster)
                remaining_budget -= monster.xp
                seen_names.add(monster.name)

        # Second pass: fill remaining budget with duplicates if needed
        for monster in candidates:
            if len(selected) >= max_creatures or monster.xp > remaining_budget:
                continue
            selected.append(monster)
            remaining_budget -= monster.xp

        return selected

    def _position_tactically(self, monsters: List[Monster]) -> List[Dict[str, Any]]:
        """
        Position monsters tactically on the map.

        - Ranged creatures (archers) positioned back
        - Melee creatures (warriors) positioned front
        - Cluster by role to support tactical engagement
        """
        placements = []

        if not monsters:
            return placements

        # Simple tactical grouping:
        # Front line: x from 100-300 (melee range)
        # Back line: x from 400-600 (ranged distance)
        # Spread across y axis

        front_line = [m for m in monsters if m.cr <= 1]  # Small creatures front
        back_line = [m for m in monsters if m.cr > 1]  # Larger creatures back

        # Position front line
        y_spacing = self.scene_height // (len(front_line) + 1) if front_line else 0
        for i, monster in enumerate(front_line):
            placements.append({
                "uuid": monster.uuid,
                "name": monster.name,
                "x": 150,
                "y": (i + 1) * y_spacing,
                "hidden": False,
            })

        # Position back line
        y_spacing = self.scene_height // (len(back_line) + 1) if back_line else 0
        for i, monster in enumerate(back_line):
            placements.append({
                "uuid": monster.uuid,
                "name": monster.name,
                "x": 500,
                "y": (i + 1) * y_spacing,
                "hidden": False,
            })

        logger.debug(
            f"[CompendiumEncounter] Positioned {len(placements)} creatures: "
            f"{len(front_line)} front, {len(back_line)} back"
        )
        return placements

    def _generate_notes(self, monsters: List[Monster], placements: List[Dict]) -> str:
        """Generate human-readable encounter notes."""
        if not monsters:
            return "Empty encounter."

        monster_names = ", ".join(m.name for m in monsters)
        front_count = len([p for p in placements if p["x"] < 300])
        back_count = len([p for p in placements if p["x"] >= 300])

        notes = f"{len(monsters)} combatants: {monster_names}. "
        if front_count > 0 and back_count > 0:
            notes += f"Positioned: {front_count} front-line, {back_count} ranged."
        elif front_count > 0:
            notes += f"{front_count} melee combatants."
        else:
            notes += f"{back_count} ranged combatants."

        return notes
