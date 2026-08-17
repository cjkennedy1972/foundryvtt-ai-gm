"""WorldClockAgent — advances world time, triggers NPC goals, and tracks settlement schedules.

Responsibilities:
1. Append TIME_ADVANCED events as time progresses
2. Activate pending NPC goals based on trigger conditions
3. Update NPC locations per settlement schedules
4. Log NPC_MOVED events for location changes (with actor_uuid if mapped)

Does NOT generate NPC actions — that requires an LLM call (NPCAgent in Phase 5).
"""

import logging
from typing import Dict, List, Optional

from events.store import EventStore
from events.types import TIME_ADVANCED, NPC_MOVED
from npc.registry import NPCRegistry
from world.settlement import Settlement

logger = logging.getLogger(__name__)

# Time-of-day cycle (assumed globally for all settlements)
_DEFAULT_TIME_CYCLE = ["dawn", "morning", "noon", "afternoon", "dusk", "night"]


class WorldClockAgent:
    """Manages game time, NPC goal activation, and settlement-based location tracking."""

    def __init__(
        self,
        event_store: EventStore,
        npc_registry: NPCRegistry,
        settlements: Optional[Dict[str, Settlement]] = None,
    ):
        self.event_store = event_store
        self.npc_registry = npc_registry
        self.settlements = settlements or {}
        self.current_time_of_day = "dawn"
        self._time_cycle = _DEFAULT_TIME_CYCLE

    def register_settlement(self, settlement: Settlement) -> None:
        """Register a settlement for location tracking."""
        self.settlements[settlement.id] = settlement
        logger.info(f"Registered settlement '{settlement.name}' for location tracking")

    async def advance(self, session_id: str, duration_seconds: int) -> List[str]:
        """Advance time and trigger goal activation and location updates.

        Args:
            session_id: Current session ID
            duration_seconds: How much time passes

        Returns:
            List of activated goals ("npc_id:goal_description")
        """
        # Log time advancement
        await self.event_store.append(
            session_id, TIME_ADVANCED, {"duration_seconds": duration_seconds}
        )
        event = {"type": TIME_ADVANCED, "payload": {"duration_seconds": duration_seconds}}

        # Update time-of-day (approximate: 3600s per cycle, 6 times per day)
        self._update_time_of_day(duration_seconds)

        # Activate pending goals
        activated = []
        for npc in self.npc_registry.list_npcs():
            for goal in npc.goals:
                if goal.status == "pending" and goal.matches(event):
                    goal.status = "active"
                    activated.append(f"{npc.npc_id}:{goal.description}")
                    logger.info(f"World clock activated goal for {npc.npc_name}: {goal.description}")

        # Update NPC locations per settlement schedules
        await self._update_settlement_locations(session_id)

        return activated

    def _update_time_of_day(self, duration_seconds: int) -> None:
        """Advance time-of-day cycle based on elapsed time.

        Assumes 1 day = 6 cycles × 3600 seconds = 21600 seconds.
        """
        seconds_per_cycle = 3600
        cycles_advanced = duration_seconds // seconds_per_cycle

        if cycles_advanced > 0:
            current_idx = self._time_cycle.index(self.current_time_of_day)
            new_idx = (current_idx + cycles_advanced) % len(self._time_cycle)
            old_time = self.current_time_of_day
            self.current_time_of_day = self._time_cycle[new_idx]
            if old_time != self.current_time_of_day:
                logger.debug(f"Time advanced: {old_time} → {self.current_time_of_day}")

    async def _update_settlement_locations(self, session_id: str) -> None:
        """Move NPCs in settlements to their scheduled locations, log NPC_MOVED events."""
        for settlement in self.settlements.values():
            locations = settlement.query_location_at_time(self.current_time_of_day)
            for npc_id, npcs_at_location in locations.items():
                for npc in npcs_at_location:
                    # Log NPC location update (with actor_uuid if mapped)
                    actor_uuid = self.npc_registry.get_actor_uuid_for_npc(npc)
                    payload = {
                        "npc_id": npc,
                        "location": npc_id,
                        "settlement": settlement.id,
                    }
                    if actor_uuid:
                        payload["actor_uuid"] = actor_uuid

                    await self.event_store.append(session_id, NPC_MOVED, payload)

    async def query_location_at_time(
        self,
        settlement_id: str,
        time_of_day: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        """Query NPC locations in a settlement at a specific time.

        Args:
            settlement_id: Which settlement to query
            time_of_day: Time to query (defaults to current time)

        Returns:
            Dict of {location_id: [npc_ids]}
        """
        if settlement_id not in self.settlements:
            logger.warning(f"Settlement not found: {settlement_id}")
            return {}

        settlement = self.settlements[settlement_id]
        time = time_of_day or self.current_time_of_day
        return settlement.query_location_at_time(time)

    def get_current_time(self) -> str:
        """Get the current in-game time-of-day."""
        return self.current_time_of_day

    def list_settlements(self) -> List[Settlement]:
        """Get all registered settlements."""
        return list(self.settlements.values())
