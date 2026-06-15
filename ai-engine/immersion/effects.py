"""Effects and visual indicators for tokens and conditions."""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TokenEffect:
    """Visual effect applied to a token."""
    token_id: str
    effect_type: str  # "aura", "status", "animation", "overlay"
    name: str
    description: str
    color: Optional[str] = None
    duration: Optional[int] = None  # in turns
    icon: Optional[str] = None


class EffectsManager:
    """Manage visual effects and status indicators for immersion."""

    # Condition to visual indicator mapping
    CONDITION_VISUALS = {
        "blinded": {
            "icon": "fa-eye-slash",
            "color": "#000000",
            "description": "Cannot see (disadvantage on attacks, attacks vs have advantage)",
        },
        "charmed": {
            "icon": "fa-heart",
            "color": "#ff69b4",
            "description": "Charmed (disadvantage on attack rolls vs charmer)",
        },
        "deafened": {
            "icon": "fa-ear-slash",
            "color": "#808080",
            "description": "Cannot hear (auto-fail hearing checks)",
        },
        "frightened": {
            "icon": "fa-face-flushed",
            "color": "#8b0000",
            "description": "Frightened (disadvantage if source in sight)",
        },
        "grappled": {
            "icon": "fa-handshake",
            "color": "#ff8c00",
            "description": "Grappled (speed becomes 0)",
        },
        "incapacitated": {
            "icon": "fa-ban",
            "color": "#c0c0c0",
            "description": "Incapacitated (cannot move or act)",
        },
        "invisible": {
            "icon": "fa-eye",
            "color": "#87ceeb",
            "description": "Invisible (cannot be seen)",
        },
        "paralyzed": {
            "icon": "fa-person-hiking",
            "color": "#daa520",
            "description": "Paralyzed (incapacitated, can't move)",
        },
        "petrified": {
            "icon": "fa-stone",
            "color": "#808080",
            "description": "Petrified (incapacitated, immune to damage)",
        },
        "poisoned": {
            "icon": "fa-droplet",
            "color": "#228b22",
            "description": "Poisoned (disadvantage on attack rolls, ability checks)",
        },
        "prone": {
            "icon": "fa-person",
            "color": "#8b4513",
            "description": "Prone (melee attacks vs have advantage)",
        },
        "restrained": {
            "icon": "fa-chain",
            "color": "#696969",
            "description": "Restrained (speed becomes 0, disadvantage on DEX saves)",
        },
        "stunned": {
            "icon": "fa-star",
            "color": "#ffd700",
            "description": "Stunned (incapacitated, can't move or speak)",
        },
        "unconscious": {
            "icon": "fa-moon",
            "color": "#000080",
            "description": "Unconscious (incapacitated, unaware)",
        },
    }

    # Aura types for various effects
    AURA_TYPES = {
        "healing": {
            "color": "#00ff00",
            "description": "Healing aura (regeneration, restoration)",
            "radius": 10,
        },
        "protection": {
            "color": "#0000ff",
            "description": "Protective aura (shield spell, protection circle)",
            "radius": 15,
        },
        "danger": {
            "color": "#ff0000",
            "description": "Danger aura (hazard, hostile presence)",
            "radius": 20,
        },
        "magic": {
            "color": "#9370db",
            "description": "Magic aura (spell in effect, magical presence)",
            "radius": 15,
        },
        "cold": {
            "color": "#00ffff",
            "description": "Cold aura (frost, ice effects)",
            "radius": 10,
        },
        "fire": {
            "color": "#ff8c00",
            "description": "Fire aura (flame, heat effects)",
            "radius": 10,
        },
    }

    def __init__(self):
        self.active_effects: Dict[str, List[TokenEffect]] = {}

    def apply_condition_visual(
        self, token_id: str, condition: str, duration: Optional[int] = None
    ) -> Dict:
        """Apply visual indicator for a condition."""
        condition_lower = condition.lower()
        visual = self.CONDITION_VISUALS.get(
            condition_lower,
            {
                "icon": "fa-warning",
                "color": "#ff9800",
                "description": f"Status: {condition}",
            },
        )

        effect = TokenEffect(
            token_id=token_id,
            effect_type="status",
            name=condition,
            description=visual.get("description", ""),
            color=visual.get("color"),
            icon=visual.get("icon"),
            duration=duration,
        )

        if token_id not in self.active_effects:
            self.active_effects[token_id] = []

        self.active_effects[token_id].append(effect)

        logger.info(
            f"[Visual Effect] Applied {condition} to {token_id} "
            f"({visual.get('color')} {visual.get('icon')})"
        )

        return {
            "type": "condition_visual_applied",
            "token_id": token_id,
            "condition": condition,
            "visual": {
                "icon": visual.get("icon"),
                "color": visual.get("color"),
                "description": visual.get("description"),
            },
            "duration": duration,
        }

    def apply_aura(
        self, token_id: str, aura_type: str, duration: Optional[int] = None
    ) -> Dict:
        """Apply an aura around a token."""
        aura_config = self.AURA_TYPES.get(
            aura_type,
            {
                "color": "#ffffff",
                "description": f"Aura: {aura_type}",
                "radius": 15,
            },
        )

        effect = TokenEffect(
            token_id=token_id,
            effect_type="aura",
            name=aura_type,
            description=aura_config.get("description", ""),
            color=aura_config.get("color"),
            duration=duration,
        )

        if token_id not in self.active_effects:
            self.active_effects[token_id] = []

        self.active_effects[token_id].append(effect)

        logger.info(
            f"[Aura] Applied {aura_type} to {token_id} "
            f"({aura_config.get('color')} radius={aura_config.get('radius')}ft)"
        )

        return {
            "type": "aura_applied",
            "token_id": token_id,
            "aura": aura_type,
            "visual": {
                "color": aura_config.get("color"),
                "radius_feet": aura_config.get("radius"),
                "description": aura_config.get("description"),
            },
            "duration": duration,
        }

    def get_token_effects(self, token_id: str) -> List[Dict]:
        """Get all active effects for a token."""
        effects = self.active_effects.get(token_id, [])

        return [
            {
                "type": effect.effect_type,
                "name": effect.name,
                "description": effect.description,
                "color": effect.color,
                "icon": effect.icon,
                "duration": effect.duration,
            }
            for effect in effects
        ]

    def remove_effect(self, token_id: str, effect_name: str) -> Dict:
        """Remove a specific effect from a token."""
        if token_id not in self.active_effects:
            return {"error": f"No effects found for {token_id}"}

        self.active_effects[token_id] = [
            e for e in self.active_effects[token_id] if e.name != effect_name
        ]

        logger.info(f"[Effect Removed] {effect_name} from {token_id}")

        return {
            "type": "effect_removed",
            "token_id": token_id,
            "removed_effect": effect_name,
            "remaining_effects": len(self.active_effects[token_id]),
        }
