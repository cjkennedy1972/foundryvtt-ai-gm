"""
Pydantic schemas for validating LLM-produced actions before dispatch.

Each schema defines the exact fields an action type is allowed to carry.
Extra fields are rejected at validation time, and numeric fields are
bounded to game-safe ranges so hallucinated values cannot corrupt state.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

# Damage limits: negative = healing, positive = damage
MIN_DAMAGE = -200  # max heal in a single action
MAX_DAMAGE = 500   # max damage in a single action

# Coordinates on the grid (arbitrary safety bounds)
MIN_COORD = -50000
MAX_COORD = 50000


class NarrateAction(BaseModel):
    """Send narration as GM."""
    text: str = Field(..., min_length=1, max_length=4000)

    class Config:
        extra = "forbid"


class SpeakAction(BaseModel):
    """Speak as an NPC."""
    npc_name: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1, max_length=4000)
    whisper_to: Optional[str] = Field(None, min_length=1, max_length=200)

    class Config:
        extra = "forbid"


class RollAction(BaseModel):
    """Roll dice."""
    formula: str = Field(..., min_length=1, max_length=256)
    speaker: str = Field(..., min_length=1, max_length=200)
    flavor: Optional[str] = Field(None, max_length=500)

    class Config:
        extra = "forbid"


class MoveTokenAction(BaseModel):
    """Move a token on the grid."""
    token_id: str = Field(..., min_length=1)
    x: float = Field(..., ge=MIN_COORD, le=MAX_COORD)
    y: float = Field(..., ge=MIN_COORD, le=MAX_COORD)

    class Config:
        extra = "forbid"


class UpdateHpAction(BaseModel):
    """Apply damage or healing to an actor."""
    actor_uuid: str = Field(..., min_length=1)
    damage: int = Field(..., ge=MIN_DAMAGE, le=MAX_DAMAGE)
    hp_path: Optional[str] = Field("hp.value", min_length=1)

    class Config:
        extra = "forbid"

    @field_validator("hp_path")
    @classmethod
    def _sanitize_hp_path(cls, v: str) -> str:
        """Only allow simple dotted attribute paths (no injection)."""
        if not all(c.isalnum() or c in ("_", ".") for c in v):
            raise ValueError("hp_path must be a simple dotted path")
        return v


class PlaySoundAction(BaseModel):
    """Play a sound effect."""
    sound_name: str = Field(..., min_length=1, max_length=500)

    class Config:
        extra = "forbid"


class SwitchSceneAction(BaseModel):
    """Change the current scene."""
    scene_name: str = Field(..., min_length=1, max_length=200)

    class Config:
        extra = "forbid"


class StartEncounterAction(BaseModel):
    """Begin combat."""
    token_ids: List[str] = Field(..., min_length=1, max_length=50)

    class Config:
        extra = "forbid"


class EndEncounterAction(BaseModel):
    """End combat."""

    class Config:
        extra = "forbid"


class PromptPlayerAction(BaseModel):
    """Ask a player for input (uses player_id, not display name)."""
    player_id: str = Field(..., min_length=1, max_length=200)
    question: str = Field(..., min_length=1, max_length=4000)

    class Config:
        extra = "forbid"


# Schema registry
ACTION_SCHEMAS = {
    "narrate": NarrateAction,
    "speak": SpeakAction,
    "roll": RollAction,
    "move_token": MoveTokenAction,
    "update_hp": UpdateHpAction,
    "play_sound": PlaySoundAction,
    "switch_scene": SwitchSceneAction,
    "start_encounter": StartEncounterAction,
    "end_encounter": EndEncounterAction,
    "prompt_player": PromptPlayerAction,
}
