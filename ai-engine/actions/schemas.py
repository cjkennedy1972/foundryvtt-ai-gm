"""
Pydantic schemas for validating LLM-produced actions before dispatch.

Each schema defines the exact fields an action type is allowed to carry.
Extra fields (``__pydantic_extra``) are rejected at validation time, and
numeric fields are clamped/bounded so hallucinated values (e.g. damage=99999)
cannot corrupt the game state.
"""

from typing import Optional, List, Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Shared defaults
# ---------------------------------------------------------------------------

# Damage limits: negative = healing, positive = damage.
# Prevents "one-shot" HP removal and accidental negative damage.
MIN_DAMAGE = -200  # max heal in a single action
MAX_DAMAGE = 500   # max damage in a single action

# Min/Max coordinates on the Foundry grid (arbitrary safety bounds).
MIN_COORD = -50000
MAX_COORD = 50000

# Dice formulas are sent straight to Foundry, so we require a non-empty
# string; the actual syntax is validated by Foundry's dice parser.
MIN_FORMULA_LEN = 1
MAX_FORMULA_LEN = 256


# ---------------------------------------------------------------------------
# Concrete action schemas (extra fields rejected by default)
# ---------------------------------------------------------------------------

class NarrateAction(BaseModel):
    """send narration as GM in Foundry chat."""

    text: str = Field(..., min_length=1, max_length=4000)

    class Config:
        extra = "forbid"


class SpeakAction(BaseModel):
    """speak as an NPC in Foundry chat."""

    npc_name: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1, max_length=4000)
    whisper_to: Optional[str] = Field(None, min_length=1, max_length=200)

    class Config:
        extra = "forbid"


class RollAction(BaseModel):
    """roll dice in Foundry."""

    formula: str = Field(..., min_length=MIN_FORMULA_LEN, max_length=MAX_FORMULA_LEN)
    speaker: str = Field(..., min_length=1, max_length=200)
    flavor: Optional[str] = Field(None, max_length=500)

    class Config:
        extra = "forbid"


class MoveTokenAction(BaseModel):
    """move a token on the grid."""

    token_id: str = Field(..., min_length=1)
    x: float = Field(..., ge=MIN_COORD, le=MAX_COORD)
    y: float = Field(..., ge=MIN_COORD, le=MAX_COORD)

    class Config:
        extra = "forbid"


class UpdateHpAction(BaseModel):
    """apply damage (positive) or healing (negative) to an actor."""

    actor_uuid: str = Field(..., min_length=1)
    damage: int = Field(..., ge=MIN_DAMAGE, le=MAX_DAMAGE)
    hp_path: Optional[str] = Field("hp.value", min_length=1)

    class Config:
        extra = "forbid"

    @field_validator("hp_path")
    @classmethod
    def _sanitize_hp_path(cls, v: str) -> str:
        """Only allow simple dotted attribute path; no brackets or dots with spaces."""
        if not all(c.isalnum() or c in ("_", ".") for c in v):
            raise ValueError("hp_path must be a simple dotted path (e.g. hp.value)")
        return v


class PlaySoundAction(BaseModel):
    """play a sound effect in Foundry."""

    sound_name: str = Field(..., min_length=1, max_length=500)

    class Config:
        extra = "forbid"


class SwitchSceneAction(BaseModel):
    """change the current scene."""

    scene_name: str = Field(..., min_length=1, max_length=200)

    class Config:
        extra = "forbid"


class StartEncounterAction(BaseModel):
    """begin combat."""

    token_ids: List[str] = Field(..., min_length=1, max_length=50)

    class Config:
        extra = "forbid"

    @field_validator("token_ids")
    @classmethod
    def _token_ids_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("start_encounter requires at least one token_id")
        return v


class EndEncounterAction(BaseModel):
    """end combat."""

    class Config:
        extra = "forbid"


class PromptPlayerAction(BaseModel):
    """ask a specific player for input."""

    player_id: str = Field(..., min_length=1, max_length=200,
                           description="Foundry user ID (not display name)")
    question: str = Field(..., min_length=1, max_length=4000)

    class Config:
        extra = "forbid"


# ---------------------------------------------------------------------------
# Schema lookup — maps action type to its Pydantic model class.
# ---------------------------------------------------------------------------

ACTION_SCHEMAS: dict[str, type[BaseModel]] = {
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
