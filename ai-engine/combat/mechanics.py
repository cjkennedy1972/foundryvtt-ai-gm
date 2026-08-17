"""Advanced D&D 5e combat mechanics — flanking, opportunity attacks, cover, reach."""

import logging
import math
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CombatantPosition:
    """Position of a combatant on the battlefield."""
    actor_id: str
    x: float
    y: float
    size: float = 5.0  # Default medium creature (5x5 feet)
    is_prone: bool = False
    has_cover: bool = False
    cover_type: Optional[str] = None  # half, three_quarter, full


@dataclass
class TacticalAdvantage:
    """Tactical advantage or disadvantage for combat."""
    reason: str
    advantage: bool  # True for advantage, False for disadvantage
    dc_modifier: int = 0  # For saves


class CombatMechanics:
    """Calculate advanced D&D 5e combat mechanics."""

    FEET_PER_GRID_SQUARE = 5  # Standard grid assumption

    def __init__(self):
        self.positions: Dict[str, CombatantPosition] = {}

    def update_position(
        self, actor_id: str, x: float, y: float,
        size: float = 5.0, is_prone: bool = False
    ) -> None:
        """Update a combatant's position on the battlefield."""
        pos = self.positions.get(actor_id)
        if pos:
            pos.x = x
            pos.y = y
            pos.is_prone = is_prone
            pos.size = size
        else:
            self.positions[actor_id] = CombatantPosition(
                actor_id=actor_id, x=x, y=y, size=size, is_prone=is_prone
            )

    def set_cover(
        self, actor_id: str, has_cover: bool, cover_type: Optional[str] = None
    ) -> None:
        """Set cover status for a combatant."""
        pos = self.positions.get(actor_id)
        if pos:
            pos.has_cover = has_cover
            pos.cover_type = cover_type

    def get_distance(self, actor1_id: str, actor2_id: str) -> Optional[float]:
        """Calculate distance between two combatants in feet."""
        pos1 = self.positions.get(actor1_id)
        pos2 = self.positions.get(actor2_id)

        if not pos1 or not pos2:
            return None

        dx = pos2.x - pos1.x
        dy = pos2.y - pos1.y
        # Use Euclidean distance, convert to feet
        distance_squares = math.sqrt(dx * dx + dy * dy)
        return distance_squares * self.FEET_PER_GRID_SQUARE

    def is_within_reach(
        self, attacker_id: str, target_id: str, weapon_reach: float = 5.0
    ) -> bool:
        """Check if attacker can reach target with a melee weapon."""
        distance = self.get_distance(attacker_id, target_id)
        if distance is None:
            return False
        return distance <= weapon_reach

    def get_available_cover(self, actor_id: str, nearest_enemy_id: str) -> Optional[str]:
        """Determine available cover for a combatant relative to an enemy.

        Returns 'full', 'three_quarter', 'half', or None based on line of sight.
        Assumes 5 feet per grid square; checks distance to walls (simple heuristic).
        """
        actor_pos = self.positions.get(actor_id)
        enemy_pos = self.positions.get(nearest_enemy_id)

        if not actor_pos or not enemy_pos:
            return None

        # Simple heuristic: if actors are adjacent (< 10 ft), check for cover
        distance = self.get_distance(actor_id, nearest_enemy_id) or 0
        if distance >= 10:
            return None  # Too far apart to take tactical cover

        # Distance from actor to edge of cell (0-2.5 ft from center)
        # Assume partial cover exists if actor is to the side relative to attacker
        dx_to_enemy = enemy_pos.x - actor_pos.x
        dy_to_enemy = enemy_pos.y - actor_pos.y

        # Compute angle; if actor is perpendicular to enemy sight line, more cover
        magnitude = math.sqrt(dx_to_enemy ** 2 + dy_to_enemy ** 2)
        if magnitude < 0.1:
            return None

        angle = math.atan2(dy_to_enemy, dx_to_enemy)
        # Perpendicularity check (±45° = ±π/4): more perpendicular = better cover
        perpendicular = min(abs(angle % (math.pi / 2) - math.pi / 4),
                           abs((angle + math.pi / 2) % math.pi - math.pi / 4))

        if perpendicular < math.pi / 8:  # Within ~22.5° of perpendicular
            return "three_quarter"
        elif perpendicular < math.pi / 4:  # Within ~45° of perpendicular
            return "half"
        return None

    def is_flanking(self, attacker_id: str, target_id: str, allies: List[str]) -> bool:
        """Check if attacker is flanking the target (with an ally on opposite side).

        For flanking, the attacker and at least one ally must be on opposite sides
        of the target and within 5 feet.
        """
        attacker_pos = self.positions.get(attacker_id)
        target_pos = self.positions.get(target_id)

        if not attacker_pos or not target_pos:
            return False

        # Both must be within 5 feet of target
        attacker_dist = self.get_distance(attacker_id, target_id)
        if attacker_dist is None or attacker_dist > 5.0:
            return False

        # Check if any ally is on opposite side (rough check using angles)
        for ally_id in allies:
            if ally_id == attacker_id or ally_id == target_id:
                continue

            ally_pos = self.positions.get(ally_id)
            if not ally_pos:
                continue

            ally_dist = self.get_distance(ally_id, target_id)
            if ally_dist is None or ally_dist > 5.0:
                continue

            # Calculate angles from target
            attacker_angle = self._get_angle(target_pos, attacker_pos)
            ally_angle = self._get_angle(target_pos, ally_pos)

            # Check if they're roughly opposite (within 90 degrees on either side of opposite)
            angle_diff = abs(attacker_angle - ally_angle)
            if angle_diff > 270:
                angle_diff = 360 - angle_diff

            # Opposite enough for flanking (within ~90 degree cone)
            if 90 <= angle_diff <= 270:
                return True

        return False

    def can_opportunity_attack(
        self, defender_id: str, hostile_ids: List[str]
    ) -> List[str]:
        """Determine which hostile combatants can make opportunity attacks.

        An opportunity attack can be made when a creature moves out of a hostile
        combatant's reach while not using the Disengage action.
        """
        defender_pos = self.positions.get(defender_id)
        if not defender_pos:
            return []

        attackers = []
        for hostile_id in hostile_ids:
            hostile_pos = self.positions.get(hostile_id)
            if not hostile_pos:
                continue

            # Check if hostile is within reach
            distance = self.get_distance(defender_id, hostile_id)
            if distance and distance <= 5.0:
                attackers.append(hostile_id)

        return attackers

    def get_cover_ac_bonus(self, actor_id: str) -> int:
        """Get AC bonus from cover."""
        pos = self.positions.get(actor_id)
        if not pos or not pos.has_cover:
            return 0

        cover_bonuses = {
            "half": 2,
            "three_quarter": 5,
            "full": float('inf'),  # Can't be targeted
        }

        return cover_bonuses.get(pos.cover_type, 0)

    def get_reach(self, actor_id: str, weapon: str = "melee") -> float:
        """Get reach of a combatant based on weapon and size."""
        pos = self.positions.get(actor_id)
        if not pos:
            return 5.0

        # Large creatures get 10 feet reach
        if pos.size > 5.0:
            return 10.0

        # Reach weapons (like polearms) extend reach
        if weapon in ["polearm", "pike", "lance"]:
            return 10.0

        return 5.0

    def _get_angle(self, from_pos: CombatantPosition, to_pos: CombatantPosition) -> float:
        """Calculate angle from one position to another (0-360 degrees)."""
        dx = to_pos.x - from_pos.x
        dy = to_pos.y - from_pos.y
        angle = math.atan2(dy, dx)
        angle_degrees = math.degrees(angle)
        return angle_degrees % 360

    def get_tactical_analysis(
        self, actor_id: str, hostile_ids: List[str], allied_ids: List[str]
    ) -> 'TacticalAnalysis':
        """Perform tactical analysis for a combatant."""
        return TacticalAnalysis.analyze(
            self, actor_id, hostile_ids, allied_ids
        )


