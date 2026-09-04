"""EventStore — append-only event log with a replay-to-project accessor.

Wraps persistence.db.Database's typed-event columns (persistence/migrations.py
migration 1). This is intentionally thin: Database already owns the
connection/write-lock/commit machinery, EventStore only adds the
projection (replay) logic on top and an unknown-type fallback so an
unrecognized event never breaks replay.
"""

import logging
from typing import Any, Dict, List, Optional

from events.types import REDUCERS, _reduce_noop
from persistence.db import Database

logger = logging.getLogger(__name__)


class EventStore:
    def __init__(self, db: Database):
        self.db = db

    async def append(
        self, session_id: str, campaign: str, event_type: str, payload: Optional[dict] = None, description: str = ""
    ) -> int:
        return await self.db.record_typed_event(session_id, campaign, event_type, payload, description)
    
    async def get_events(self, campaign: str, limit: Optional[int] = None) -> List[dict]:
        return await self.db.get_events_full(campaign, limit)
    
    def project(self, state: Dict[str, Any], event: dict) -> Dict[str, Any]:
        reducer = REDUCERS.get(event.get("type"), _reduce_noop)
        try:
            return reducer(state, event.get("payload") or {})
        except Exception:
            logger.error(f"Failed to project event {event.get('id')} ({event.get('type')})", exc_info=True)
            return state
    
    async def replay(self, campaign: str) -> Dict[str, Any]:
        """Rebuild game state from the full event log, oldest first."""
        state: Dict[str, Any] = {}
        for event in await self.get_events(campaign):
            state = self.project(state, event)
        return state
