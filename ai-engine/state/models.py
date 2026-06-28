from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class GameMode(str, Enum):
    EXPLORATION = "exploration"
    COMBAT = "combat"
    SOCIAL = "social"
    DRAMA = "drama"


class CombatState(BaseModel):
    round: int = 0
    turn: int = 0
    turn_order: List[str] = Field(default_factory=list)  # actor UUIDs
    in_combat: bool = False
    scene: str = ""


class GameState(BaseModel):
    mode: GameMode = GameMode.EXPLORATION
    current_scene: str = ""
    campaign: str = ""
    session_number: int = 1
    combat: CombatState = Field(default_factory=CombatState)
    scene_data: Dict[str, Any] = Field(default_factory=dict)
    npc_context: str = ""
    encounter_context: str = ""
    player_actors: Dict[str, str] = Field(default_factory=dict)  # actor_name -> user_id
    last_event: str = ""
    updated_at: Optional[datetime] = None

    def set_player_actors(self, mapping: Dict[str, str]):
        """Update the player actors mapping (actor_name -> user_id)."""
        self.player_actors = mapping

    def get_summary(self) -> str:
        """Return a concise summary for LLM context."""
        lines = []
        # Tolerate mode being a plain string (e.g. from deserialization)
        mode_str = self.mode.value if isinstance(self.mode, Enum) else str(self.mode)
        lines.append(f"Game Mode: {mode_str}")
        lines.append(f"Current Scene: {self.current_scene}")
        lines.append(f"Session: {self.session_number}")
        lines.append(f"Last Event: {self.last_event}")
        if self.combat.in_combat:
            lines.append(f"Combat: Round {self.combat.round}, Turn {self.combat.turn}")
            if self.combat.turn_order:
                lines.append(f"Turn Order: {', '.join(self.combat.turn_order)}")
        if self.player_actors:
            lines.append("Player Characters (use these user_ids for prompt_player):")
            for actor_name, user_id in self.player_actors.items():
                lines.append(f"  {actor_name}: {user_id}")
        return "\n".join(lines)
