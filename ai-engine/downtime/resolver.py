"""DowntimeResolver — a player's between-session action, resolved with no live
session and no Foundry connection (CKP-102).

The player writes prose — "my ranger spends the week tracking the cult" — and
it goes through the same adjudication path a live turn takes: the NPC-tier
model proposes actions, RefereeAgent rules on them, and the outcome lands in
the event log. Nothing executes against Foundry, because by definition nobody
is connected.

THE NAMED DELIVERY PATH. Every simulated world change needs one before it may
be simulated, and this one is: the GM narrates the outcome through
NarrativeSink at the start of the next session (GameLoop._narrate_pending_
downtime). An outcome that has not been narrated has not been delivered, and
`pending()` keeps returning it until it has.

Off-session events attach to the campaign's most recent session id — the
pattern world_tick.clock.WorldTick established. The campaign is not yet the
event log's aggregate root (CKP-99 is still open in the code however the board
reads), so this keeps downtime on the same timeline as play instead of in a
parallel log.

The submitter never sees the outcome. The human operator is also the player
here, so `resolve()` returns confirmation and a reason-if-stopped, never the
prose — the first time they hear what their week produced is at the table.

The token spend cap is the existing one in llm.usage.TokenUsage, armed by
setting the usage context before the call. An unattended downtime turn is
charged and capped exactly like a live one.
"""

import logging
from typing import Any, Dict, List, Optional

from events.store import EventStore
from events.types import PLAYER_DOWNTIME_NARRATED, PLAYER_DOWNTIME_RESOLVED
from llm.router import ModelRouter
from persistence.db import Database
from referee.agent import RefereeAgent

logger = logging.getLogger(__name__)

_PROMPT = (
    "A player has taken a downtime action between sessions. Resolve it as the GM.\n"
    "Return one narrate action whose text is what actually happened over that "
    "stretch of time — concrete, two or three sentences, and willing to let the "
    "attempt partly fail. Add further actions only if the fiction demands them."
)


class DowntimeResolver:
    """Resolves one prose downtime action against a campaign's event log."""

    def __init__(
        self,
        db: Database,
        model_router: ModelRouter,
        referee: RefereeAgent,
        event_store: EventStore,
    ):
        self.db = db
        self.model_router = model_router
        self.referee = referee
        self.event_store = event_store

    async def resolve(self, campaign: str, player_name: str, action_text: str) -> Dict[str, Any]:
        """Resolve *action_text* for *player_name* in *campaign*.

        Returns a receipt: the player, their action, the session the outcome
        was logged against, and `stopped_reason` if nothing was logged. The
        outcome prose is deliberately absent — see the module docstring.
        """
        player_name = (player_name or "").strip()
        action_text = (action_text or "").strip()
        receipt: Dict[str, Any] = {
            "campaign": campaign,
            "player": player_name,
            "action": action_text,
            "session_id": None,
            "event_id": None,
            "resolved": False,
            "stopped_reason": None,
        }

        if not player_name or not action_text:
            receipt["stopped_reason"] = "empty_action"
            return receipt

        session_id = await self._resolve_session(campaign)
        receipt["session_id"] = session_id
        if not session_id:
            receipt["stopped_reason"] = "no_session"
            logger.warning(
                f"Downtime turn for '{player_name}' skipped: campaign '{campaign}' has no session history"
            )
            return receipt

        actions = await self._propose(campaign, session_id, player_name, action_text)
        if actions is None:
            receipt["stopped_reason"] = "llm_error"
            return receipt

        rulings = await self.referee.adjudicate_batch(actions)
        approved = [r.action for r in rulings if r.approved]
        outcome = self._outcome_text(approved)
        if not outcome:
            receipt["stopped_reason"] = "no_outcome"
            logger.warning(f"Downtime turn for '{player_name}' produced no narratable outcome")
            return receipt

        receipt["event_id"] = await self.event_store.append(
            session_id,
            campaign,
            PLAYER_DOWNTIME_RESOLVED,
            payload={
                "player": player_name,
                "action": action_text,
                "outcome": outcome,
                "approved_actions": approved,
            },
            description=f"Downtime — {player_name}: {action_text}",
        )
        receipt["resolved"] = True
        logger.info(f"Downtime turn resolved for '{player_name}' in campaign '{campaign}'")
        return receipt

    async def pending(self, campaign: str) -> List[Dict[str, Any]]:
        """Resolved downtime outcomes this campaign has never narrated, oldest first."""
        resolved: List[Dict[str, Any]] = []
        narrated: set = set()

        # get_campaign_session_ids is most-recent-first; reverse it so the
        # outcomes come back in the order they were lived through.
        for session_id in reversed(await self.db.get_campaign_session_ids(campaign)):
            for event in await self.event_store.get_events(session_id):
                payload = event.get("payload") or {}
                if event.get("type") == PLAYER_DOWNTIME_RESOLVED:
                    resolved.append({
                        "id": event.get("id"),
                        "session_id": session_id,
                        "player": payload.get("player", ""),
                        "action": payload.get("action", ""),
                        "outcome": payload.get("outcome", ""),
                    })
                elif event.get("type") == PLAYER_DOWNTIME_NARRATED:
                    narrated.update(payload.get("event_ids") or [])

        return [outcome for outcome in resolved if outcome["id"] not in narrated]

    async def mark_narrated(self, session_id: str, event_ids: List[int]) -> None:
        """Record that *event_ids* have now reached the table."""
        if not event_ids:
            return
        await self.event_store.append(
            session_id,
            PLAYER_DOWNTIME_NARRATED,
            payload={"event_ids": list(event_ids)},
            description=f"Narrated {len(event_ids)} downtime outcome(s) at session start",
        )

    # ─── internals ──────────────────────────────────────────────────────────

    async def _propose(
        self, campaign: str, session_id: str, player_name: str, action_text: str
    ) -> Optional[List[Dict[str, Any]]]:
        """The LLM half of the turn. Returns None if the call failed."""
        llm = self.model_router.get("npc")
        previous = llm.usage_context
        # Charge this turn to the session it is logged against, which is what
        # arms TokenUsage's hard cap. Restored afterwards so an off-session
        # turn cannot silently re-point a live session's accounting.
        llm.set_usage_context(session_id, campaign)
        try:
            result = await llm.generate(
                user_message=f"[Downtime] {player_name}: {action_text}",
                extra_context=_PROMPT,
            )
            return result.get("actions") or []
        except Exception:
            logger.error(f"Downtime resolution failed for '{player_name}'", exc_info=True)
            return None
        finally:
            llm.set_usage_context(*previous)

    async def _resolve_session(self, campaign: str) -> Optional[str]:
        sessions = await self.db.get_campaign_session_ids(campaign)
        return sessions[0] if sessions else None

    @staticmethod
    def _outcome_text(actions: List[Dict[str, Any]]) -> str:
        """The narratable prose out of an adjudicated action list."""
        for action in actions:
            text = (action.get("text") or action.get("description") or "").strip()
            if text:
                return text
        return ""