class TacticalAnalysis:
    """Analysis of tactical situation for a combatant."""

    def __init__(
        self,
        actor_id: str,
        flanking_allies: List[str],
        flanking_enemies: List[str],
        enemies_in_range: List[str],
        available_cover: Optional[str],
        opportunity_attack_threats: List[str],
    ):
        self.actor_id = actor_id
        self.flanking_allies = flanking_allies
        self.flanking_enemies = flanking_enemies
        self.enemies_in_range = enemies_in_range
        self.available_cover = available_cover
        self.opportunity_attack_threats = opportunity_attack_threats

    def get_recommendations(self) -> List[str]:
        """Get tactical recommendations based on current situation."""
        recommendations = []

        if self.flanking_enemies:
            recommendations.append(
                f"You are flanking {len(self.flanking_enemies)} enemy(ies) - gain advantage on attack rolls"
            )

        if self.flanking_allies:
            recommendations.append(
                f"{len(self.flanking_allies)} ally/ies are flanking enemies with you"
            )

        if self.opportunity_attack_threats:
            recommendations.append(
                f"Moving away from {len(self.opportunity_attack_threats)} enemy(ies) will provoke opportunity attacks"
            )

        if self.available_cover == "three_quarter":
            recommendations.append("Take three-quarter cover (+5 AC)")
        elif self.available_cover == "half":
            recommendations.append("Take half cover (+2 AC)")

        if not self.enemies_in_range:
            recommendations.append("No enemies within melee range - use ranged attacks or move to engage")

        return recommendations

    @staticmethod
    def analyze(
        mechanics: CombatMechanics,
        actor_id: str,
        hostile_ids: List[str],
        allied_ids: List[str],
    ) -> 'TacticalAnalysis':
        """Analyze tactical situation for an actor."""
        flanking_allies = []
        flanking_enemies = []

        # Check flanking with allies
        for ally_id in allied_ids:
            if mechanics.is_flanking(actor_id, ally_id, []):
                flanking_allies.append(ally_id)

        # Check flanking with enemies
        for enemy_id in hostile_ids:
            if mechanics.is_flanking(actor_id, enemy_id, allied_ids):
                flanking_enemies.append(enemy_id)

        # Check enemies in range
        enemies_in_range = []
        for enemy_id in hostile_ids:
            if mechanics.is_within_reach(actor_id, enemy_id):
                enemies_in_range.append(enemy_id)

        # Opportunity attack threats
        threats = mechanics.can_opportunity_attack(actor_id, hostile_ids)

        # Determine available cover (relative to nearest hostile)
        available_cover = None
        if hostile_ids:
            nearest_enemy = min(
                hostile_ids,
                key=lambda e: mechanics.get_distance(actor_id, e) or float('inf')
            )
            available_cover = mechanics.get_available_cover(actor_id, nearest_enemy)

        return TacticalAnalysis(
            actor_id=actor_id,
            flanking_allies=flanking_allies,
            flanking_enemies=flanking_enemies,
            enemies_in_range=enemies_in_range,
            available_cover=available_cover,
            opportunity_attack_threats=threats,
        )
