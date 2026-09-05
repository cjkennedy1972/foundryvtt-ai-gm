"""Downtime endpoints: submit a between-session action, list what is pending (CKP-102).

Neither endpoint returns the resolved outcome. The operator driving the admin
panel is also a player at this table — they hear how their week went when the
GM narrates it at the next session start, not here.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import AppState, ErrorResponse, get_app_state
from config import settings
from downtime.resolver import DowntimeResolver
from events.store import EventStore
from llm.router import ModelRouter
from referee.agent import RefereeAgent

logger = logging.getLogger("ai-gm")

router = APIRouter(tags=["downtime"])


class DowntimeTurn(BaseModel):
    player: str
    action: str
    campaign: str = ""


def _resolver(state: AppState) -> DowntimeResolver:
    """A resolver over the running app's database.

    Reuses the live GameLoop's collaborators when there is one — it already
    owns the event store and the NPC-tier router — and builds a standalone
    set when there is not, which is the case this feature exists for: a
    downtime turn submitted with nothing connected.
    """
    loop = state.chat_listener
    if loop is not None:
        return loop.downtime
    event_store = EventStore(state.db)
    return DowntimeResolver(
        state.db,
        ModelRouter(state.llm_manager),
        RefereeAgent(),
        event_store,
    )


async def _campaign(state: AppState, requested: str) -> str:
    if requested:
        return requested
    info = await state.db.get_active_session_info()
    return (info or {}).get("campaign") or settings.default_campaign or ""


@router.post("/api/downtime")
async def submit_downtime_turn(turn: DowntimeTurn, state: AppState = Depends(get_app_state)):
    """Resolve a between-session action. Returns a receipt, never the outcome."""
    if state.db is None or state.llm_manager is None:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error="The engine is still starting up.", code="NOT_READY"
            ).model_dump(),
        )

    campaign = await _campaign(state, turn.campaign.strip())
    receipt = await _resolver(state).resolve(campaign, turn.player, turn.action)
    return {
        "status": "ok" if receipt["resolved"] else "error",
        "campaign": receipt["campaign"],
        "player": receipt["player"],
        "action": receipt["action"],
        "resolved": receipt["resolved"],
        "stopped_reason": receipt["stopped_reason"],
    }


@router.get("/api/downtime/pending")
async def list_pending_downtime(campaign: str = "", state: AppState = Depends(get_app_state)):
    """Downtime turns resolved but not yet narrated. Outcomes withheld."""
    if state.db is None:
        return {"campaign": "", "pending": []}

    resolved_campaign = await _campaign(state, campaign.strip())
    pending = await _resolver(state).pending(resolved_campaign)
    return {
        "campaign": resolved_campaign,
        "pending": [{"player": p["player"], "action": p["action"]} for p in pending],
    }
