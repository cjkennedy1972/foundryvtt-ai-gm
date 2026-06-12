"""
Action Dispatcher — routes LLM action requests to the appropriate executor.
"""

import logging
from typing import Dict, Any, List
from functools import partial

from actions.executors import ACTION_HANDLERS
from foundry.client import FoundryClient

logger = logging.getLogger(__name__)


class ActionDispatcher:
    def __init__(self, foundry_client: FoundryClient):
        self.foundry = foundry_client

    async def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single action."""
        action_type = action.get("type")
        if not action_type:
            return {"error": "No action type specified", "raw": action}

        handler = ACTION_HANDLERS.get(action_type)
        if not handler:
            logger.warning(f"Unknown action type: {action_type}")
            return {"error": f"Unknown action type: {action_type}"}

        try:
            kwargs = dict(action)
            del kwargs["type"]
            kwargs["foundry"] = self.foundry
            result = await handler(**kwargs)
            result["success"] = True
            return result
        except Exception as e:
            logger.error(f"Action execution failed ({action_type}): {e}")
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
