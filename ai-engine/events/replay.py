"""Session Replay — query and audit event log for debugging and learning.

Provides APIs for:
1. Human-readable transcript of events
2. World state at any point in history
3. Finding events by type or NPC

Run with:
    cd ai-engine && python -m pytest tests/test_session_replay.py -v
"""

from typing import List, Optional, Dict, Any
from datetime import datetime

from events.store import EventStore
from events.types import NPC_MOVED, RELATIONSHIP_CHANGED, FACT_CANONIZED, TIME_ADVANCED, ACTION_RESOLVED


class SessionReplay:
    """Query and replay session events for debugging, auditing, and learning."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    async def get_session_transcript(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """All events in human-readable form.

        Args:
            session_id: Session to query
            limit: Maximum number of most-recent events to return (None = all)

        Returns:
            List of events, oldest first, each humanized to readable text.
            Example: {"event": "npc_moved", "npc": "Mara", "location": "tavern"}
        """
        events = await self.event_store.get_events(session_id, limit=limit)
        return [self._humanize_event(e) for e in events]

    async def get_state_at_time(
        self, session_id: str, event_index: int
    ) -> Dict[str, Any]:
        """World state after all events up to event_index (0-indexed).

        Replays all events up to that point and returns the projected state:
        NPC locations, relationships, canon facts, time elapsed, etc.

        Args:
            session_id: Session to query
            event_index: Which event to include (0 = after first event, etc.)

        Returns:
            Projected world state at that point in history.
        """
        events = await self.event_store.get_events(session_id)
        if event_index < 0 or event_index >= len(events):
            return {}

        # Replay all events up to and including event_index
        state = {}
        for event in events[: event_index + 1]:
            state = self.event_store.project(state, event)
        return state

    async def find_events_by_type(
        self, session_id: str, event_type: str
    ) -> List[Dict[str, Any]]:
        """All events of a given type in a session.

        Args:
            session_id: Session to query
            event_type: Type to match (e.g., "action_resolved", "npc_moved")

        Returns:
            All matching events, oldest first, humanized.
        """
        events = await self.event_store.get_events(session_id)
        matching = [e for e in events if e.get("type") == event_type]
        return [self._humanize_event(e) for e in matching]

    async def find_events_by_npc(
        self, session_id: str, npc_id: str
    ) -> List[Dict[str, Any]]:
        """All events mentioning an NPC (by npc_id, source_id, or target_id).

        Args:
            session_id: Session to query
            npc_id: NPC identifier to search for

        Returns:
            All events where this NPC appears, oldest first, humanized.
        """
        events = await self.event_store.get_events(session_id)
        matching = [
            e for e in events
            if any(
                e.get("payload", {}).get(k) == npc_id
                for k in ("npc_id", "source_id", "target_id")
            )
        ]
        return [self._humanize_event(e) for e in matching]

    def _humanize_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Convert event dict to human-readable format.

        Strips JSON noise, extracts meaning, and presents it simply.
        Example: {"npc_moved", "npc": "Mara", "location": "tavern"}
        """
        event_type = event.get("type")
        payload = event.get("payload", {})

        if event_type == ACTION_RESOLVED:
            return {
                "event": "action",
                "type": payload.get("action_type"),
                "success": payload.get("success", True),
                "error": payload.get("error"),
            }
        elif event_type == NPC_MOVED:
            return {
                "event": "npc_moved",
                "npc": payload.get("npc_id"),
                "location": payload.get("location"),
            }
        elif event_type == RELATIONSHIP_CHANGED:
            return {
                "event": "relationship",
                "source": payload.get("source_id"),
                "target": payload.get("target_id"),
                "type": payload.get("relationship_type"),
                "strength": payload.get("strength"),
            }
        elif event_type == FACT_CANONIZED:
            return {
                "event": "canon",
                "fact": payload.get("fact"),
            }
        elif event_type == TIME_ADVANCED:
            return {
                "event": "time_passed",
                "seconds": payload.get("duration_seconds"),
            }
        else:
            # Unknown type; return structure as-is
            return {
                "event": event_type or "unknown",
                "type": event_type,
                "payload": payload,
            }

    def format_transcript_for_chat(self, events: List[Dict[str, Any]]) -> str:
        """Format transcript events as readable chat message.

        Args:
            events: List of humanized events

        Returns:
            Markdown-formatted text suitable for posting to Foundry chat.
        """
        if not events:
            return "No events found."

        lines = ["**Session Transcript**:"]
        for i, evt in enumerate(events, 1):
            event_type = evt.get("event")
            if event_type == "action":
                status = "✅" if evt.get("success") else "❌"
                line = f"{i}. {status} {evt.get('type')}"
                if evt.get("error"):
                    line += f" — {evt['error']}"
            elif event_type == "npc_moved":
                line = f"{i}. 🚶 {evt.get('npc')} moved to {evt.get('location')}"
            elif event_type == "relationship":
                line = f"{i}. 🤝 {evt.get('source')} → {evt.get('target')}: {evt.get('type')} ({evt.get('strength')})"
            elif event_type == "canon":
                line = f"{i}. 📜 {evt.get('fact')}"
            elif event_type == "time_passed":
                hours = evt.get("seconds", 0) // 3600
                line = f"{i}. ⏰ {hours}h passed"
            else:
                line = f"{i}. {event_type}"
            lines.append(line)

        return "\n".join(lines)
