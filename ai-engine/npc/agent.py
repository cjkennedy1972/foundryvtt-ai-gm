"""NPCAgent — a single NPC's autonomous turn, triggered by one of its own
active goals matching an event. Reuses the Referee gate (Phase 1) and
ModelRouter (Phase 5) rather than a separate action pipeline: an NPC's
proposed action is adjudicated exactly like a player's before anything
executes.
"""

import logging
from typing import List

from llm.router import ModelRouter
from npc.memory import NPCMemory
from npc.registry import NPCRecord
from referee.agent import RefereeAgent
from referee.models import Ruling

logger = logging.getLogger(__name__)

_MEMORY_RECALL_LIMIT = 10
_MEMORY_CONTEXT_LIMIT = 5


class NPCAgent:
    def __init__(self, npc: NPCRecord, model_router: ModelRouter, referee: RefereeAgent, memory: NPCMemory):
        self.npc = npc
        self.model_router = model_router
        self.referee = referee
        self.memory = memory

    async def act(self, session_id: str, triggering_event: dict) -> List[Ruling]:
        """Ask the NPC-tier model for this NPC's response to
        *triggering_event* and adjudicate the result. Returns approved (or
        rules-adjusted) rulings ready for a caller to dispatch. Never
        raises — an LLM or adjudication failure yields an empty list
        instead of blocking the rest of the turn."""
        active_goals = [g for g in self.npc.goals if g.status == "active"]
        if not active_goals:
            return []

        try:
            memory_events = await self.memory.recall(session_id, self.npc.npc_id, limit=_MEMORY_RECALL_LIMIT)
            context = self._build_context(active_goals, memory_events, triggering_event)
            llm = self.model_router.get("npc")
            result = await llm.generate(
                user_message=f"[{self.npc.npc_name} acts on their own initiative]",
                extra_context=context,
            )
            actions = result.get("actions", [])
        except Exception:
            logger.error(f"NPCAgent.act failed for {self.npc.npc_name}", exc_info=True)
            return []

        rulings = await self.referee.adjudicate_batch(actions)
        return [r for r in rulings if r.approved]

    def _build_context(self, goals, memory_events, triggering_event) -> str:
        lines = [f"You are narrating {self.npc.npc_name}, acting on their own initiative — not in response to a player."]
        if self.npc.disposition == 1.0:
            lines.append("You are a loyal companion to the player; your actions should support the party and reflect your friendship.")
        lines.append(f"Triggering event: {triggering_event.get('type')}")
        lines.append("Active goals: " + "; ".join(g.description for g in goals))
        if memory_events:
            recalled = "; ".join(e.get("description") or e.get("type") for e in memory_events[-_MEMORY_CONTEXT_LIMIT:])
            lines.append(f"What {self.npc.npc_name} remembers: {recalled}")
        return "\n".join(lines)
