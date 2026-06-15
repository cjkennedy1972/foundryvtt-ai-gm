"""Particle effects and visual animations for immersive gameplay."""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ParticleEffect:
    """A particle effect in the game."""
    effect_id: str
    name: str
    type: str  # "spell", "weather", "environmental", "impact", "animation"
    position: tuple  # (x, y)
    color: str  # hex color
    duration: Optional[int]  # milliseconds or None for permanent
    intensity: float  # 0.0 - 1.0
    size: str  # "small", "medium", "large"


class ParticleManager:
    """Manage particle effects and animations."""

    PARTICLE_PRESETS = {
        "fireball": {
            "color": "#ff4500",
            "size": "large",
            "duration": 1000,
            "intensity": 0.8,
        },
        "ice_shards": {
            "color": "#00ffff",
            "size": "medium",
            "duration": 1500,
            "intensity": 0.7,
        },
        "healing_light": {
            "color": "#00ff00",
            "size": "medium",
            "duration": 2000,
            "intensity": 0.6,
        },
        "lightning": {
            "color": "#ffff00",
            "size": "small",
            "duration": 500,
            "intensity": 1.0,
        },
        "poison_cloud": {
            "color": "#228b22",
            "size": "large",
            "duration": 3000,
            "intensity": 0.5,
        },
        "blood_spray": {
            "color": "#8b0000",
            "size": "small",
            "duration": 800,
            "intensity": 0.7,
        },
        "smoke": {
            "color": "#808080",
            "size": "large",
            "duration": 2000,
            "intensity": 0.4,
        },
        "sparkles": {
            "color": "#ffd700",
            "size": "small",
            "duration": 1500,
            "intensity": 0.6,
        },
    }

    def __init__(self):
        self.active_effects: Dict[str, ParticleEffect] = {}
        self.effect_history: List[Dict] = []
        self.max_history = 100

    def create_effect(
        self,
        effect_id: str,
        name: str,
        effect_type: str,
        x: float,
        y: float,
        color: str = "#ffffff",
        duration: Optional[int] = None,
        intensity: float = 0.7,
        size: str = "medium",
    ) -> Dict:
        """Create a particle effect at a location."""
        effect = ParticleEffect(
            effect_id=effect_id,
            name=name,
            type=effect_type,
            position=(x, y),
            color=color,
            duration=duration,
            intensity=intensity,
            size=size,
        )

        self.active_effects[effect_id] = effect

        logger.info(
            f"[Particle] Created {name} ({effect_type}) at ({x}, {y}) "
            f"color={color} intensity={intensity}"
        )

        self.effect_history.append(
            {
                "effect_id": effect_id,
                "name": name,
                "type": effect_type,
            }
        )
        if len(self.effect_history) > self.max_history:
            self.effect_history.pop(0)

        return {
            "type": "particle_effect_created",
            "effect_id": effect_id,
            "name": name,
            "position": effect.position,
            "visual": {
                "color": color,
                "size": size,
                "intensity": intensity,
                "duration_ms": duration,
            },
        }

    def create_effect_from_preset(
        self,
        effect_id: str,
        preset_name: str,
        x: float,
        y: float,
    ) -> Dict:
        """Create a particle effect from a preset."""
        if preset_name not in self.PARTICLE_PRESETS:
            return {"error": f"Unknown particle preset: {preset_name}"}

        preset = self.PARTICLE_PRESETS[preset_name]

        return self.create_effect(
            effect_id=effect_id,
            name=preset_name,
            effect_type="spell",
            x=x,
            y=y,
            color=preset["color"],
            duration=preset["duration"],
            intensity=preset["intensity"],
            size=preset["size"],
        )

    def remove_effect(self, effect_id: str) -> Dict:
        """Remove a particle effect."""
        if effect_id not in self.active_effects:
            return {"error": f"Effect not found: {effect_id}"}

        effect = self.active_effects[effect_id]
        del self.active_effects[effect_id]

        logger.info(f"[Particle] Removed {effect.name} ({effect_id})")

        return {
            "type": "particle_effect_removed",
            "effect_id": effect_id,
            "name": effect.name,
        }

    def remove_effects_at_location(self, x: float, y: float, radius: float = 10) -> Dict:
        """Remove all effects within a radius of a location."""
        removed = []
        to_remove = [
            effect_id
            for effect_id, effect in self.active_effects.items()
            if abs(effect.position[0] - x) <= radius and abs(effect.position[1] - y) <= radius
        ]

        for effect_id in to_remove:
            self.remove_effect(effect_id)
            removed.append(effect_id)

        logger.info(f"[Particle] Removed {len(removed)} effects near ({x}, {y})")

        return {
            "type": "particle_effects_cleared",
            "location": (x, y),
            "radius": radius,
            "effects_removed": len(removed),
        }

    def get_active_effects(self) -> Dict:
        """Get all active particle effects."""
        return {
            effect_id: {
                "name": effect.name,
                "type": effect.type,
                "position": effect.position,
                "color": effect.color,
                "intensity": effect.intensity,
                "size": effect.size,
                "duration_ms": effect.duration,
            }
            for effect_id, effect in self.active_effects.items()
        }

    def list_presets(self) -> Dict[str, Dict]:
        """List available particle effect presets."""
        return self.PARTICLE_PRESETS

    def get_effect_count(self) -> int:
        """Get count of active effects."""
        return len(self.active_effects)
