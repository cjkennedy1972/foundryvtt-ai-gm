"""
Action Dispatcher — routes LLM action requests to the appropriate executor.

All action payloads are validated against Pydantic schemas before handlers
are called, so extra/misnamed LLM keys cannot leak into Foundry and numeric
fields are bounded to game-safe ranges.
"""

import logging
from typing import Dict, Any, List

from actions.executors import ACTION_HANDLERS
from actions.schemas import ACTION_SCHEMAS, MIN_DAMAGE, MAX_DAMAGE
from foundry.client import FoundryClient

logger = logging.getLogger(__name__)


class ActionDispatcher:
    def __init__(self, foundry_client: FoundryClient):
        self.foundry = foundry_client

    async def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single action with validation."""
        action_type = action.get("type")
        if not action_type:
            return {"error": "No action type specified", "raw": action}

        handler = ACTION_HANDLERS.get(action_type)
        if not handler:
            logger.warning(f"Unknown action type: {action_type}")
            return {"error": f"Unknown action type: {action_type}"}

        # Validate action against schema (rejects extra/unknown fields)
        schema_cls = ACTION_SCHEMAS.get(action_type)
        if not schema_cls:
            logger.warning(f"No schema for action type: {action_type}")
            return {"error": f"No schema for action type: {action_type}", "success": False}

        try:
            # Validate using Pydantic schema
            validated = schema_cls(**action)
            kwargs = validated.model_dump(exclude_unset=True)

            # Remove 'type' if present (already know the action type)
            kwargs.pop("type", None)

            # Clamp damage values to safe range
            if action_type == "update_hp" and "damage" in kwargs:
                original_damage = kwargs["damage"]
                if original_damage < MIN_DAMAGE:
                    kwargs["damage"] = MIN_DAMAGE
                    logger.info(f"Damage clamped from {original_damage} to {MIN_DAMAGE}")
                elif original_damage > MAX_DAMAGE:
                    kwargs["damage"] = MAX_DAMAGE
                    logger.info(f"Damage clamped from {original_damage} to {MAX_DAMAGE}")

            # Pass foundry client to handler
            kwargs["foundry"] = self.foundry

            # Execute handler
            result = await handler(**kwargs)

            # Ensure result is a dict before setting success
            if not isinstance(result, dict):
                result = {"raw_result": result}

            result["success"] = True
            return result

        except Exception as e:
            logger.error(f"Action validation/execution failed ({action_type}): {e}")
            return {
                "type": action_type,
                "error": str(e),
                "success": False
            }

    async def execute_batch(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute multiple actions in sequence."""
        results = []
        for action in actions:
            result = await self.execute(action)
            results.append(result)
            logger.info(f"[{result.get('type', '?')}] {result}")
        return results

    @property
    def available_actions(self) -> List[str]:
        return list(ACTION_HANDLERS.keys())
