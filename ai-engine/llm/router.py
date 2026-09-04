"""ModelRouter — selects an LLMManager by cost tier.

The frontier manager is always available. NPC turns use the optional
dedicated manager when one is configured; otherwise they deliberately share
the frontier manager for backwards-compatible behaviour. Each manager owns
its model selection, so routing does not mutate global settings or leak a
cheaper model into narrator turns.
"""

from typing import Dict

from llm.manager import LLMManager

DEFAULT_TIER = "frontier"


class ModelRouter:
    def __init__(self, frontier: LLMManager, npc: LLMManager = None):
        self._tiers: Dict[str, LLMManager] = {
            "frontier": frontier,
            "npc": npc or frontier,
        }

    def get(self, tier: str) -> LLMManager:
        return self._tiers.get(tier, self._tiers[DEFAULT_TIER])
