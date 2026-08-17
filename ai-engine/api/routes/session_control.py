"""Session control API endpoints — in-Foundry control panel endpoints.

Provides REST endpoints for controlling the autonomous GM:
- Pause/resume AI processing
- Trigger idle beats and NPC turns
- Query session status and settlement state
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, List, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


class SessionStatus(BaseModel):
    """Current session status."""
    session_id: Optional[str] = None
    campaign: Optional[str] = None
    is_running: bool = False
    current_time: Optional[str] = None
    turn_count: int = 0


class SettlementLocationResponse(BaseModel):
    """Settlement location query response."""
    settlement_id: str
    time_of_day: str
    locations: Dict[str, List[str]]  # location -> [npc_ids]


class SettlementListItem(BaseModel):
    """Settlement list item."""
    id: str
    name: str
    region: str
    population: int
    npc_count: int
    building_count: int


def create_session_control_router(app_state) -> APIRouter:
    """Create session control endpoints.

    Args:
        app_state: AppState instance with access to ChatListener, db, etc.

    Returns:
        APIRouter with session control endpoints
    """
    router = APIRouter(prefix="/api/session", tags=["session-control"])

    @router.get("/status", response_model=SessionStatus)
    async def get_session_status():
        """Get current session status."""
        try:
            session_info = await app_state.db.get_active_session_info()
            if not session_info:
                return SessionStatus(is_running=False)

            listener = getattr(app_state, "chat_listener", None)
            is_running = getattr(listener, "_running", False) if listener else False
            current_time = None
            if listener and hasattr(listener, "_world_clock"):
                current_time = listener._world_clock.get_current_time()

            return SessionStatus(
                session_id=session_info.get("session_id"),
                campaign=session_info.get("campaign"),
                is_running=is_running,
                current_time=current_time,
                turn_count=session_info.get("turn_count", 0),
            )
        except Exception as e:
            logger.error(f"Failed to get session status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/pause")
    async def pause_session():
        """Pause AI processing."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener:
                raise HTTPException(status_code=400, detail="Chat listener not available")

            listener._running = False
            logger.info("Session paused via API")
            return {"status": "paused"}
        except Exception as e:
            logger.error(f"Failed to pause session: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/resume")
    async def resume_session():
        """Resume AI processing."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener:
                raise HTTPException(status_code=400, detail="Chat listener not available")

            listener._running = True
            logger.info("Session resumed via API")
            return {"status": "running"}
        except Exception as e:
            logger.error(f"Failed to resume session: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/idle-beat")
    async def trigger_idle_beat():
        """Trigger an idle beat (proactive AI turn)."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener:
                raise HTTPException(status_code=400, detail="Chat listener not available")

            session_id = await app_state.db.get_active_session()
            if not session_id:
                raise HTTPException(status_code=400, detail="No active session")

            # Trigger idle beat
            await listener._run_proactive_action(reason="gm-triggered-idle-beat")
            logger.info("Idle beat triggered via API")
            return {"status": "idle-beat-triggered"}
        except Exception as e:
            logger.error(f"Failed to trigger idle beat: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/settlements", response_model=List[SettlementListItem])
    async def list_settlements():
        """List all settlements in the campaign."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener or not hasattr(listener, "_world_clock"):
                return []

            settlements = listener._world_clock.list_settlements()
            return [
                SettlementListItem(
                    id=s.id,
                    name=s.name,
                    region=s.region,
                    population=s.population,
                    npc_count=len(s.npcs),
                    building_count=len(s.buildings),
                )
                for s in settlements
            ]
        except Exception as e:
            logger.error(f"Failed to list settlements: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/settlements/{settlement_id}",
        response_model=SettlementLocationResponse,
    )
    async def query_settlement_locations(
        settlement_id: str,
        time_of_day: Optional[str] = None,
    ):
        """Query NPC locations in a settlement."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener or not hasattr(listener, "_world_clock"):
                raise HTTPException(status_code=400, detail="Settlement system not available")

            locations = await listener._world_clock.query_location_at_time(
                settlement_id,
                time_of_day,
            )

            actual_time = time_of_day or listener._world_clock.get_current_time()
            return SettlementLocationResponse(
                settlement_id=settlement_id,
                time_of_day=actual_time,
                locations=locations,
            )
        except Exception as e:
            logger.error(f"Failed to query settlement: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
