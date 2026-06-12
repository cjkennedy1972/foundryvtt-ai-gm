from datetime import datetime, timezone
from persistence.db import Database
from state.models import GameState, GameMode, CombatState


class GameStateTracker:
    def __init__(self, db: Database):
        self.db = db
        self._state: GameState = GameState()

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
        self._state.updated_at = datetime.now(timezone.utc)
        await self.db.save_state("game_state", self._state.model_dump())

    async def save(self):
        await self._save_current()

    def set_mode(self, mode: GameMode):
        # Coerce strings ("combat") to the enum so .value access never breaks
        self._state.mode = GameMode(mode)

    def set_scene(self, scene: str):
        self._state.current_scene = scene

    def set_campaign(self, campaign: str):
        self._state.campaign = campaign

    def increment_session(self):
        self._state.session_number += 1

    def update_combat(self, in_combat: bool, round_num: int = 0, turn: int = 0, turn_order: list = None):
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

    def record_event(self, event: str):
        self._state.last_event = event

    def get_snapshot(self) -> str:
        return self._state.get_summary()

    @property
    def state(self) -> GameState:
        return self._state

    async def set_scene_data(self, data: dict):
        self._state.scene_data.update(data)
        await self.save()

    async def set_npc_context(self, context: str):
        self._state.npc_context = context
        await self.save()

    async def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self._state, key):
                setattr(self._state, key, value)
        await self.save()
