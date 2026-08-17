"""NPC goals — the state Phase 4 (world clock) and Phase 5 (NPC agents)
act on. No behavior lives here yet, just the data shape."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

STATUSES = ("pending", "active", "done", "abandoned")


@dataclass
class Goal:
    """A single NPC objective.

    `trigger_conditions` is a free-form dict interpreted by whichever agent
    checks it (e.g. {"event_type": "time_advanced"} for the world clock,
    {"event_type": "action_resolved", "target_id": "pc-1"} for an NPC
    agent reacting to a specific player). No fixed schema yet — there's
    only one consumer so far (Phase 4), so it stays a dict rather than a
    speculative sub-hierarchy of trigger classes.
    """

    description: str
    priority: int = 0
    status: str = "pending"
    trigger_conditions: Dict[str, Any] = field(default_factory=dict)

    def matches(self, event: dict) -> bool:
        """True if this goal's trigger_conditions are satisfied by *event*
        (a dict as returned by EventStore.get_events: id/type/payload/...).
        Every configured condition key must match; an empty
        trigger_conditions dict never matches (a goal with no trigger is
        not self-initiating)."""
        if not self.trigger_conditions:
            return False
        for key, expected in self.trigger_conditions.items():
            if key == "event_type":
                if event.get("type") != expected:
                    return False
            elif event.get("payload", {}).get(key) != expected:
                return False
        return True
