"""Shared FastAPI dependencies: app state container, error handling.

Phase 1 of the modular architecture split (docs/ARCHITECTURE_REFACTOR.md).
main.py still owns `lifespan` (component construction/wiring is a single
340-line sequence with no natural seams) and populates `app.state` with an
AppState instance; routers import get_app_state/AppState from here instead
of from main, so routes can move into api/routes/*.py without a circular
import back to main.
"""

from typing import Any, Dict, Optional

from fastapi import Request
from pydantic import BaseModel

from foundry.client import FoundryClient
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
from state.tracker import GameStateTracker
from persistence.db import Database
from context.loader import CampaignLoader
from context.window_manager import ContextWindowManager
from combat.loop import CombatLoop
from scene.awareness import SceneAwareness
from context.reinforcement_manager import ContextReinforcementManager
from relay_proc.manager import RelayManager
from foundry.chat_listener import ChatListener
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
        self.context_manager: Optional[ContextWindowManager] = None
        self.combat_loop: Optional[CombatLoop] = None
        self.scene_awareness: Optional[SceneAwareness] = None
        self.reinforcement_mgr: Optional[ContextReinforcementManager] = None
        self.relay_manager: Optional[RelayManager] = None
        # NPC personality system (Tier 3)
        self.npc_registry: Optional[Any] = None  # NPCRegistry
        self.personality_engine: Optional[Any] = None  # PersonalityEngine
        # TTS narration
        self.tts_service: Optional[TTSService] = None
        # Immersion features (Tier 6)
        self.ambient_manager: Optional[Any] = None  # AmbientManager
        self.effects_manager: Optional[Any] = None  # EffectsManager
        self.vision_manager: Optional[Any] = None  # VisionManager
        self.macro_manager: Optional[Any] = None  # MacroManager
        self.item_manager: Optional[Any] = None  # ItemManager
        self.particle_manager: Optional[Any] = None  # ParticleManager


async def get_app_state(request: Request) -> AppState:
    """FastAPI dependency to inject app state into endpoints."""
    return request.app.state


def require_foundry(state: AppState) -> None:
    """Raise ApiError(503) unless a Foundry client is connected."""
    if not state.foundry_client or not state.foundry_client.is_connected:
        raise ApiError("Not connected to FoundryVTT", "FOUNDRY_NOT_CONNECTED", 503)
