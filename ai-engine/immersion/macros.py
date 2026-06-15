"""Macro execution and automation for GM actions."""

import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class MacroManager:
    """Manage and execute GM macros for automation."""

    def __init__(self):
        self.registered_macros: Dict[str, Dict[str, Any]] = {}
        self.macro_history: List[Dict[str, Any]] = []
        self.max_history = 50

    def register_macro(
        self,
        macro_id: str,
        name: str,
        description: str,
        action_type: str,
        parameters: Dict[str, Any],
    ) -> Dict:
        """Register a new GM macro."""
        macro = {
            "id": macro_id,
            "name": name,
            "description": description,
            "action_type": action_type,
            "parameters": parameters,
        }

        self.registered_macros[macro_id] = macro
        logger.info(f"[Macro] Registered {name} ({action_type})")

        return {
            "type": "macro_registered",
            "macro_id": macro_id,
            "name": name,
            "action_type": action_type,
        }

    def execute_macro(self, macro_id: str, overrides: Optional[Dict] = None) -> Dict:
        """Execute a registered macro with optional parameter overrides."""
        if macro_id not in self.registered_macros:
            logger.warning(f"[Macro] Attempted to execute unknown macro: {macro_id}")
            return {"type": "macro_execution", "error": f"Macro not found: {macro_id}"}

        macro = self.registered_macros[macro_id]
        parameters = {**macro["parameters"], **(overrides or {})}

        execution = {
            "macro_id": macro_id,
            "name": macro["name"],
            "action_type": macro["action_type"],
            "parameters": parameters,
            "executed_at": "timestamp",  # Filled by caller
        }

        # Add to history (bounded)
        self.macro_history.append(execution)
        if len(self.macro_history) > self.max_history:
            self.macro_history.pop(0)

        logger.info(f"[Macro] Executed {macro['name']} ({macro['action_type']})")

        return {
            "type": "macro_execution",
            "macro_id": macro_id,
            "name": macro["name"],
            "action_type": macro["action_type"],
            "parameters": parameters,
        }

    def list_macros(self) -> List[Dict]:
        """List all registered macros."""
        return [
            {
                "id": m["id"],
                "name": m["name"],
                "description": m["description"],
                "action_type": m["action_type"],
            }
            for m in self.registered_macros.values()
        ]

    def get_macro_history(self, limit: int = 10) -> List[Dict]:
        """Get recent macro execution history."""
        return self.macro_history[-limit:] if self.macro_history else []

    def delete_macro(self, macro_id: str) -> Dict:
        """Delete a registered macro."""
        if macro_id not in self.registered_macros:
            return {"error": f"Macro not found: {macro_id}"}

        name = self.registered_macros[macro_id]["name"]
        del self.registered_macros[macro_id]

        logger.info(f"[Macro] Deleted {name} ({macro_id})")

        return {
            "type": "macro_deleted",
            "macro_id": macro_id,
            "name": name,
        }

    def get_macro_templates(self) -> Dict[str, Dict]:
        """Get common macro templates for quick creation."""
        return {
            "spawn_reinforcements": {
                "name": "Spawn Reinforcements",
                "description": "Spawn enemy reinforcements at specified locations",
                "action_type": "start_encounter",
                "parameters": {
                    "token_ids": [],
                    "auto_roll_initiative": True,
                },
            },
            "party_rest": {
                "name": "Party Takes a Rest",
                "description": "Restore party HP and resources after a short/long rest",
                "action_type": "narrate",
                "parameters": {
                    "text": "The party takes a moment to rest and recover...",
                },
            },
            "dramatic_weather": {
                "name": "Dramatic Weather Change",
                "description": "Set weather to create dramatic atmosphere",
                "action_type": "set_weather",
                "parameters": {
                    "weather": "thunderstorm",
                },
            },
            "ambient_light": {
                "name": "Set Ambient Lighting",
                "description": "Change scene lighting and time of day",
                "action_type": "set_time",
                "parameters": {
                    "time": "night",
                },
            },
            "mass_condition": {
                "name": "Apply Condition to Multiple Tokens",
                "description": "Apply a condition to multiple tokens at once",
                "action_type": "apply_token_effect",
                "parameters": {
                    "token_id": "",
                    "effect_type": "condition",
                    "effect_name": "frightened",
                },
            },
        }
