from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class GameMode(str, Enum):
    EXPLORATION = "exploration"
    COMBAT = "combat"
    SOCIAL = "social"
    DRAMA = "drama"


class CombatState(BaseModel):
    round: int = 0
    turn: int = 0
    turn_order: List[str] = []  # actor UUIDs
    in_combat: bool = False
    scene: str = ""


class GameState(BaseModel):
    mode: GameMode = GameMode.EXPLORATION
    current_scene: str = ""
    campaign: str = ""
    session_number: int = 1
    combat: CombatState = CombatState()
    scene_data: Dict[str, Any] = {}
    npc_context: str = ""
    last_event: str = ""
    updated_at: datetime = None

    def get_summary(self) -> str:
        """Return a concise summary for LLM context."""
        lines = []
        lines.append(f"Game Mode: {self.mode.value}")
        lines.append(f"Current Scene: {self.current_scene}")
        lines.append(f"Session: {self.session_number}")
        lines.append(f"Last Event: {self.last_event}")
        if self.combat.in_combat:
            lines.append(f"Combat: Round {self.combat.round}, Turn {self.combat.turn}")
            if self.combat.turn_order:
                lines.append(f"Turn Order: {', '.join(self.combat.turn_order)}")
        return "\n".join(lines)
