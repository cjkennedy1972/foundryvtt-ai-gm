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
    advantage: Optional[bool] = Field(None, description="True for advantage, False for disadvantage, None for normal")

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


class PlayMusicAction(BaseModel):
    """play background music from a Foundry playlist."""

    playlist_name: str = Field(..., min_length=1, max_length=200)
    volume: float = Field(0.5, ge=0.0, le=1.0, description="0-1, with 0.5 as default (50%)")

    class Config:
        extra = "forbid"


class WhisperAction(BaseModel):
    """send a private message to a specific player."""

    player_id: str = Field(..., min_length=1, max_length=200,
                           description="Foundry user ID (not display name)")
    message: str = Field(..., min_length=1, max_length=4000)

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
    auto_roll_initiative: Optional[bool] = Field(True, description="Auto-roll initiative for turn order")

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


class CastSpellAction(BaseModel):
    """cast a spell, automatically managing spell slots."""

    actor_uuid: str = Field(..., min_length=1)
    spell_name: str = Field(..., min_length=1, max_length=200)
    spell_level: int = Field(..., ge=0, le=9, description="Spell level (0-9)")

    class Config:
        extra = "forbid"


class SetWeatherAction(BaseModel):
    """set weather and atmosphere."""

    weather: str = Field(..., min_length=1, max_length=50,
                         description="Weather type (clear, rain, thunderstorm, snow, fog, etc)")

    class Config:
        extra = "forbid"


class SetTimeAction(BaseModel):
    """set time of day for atmosphere."""

    time: str = Field(..., min_length=1, max_length=50,
                      description="Time of day (dawn, morning, noon, afternoon, dusk, evening, night)")

    class Config:
        extra = "forbid"


class ApplyTokenEffectAction(BaseModel):
    """apply visual effects to tokens."""

    token_id: str = Field(..., min_length=1)
    effect_type: str = Field(..., min_length=1, max_length=50,
                             description="Effect type (condition, aura, animation, overlay)")
    effect_name: str = Field(..., min_length=1, max_length=100)
    duration: Optional[int] = Field(None, ge=0, description="Duration in turns")

    class Config:
        extra = "forbid"


class UpdateVisionAction(BaseModel):
    """update vision and fog of war."""

    token_id: str = Field(..., min_length=1)
    vision_range: float = Field(..., ge=0, le=1000, description="Vision range in feet")
    has_light: bool = Field(False, description="Token has a light source")
    light_radius: Optional[float] = Field(None, ge=0, le=500, description="Light radius in feet")

    class Config:
        extra = "forbid"


class UseActionAction(BaseModel):
    """consume an action or bonus action in combat."""

    actor_uuid: str = Field(..., min_length=1)
    action_type: str = Field(..., min_length=1, max_length=50,
                             description="action, bonus_action, reaction, or movement")

    class Config:
        extra = "forbid"


class SkillCheckAction(BaseModel):
    """request a skill check from a player."""

    actor_uuid: str = Field(..., min_length=1, description="Actor UUID of the creature making the check")
    skill: str = Field(..., min_length=1, max_length=50, description="Skill name (e.g., stealth, perception)")
    dc: int = Field(..., ge=0, le=40, description="Difficulty class (0-40)")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for the check")
    advantage: Optional[bool] = Field(None, description="True for advantage, False for disadvantage, None for normal")

    class Config:
        extra = "forbid"


class ApplyConditionAction(BaseModel):
    """apply a condition to a creature."""

    actor_uuid: str = Field(..., min_length=1)
    condition: str = Field(..., min_length=1, max_length=100)
    duration: Optional[str] = Field(None, max_length=200, description="How long the condition lasts")

    class Config:
        extra = "forbid"


class OpportunityAttackAction(BaseModel):
    """trigger an opportunity attack when a creature moves away."""

    attacker_uuid: str = Field(..., min_length=1)
    target_uuid: str = Field(..., min_length=1)
    reason: Optional[str] = Field(None, max_length=200)

    class Config:
        extra = "forbid"


class TacticalAnalysisAction(BaseModel):
    """request tactical analysis of the current battlefield."""

    actor_uuid: str = Field(..., min_length=1)
    include_recommendations: bool = Field(True, description="Include tactical recommendations")

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
    "play_music": PlayMusicAction,
    "whisper": WhisperAction,
    "switch_scene": SwitchSceneAction,
    "start_encounter": StartEncounterAction,
    "end_encounter": EndEncounterAction,
    "prompt_player": PromptPlayerAction,
    "cast_spell": CastSpellAction,
    "use_action": UseActionAction,
    "skill_check": SkillCheckAction,
    "apply_condition": ApplyConditionAction,
    "opportunity_attack": OpportunityAttackAction,
    "tactical_analysis": TacticalAnalysisAction,
    "set_weather": SetWeatherAction,
    "set_time": SetTimeAction,
    "apply_token_effect": ApplyTokenEffectAction,
    "update_vision": UpdateVisionAction,
}
