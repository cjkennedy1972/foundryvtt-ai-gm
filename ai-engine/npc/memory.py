"""NPCMemory — what an NPC remembers, sourced from the event log
(events/store.py) rather than a separate memory store. An NPC "witnessed"
an event if its npc_id appears in that event's payload as npc_id, source_id,
or target_id — the identity fields already used by the NPC_MOVED and
RELATIONSHIP_CHANGED reducers (events/types.py).
"""

from typing import List, Optional

from events.store import EventStore

_IDENTITY_PAYLOAD_KEYS = ("npc_id", "source_id", "target_id")


class NPCMemory:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    async def recall(self, campaign: str, npc_id: str, limit: Optional[int] = None) -> List[dict]:
        """Events (oldest first) that mention *npc_id* in an identity field.
S
        Deliberately fetches the WHOLE campaign history and filters in
        Python rather than pushing `limit` down to the SQL layer: an
        npc_id-relevant event can be arbitrarily sparse in the full event
        stream, so "the most recent N events in the campaign" (what a SQL
        LIMIT would give) is not the same set as "the most recent N events
        relevant to this NPC" — pushing limit down would silently return
        fewer (or zero) results for an NPC that isn't mentioned often, even
        with real relevant history further back. Fetching everything is
        the correct behavior; it just doesn't scale to a very long-running
        campaign without an npc_id-aware SQL query (json_extract on
        payload), which is worth doing if this ever shows up as a real
        bottleneck.
        """
        events = await self.event_store.get_events(campaign)
        relevant = [
            e for e in events
            if any(e.get("payload", {}).get(k) == npc_id for k in _IDENTITY_PAYLOAD_KEYS)
        ]
        if limit is None:
            return relevant
        if limit <= 0:
            return []
        return relevant[-limit:]
