"""WorldClockAgent — advances world time and flags NPC goals whose triggers
match, so Phase 5's NPCAgent has something to act on next.

This agent does NOT generate NPC actions itself — that requires an LLM call
(Phase 5's NPCAgent), which this phase doesn't have access to. Its job ends
at "here's what changed and which goals are now ready"; a caller (or a
future NPCAgent sweep) is responsible for turning "active" goals into actual
proposed actions.
"""

import logging
from typing import List

from events.store import EventStore
from events.types import TIME_ADVANCED
from npc.registry import NPCRegistry

logger = logging.getLogger(__name__)


class WorldClockAgent:
    def __init__(self, event_store: EventStore, npc_registry: NPCRegistry):
        self.event_store = event_store
        self.npc_registry = npc_registry

    async def advance(self, session_id: str, duration_seconds: int) -> List[str]:
        """Append a TIME_ADVANCED event, then activate any pending goal
        across all NPCs whose trigger_conditions match it. Returns the list
        of "npc_id:goal_description" activated, for logging/inspection."""
        await self.event_store.append(
            session_id, TIME_ADVANCED, {"duration_seconds": duration_seconds}
        )
        event = {"type": TIME_ADVANCED, "payload": {"duration_seconds": duration_seconds}}

        activated = []
        for npc in self.npc_registry.list_npcs():
            for goal in npc.goals:
                if goal.status == "pending" and goal.matches(event):
                    goal.status = "active"
                    activated.append(f"{npc.npc_id}:{goal.description}")
                    logger.info(f"World clock activated goal for {npc.npc_name}: {goal.description}")
        return activated
