"""Shared FastAPI dependencies: app state container, error handling, admin WS broadcast.

Phase 1 of the modular architecture split (docs/architecture-refactor.md).
main.py still owns `lifespan` (component construction/wiring is a single
340-line sequence with no natural seams) and populates `app.state` with an
AppState instance; routers import get_app_state/AppState from here instead
of from main, so routes can move into api/routes/*.py without a circular
import back to main.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("ai-gm")

from foundry.client import FoundryClient
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
from state.tracker import GameStateTracker
from persistence.db import Database
from context.loader import CampaignLoader
from combat.loop import CombatLoop
from scene.awareness import SceneAwareness
from context.reinforcement_manager import ContextReinforcementManager
from relay_proc.manager import RelayManager
from foundry.chat_listener import ChatListener
from immersion.ambient import AmbientManager
from immersion.effects import EffectsManager
from immersion.items import ItemManager
from immersion.macros import MacroManager
from immersion.particles import ParticleManager
from immersion.vision import VisionManager
from llm.usage import TokenUsage
from npc.personality import PersonalityEngine
from npc.registry import NPCRegistry
from tts.service import TTSService
class ErrorResponse(BaseModel):
    """Standard error response format for all endpoints."""
    status: str = "error"
    error: str
    code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class ApiError(Exception):
    """Raise from any endpoint; rendered as an ErrorResponse by the handler."""

    def __init__(self, error: str, code: str = "ERROR", status: int = 400):
        super().__init__(error)
        self.error = error
        self.code = code
        self.status = status


class AppState:
    """Encapsulates all application state and component instances."""

    def __init__(self):
        self.db: Optional[Database] = None
        self.foundry_client: Optional[FoundryClient] = None
        self.llm_manager: Optional[LLMManager] = None
        self.action_dispatcher: Optional[ActionDispatcher] = None
        self.state_tracker: Optional[GameStateTracker] = None
        self.chat_listener: Optional[ChatListener] = None
        self.campaign_loader: Optional[CampaignLoader] = None
        self.combat_loop: Optional[CombatLoop] = None
        self.scene_awareness: Optional[SceneAwareness] = None
        self.reinforcement_mgr: Optional[ContextReinforcementManager] = None
        self.relay_manager: Optional[RelayManager] = None
        # NPC personality system (Tier 3)
        self.npc_registry: Optional[NPCRegistry] = None
        self.personality_engine: Optional[PersonalityEngine] = None
        # TTS narration
        self.tts_service: Optional[TTSService] = None
        # Immersion features (Tier 6)
        self.ambient_manager: Optional[AmbientManager] = None
        self.effects_manager: Optional[EffectsManager] = None
        self.vision_manager: Optional[VisionManager] = None
        self.macro_manager: Optional[MacroManager] = None
        self.item_manager: Optional[ItemManager] = None
        self.particle_manager: Optional[ParticleManager] = None
        self.token_usage: Optional[TokenUsage] = None


async def get_app_state(request: Request) -> AppState:
    """FastAPI dependency to inject app state into endpoints."""
    return request.app.state


def internal_error(context: str, exc: Exception, **extra: Any) -> JSONResponse:
    """Log the exception in full, return a message safe to hand a client.

    Routes used to return `str(e)` straight to the caller, which CodeQL flags
    as py/stack-trace-exposure: an exception message routinely carries absolute
    paths, SQL fragments or internal hostnames. The operator still needs to
    triage, so the traceback goes to ai-gm.log and the response keeps the
    exception *type*, which is enough to tell a timeout from a permission error
    without describing the filesystem.
    """
    # exc_info=exc, not logger.exception(): this helper is also called from
    # places where no exception is currently being handled.
    logger.error("%s: %s", context, exc, exc_info=exc)
    payload = {
        "status": "error",
        "error": f"{context} ({type(exc).__name__}) — see ai-gm.log for details",
    }
    payload.update(extra)
    return JSONResponse(status_code=500, content=payload)


def require_foundry(state: AppState) -> None:
    """Raise ApiError(503) unless a Foundry client is connected."""
    if not state.foundry_client or not state.foundry_client.is_connected:
        raise ApiError("Not connected to FoundryVTT", "FOUNDRY_NOT_CONNECTED", 503)


# --- WebSocket broadcast for admin panel ---

websocket_clients: List[WebSocket] = []


async def broadcast_state_update(data: dict):
    """Broadcast state updates to all connected admin WebSocket clients."""
    msg = json.dumps(data)
    for ws in list(websocket_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            # Guard the remove: a concurrent broadcast may have already pruned
            # this dead socket, and an unguarded list.remove() would raise
            # ValueError that escapes into the combat-turn callbacks.
            if ws in websocket_clients:
                websocket_clients.remove(ws)
