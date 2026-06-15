"""Vision and fog of war management for dynamic visibility."""

import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VisionManager:
    """Manage vision, fog of war, and line-of-sight mechanics."""

    def __init__(self):
        self.fog_of_war_enabled = True
        self.vision_ranges: Dict[str, float] = {}  # token_id -> vision range in feet
        self.light_sources: Dict[str, Dict] = {}  # token_id -> light config
        self.darkness_enabled = False

    def set_vision_range(self, token_id: str, range_feet: float) -> Dict:
        """Set vision range for a token."""
        self.vision_ranges[token_id] = range_feet
        logger.info(f"[Vision] {token_id} vision range set to {range_feet}ft")

        return {
            "type": "vision_range_set",
            "token_id": token_id,
            "vision_range_feet": range_feet,
        }

    def apply_light_source(
        self,
        token_id: str,
        light_radius: float,
        color: str = "#ffffff",
        intensity: float = 1.0,
    ) -> Dict:
        """Apply a light source to a token (torch, spell, etc)."""
        self.light_sources[token_id] = {
            "radius": light_radius,
            "color": color,
            "intensity": intensity,
        }

        logger.info(
            f"[Light] {token_id} emitting light "
            f"(radius={light_radius}ft, color={color}, intensity={intensity})"
        )

        return {
            "type": "light_source_added",
            "token_id": token_id,
            "light": {
                "radius_feet": light_radius,
                "color": color,
                "intensity": intensity,
            },
        }

    def remove_light_source(self, token_id: str) -> Dict:
        """Remove a light source from a token."""
        if token_id in self.light_sources:
            del self.light_sources[token_id]
            logger.info(f"[Light] Removed light source from {token_id}")

        return {
            "type": "light_source_removed",
            "token_id": token_id,
        }

    def calculate_visibility(
        self, observer_token: str, target_token: str, distance_feet: float
    ) -> Dict:
        """Calculate if observer can see target based on distance, lighting, and vision."""
        observer_vision = self.vision_ranges.get(observer_token, 60)  # Default human vision
        observer_light = self.light_sources.get(observer_token, {})
        target_light = self.light_sources.get(target_token, {})

        visible = False
        reason = "Unknown"

        # Check light sources first
        if observer_light or target_light:
            max_radius = max(
                observer_light.get("radius", 0), target_light.get("radius", 0)
            )
            if distance_feet <= max_radius:
                visible = True
                reason = "Within light source radius"
            else:
                visible = False
                reason = "Outside light source range"
        elif self.darkness_enabled:
            visible = False
            reason = "Darkness - no light source"
        else:
            # Normal visibility based on vision range
            if distance_feet <= observer_vision:
                visible = True
                reason = f"Within vision range ({observer_vision}ft)"
            else:
                visible = False
                reason = f"Beyond vision range ({observer_vision}ft)"

        logger.info(
            f"[Visibility] {observer_token} -> {target_token}: {visible} ({reason})"
        )

        return {
            "type": "visibility_check",
            "observer": observer_token,
            "target": target_token,
            "distance_feet": distance_feet,
            "visible": visible,
            "reason": reason,
            "observer_vision_range": observer_vision,
        }

    def update_fog_of_war(
        self,
        player_token_ids: List[str],
        all_token_positions: Dict[str, Tuple[float, float]],
    ) -> Dict:
        """Update fog of war based on player positions and vision."""
        explored_positions = set()
        vision_coverage = {}

        for player_token in player_token_ids:
            if player_token not in all_token_positions:
                continue

            player_x, player_y = all_token_positions[player_token]
            vision_range = self.vision_ranges.get(player_token, 60)

            # Add light source visibility
            light_source = self.light_sources.get(player_token)
            if light_source:
                vision_range = max(vision_range, light_source.get("radius", 0))

            # Calculate visible positions within vision range
            for token_id, (token_x, token_y) in all_token_positions.items():
                distance = math.sqrt((token_x - player_x) ** 2 + (token_y - player_y) ** 2)

                if distance <= vision_range:
                    explored_positions.add(token_id)
                    if token_id not in vision_coverage:
                        vision_coverage[token_id] = []
                    vision_coverage[token_id].append(
                        {
                            "visible_to": player_token,
                            "distance": distance,
                        }
                    )

        logger.info(
            f"[Fog of War] Updated: {len(explored_positions)} positions visible "
            f"to {len(player_token_ids)} player tokens"
        )

        return {
            "type": "fog_of_war_updated",
            "visible_tokens": list(explored_positions),
            "hidden_tokens": [
                t
                for t in all_token_positions.keys()
                if t not in explored_positions
                and t not in player_token_ids
            ],
            "vision_coverage": vision_coverage,
            "darkness_enabled": self.darkness_enabled,
        }

    def set_darkness(self, enabled: bool) -> Dict:
        """Enable or disable darkness (requires light sources to see)."""
        self.darkness_enabled = enabled
        logger.info(f"[Darkness] {'Enabled' if enabled else 'Disabled'}")

        return {
            "type": "darkness_changed",
            "darkness_enabled": enabled,
            "light_sources_active": len(self.light_sources),
        }

    def get_vision_status(self) -> Dict:
        """Get current vision and fog of war status."""
        return {
            "fog_of_war_enabled": self.fog_of_war_enabled,
            "darkness_enabled": self.darkness_enabled,
            "tokens_with_vision": len(self.vision_ranges),
            "active_light_sources": len(self.light_sources),
            "average_vision_range": (
                sum(self.vision_ranges.values()) / len(self.vision_ranges)
                if self.vision_ranges
                else 0
            ),
        }
