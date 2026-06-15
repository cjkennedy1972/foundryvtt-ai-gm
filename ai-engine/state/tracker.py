import asyncio
from datetime import datetime, timezone
from persistence.db import Database
from state.models import GameState, GameMode, CombatState


class GameStateTracker:
    """Tracks game state with thread-safe mutations protected by asyncio.Lock."""

    def __init__(self, db: Database):
        self.db = db
        self._state: GameState = GameState()
        self._state_lock = asyncio.Lock()  # Protects _state from concurrent mutations

    @staticmethod
    def _parse_datetime(val):
        """Convert ISO string or None to datetime."""
        if val is None:
            return None
        if isinstance(val, str):
            return datetime.fromisoformat(val)
        return val

    async def load(self):
        state_data = await self.db.load_state("game_state")
        if state_data:
            # Fix datetime field for Pydantic compatibility
            if "updated_at" in state_data and state_data["updated_at"]:
                state_data["updated_at"] = self._parse_datetime(state_data["updated_at"])
            self._state = GameState(**state_data)
        else:
            await self._save_current()

    async def _save_current(self):
        """Save current state to database (protected by lock in callers)."""
        self._state.updated_at = datetime.now(timezone.utc)
        await self.db.save_state("game_state", self._state.model_dump())

    async def save(self):
        """Save state to database with lock protection."""
        async with self._state_lock:
            await self._save_current()

    async def set_mode(self, mode: GameMode):
        """Set game mode with lock protection."""
        async with self._state_lock:
            # Coerce strings ("combat") to the enum so .value access never breaks
            self._state.mode = GameMode(mode)

    async def set_scene(self, scene: str):
        """Set current scene with lock protection."""
        async with self._state_lock:
            self._state.current_scene = scene

    async def set_campaign(self, campaign: str):
        """Set campaign with lock protection."""
        async with self._state_lock:
            self._state.campaign = campaign

    async def increment_session(self):
        """Increment session number with lock protection."""
        async with self._state_lock:
            self._state.session_number += 1

    async def update_combat(self, in_combat: bool, round_num: int = 0, turn: int = 0, turn_order: list = None):
        """Update combat state atomically with lock protection."""
        async with self._state_lock:
            self._state.combat.in_combat = in_combat
            if in_combat:
                self._state.combat.round = round_num
                self._state.combat.turn = turn
                if turn_order is not None:
                    self._state.combat.turn_order = turn_order
            else:
                self._state.combat.round = 0
                self._state.combat.turn = 0
                self._state.combat.turn_order = []

    async def record_event(self, event: str):
        """Record an event with lock protection."""
        async with self._state_lock:
            self._state.last_event = event

    def get_snapshot(self) -> str:
        """Get a snapshot of game state (read-only, no lock needed)."""
        return self._state.get_summary()

    @property
    def state(self) -> GameState:
        """Get reference to state (readers must handle potential concurrent updates)."""
        return self._state

    async def set_scene_data(self, data: dict):
        """Update scene data and persist with lock protection."""
        async with self._state_lock:
            self._state.scene_data.update(data)
            await self._save_current()

    async def set_npc_context(self, context: str):
        """Set NPC context and persist with lock protection."""
        async with self._state_lock:
            self._state.npc_context = context
            await self._save_current()

    async def update(self, **kwargs):
        """Update multiple state fields atomically with lock protection."""
        async with self._state_lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
            await self._save_current()
