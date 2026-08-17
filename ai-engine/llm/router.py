"""ModelRouter — selects an LLMManager by cost tier.

Only one model is configured today (settings.model) — there's no second,
cheaper model available for NPC turns yet, so every tier resolves to the
same LLMManager instance. This class is the seam Phase 5's NPCAgent calls
through: wiring in a distinct cheaper model later is a constructor change
here (pass a differently-configured LLMManager for "npc"), not a rewrite of
every call site. LLMManager itself would also need a per-instance model
override added — it currently reads settings.model globally — but that's
out of scope until a second model actually exists to route to.
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
