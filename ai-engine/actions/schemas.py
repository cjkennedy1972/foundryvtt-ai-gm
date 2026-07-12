"""
Pydantic schemas for validating LLM-produced actions before dispatch.

Each schema defines the exact fields an action type is allowed to carry.
Extra fields (``__pydantic_extra``) are rejected at validation time, and
numeric fields are clamped/bounded so hallucinated values (e.g. damage=99999)
cannot corrupt the game state.
"""

from typing import Optional, List, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    model_config = ConfigDict(extra="forbid")


class SpeakAction(BaseModel):
    """speak as an NPC in Foundry chat."""

    npc_name: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1, max_length=4000)
    whisper_to: Optional[str] = Field(None, min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid")


class RollAction(BaseModel):
    """roll dice in Foundry."""

    formula: str = Field(..., min_length=MIN_FORMULA_LEN, max_length=MAX_FORMULA_LEN)
    speaker: str = Field(..., min_length=1, max_length=200)
    flavor: Optional[str] = Field(None, max_length=500)
    advantage: Optional[bool] = Field(None, description="True for advantage, False for disadvantage, None for normal")

    model_config = ConfigDict(extra="forbid")


class MoveTokenAction(BaseModel):
    """move a token on the grid."""

    token_id: str = Field(..., min_length=1)
    x: float = Field(..., ge=MIN_COORD, le=MAX_COORD)
    y: float = Field(..., ge=MIN_COORD, le=MAX_COORD)

    model_config = ConfigDict(extra="forbid")


class UpdateHpAction(BaseModel):
    """apply damage (positive) or healing (negative) to an actor."""

    actor_uuid: str = Field(..., min_length=1)
    damage: int = Field(..., ge=MIN_DAMAGE, le=MAX_DAMAGE)
    hp_path: Optional[str] = Field("hp.value", min_length=1)

    model_config = ConfigDict(extra="forbid")

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
    volume: float = Field(0.5, ge=0.0, le=1.0, description="Playback volume 0-1")

    model_config = ConfigDict(extra="forbid")


class PlayMusicAction(BaseModel):
    """play background music from a Foundry playlist."""

    playlist_name: str = Field(..., min_length=1, max_length=200)
    volume: float = Field(0.5, ge=0.0, le=1.0, description="0-1, with 0.5 as default (50%)")

    model_config = ConfigDict(extra="forbid")


class WhisperAction(BaseModel):
    """send a private message to a specific player."""

    player_id: str = Field(..., min_length=1, max_length=200,
                           description="Foundry user ID (not display name)")
    message: str = Field(..., min_length=1, max_length=4000)

    model_config = ConfigDict(extra="forbid")


class SwitchSceneAction(BaseModel):
    """change the current scene."""

    scene_name: str = Field(..., min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid")


class StartEncounterAction(BaseModel):
    """begin combat."""

    token_ids: Optional[List[str]] = Field(None, max_length=50,
        description="Specific token IDs to include. Omit to use all tokens on scene.")
    encounter_name: Optional[str] = Field(None, max_length=100,
        description="Optional name for the encounter, shown in Foundry's combat tracker.")
    auto_roll_initiative: Optional[bool] = Field(True, description="Auto-roll initiative for turn order")

    model_config = ConfigDict(extra="forbid")


class EndEncounterAction(BaseModel):
    """end combat."""

    model_config = ConfigDict(extra="forbid")


class PromptPlayerAction(BaseModel):
    """ask a specific player for input."""

    player_id: str = Field(..., min_length=1, max_length=200,
                           description="Foundry user ID (not display name)")
    question: str = Field(..., min_length=1, max_length=4000)

    model_config = ConfigDict(extra="forbid")


class CastSpellAction(BaseModel):
    """Cast a spell, optionally as a ritual."""

    actor_uuid: str = Field(..., min_length=1)
    spell_name: str = Field(..., min_length=1, max_length=200)
    spell_level: int = Field(..., ge=0, le=9, description="Spell level (0-9)")
    ritual: bool = Field(default=False, description="Cast as a ritual; only valid for ritual spells")

    model_config = ConfigDict(extra="forbid")


class SetWeatherAction(BaseModel):
    """set weather and atmosphere."""

    weather: str = Field(..., min_length=1, max_length=50,
                         description="Weather type (clear, rain, thunderstorm, snow, fog, etc)")

    model_config = ConfigDict(extra="forbid")


class SetTimeAction(BaseModel):
    """set time of day for atmosphere."""

    time: str = Field(..., min_length=1, max_length=50,
                      description="Time of day (dawn, morning, noon, afternoon, dusk, evening, night)")

    model_config = ConfigDict(extra="forbid")


class ApplyTokenEffectAction(BaseModel):
    """apply visual effects to tokens."""

    token_id: str = Field(..., min_length=1)
    effect_type: str = Field(..., min_length=1, max_length=50,
                             description="Effect type (condition, aura, animation, overlay)")
    effect_name: str = Field(..., min_length=1, max_length=100)
    duration: Optional[int] = Field(None, ge=0, description="Duration in turns")

    @field_validator("duration", mode="before")
    @classmethod
    def coerce_duration(cls, v):
        """LLM sometimes passes descriptive strings (e.g. 'until_encounter_start'); treat as None."""
        if isinstance(v, str):
            try:
                return int(v)
            except (ValueError, TypeError):
                return None
        return v

    model_config = ConfigDict(extra="forbid")


class UpdateVisionAction(BaseModel):
    """update vision and fog of war."""

    token_id: str = Field(..., min_length=1)
    vision_range: float = Field(..., ge=0, le=1000, description="Vision range in feet")
    has_light: bool = Field(False, description="Token has a light source")
    light_radius: Optional[float] = Field(None, ge=0, le=500, description="Light radius in feet")

    model_config = ConfigDict(extra="forbid")


class GenerateEncounterAction(BaseModel):
    """generate a new combat encounter."""

    party_level: int = Field(..., ge=1, le=20, description="Party level (1-20)")
    party_size: int = Field(..., ge=1, le=10, description="Number of party members")
    difficulty: Optional[str] = Field(
        "medium",
        pattern="^(trivial|easy|medium|hard|deadly)$",
        description="Encounter difficulty: trivial, easy, medium, hard, or deadly",
    )
    environment: Optional[str] = Field(None, max_length=100, description="Environment/location type")

    model_config = ConfigDict(extra="forbid")


class GenerateTreasureAction(BaseModel):
    """generate loot and treasure."""

    cr: float = Field(..., ge=0, le=30, description="Challenge Rating of defeated enemy")
    rarity_preference: Optional[str] = Field(None, max_length=50, description="Preference (common, uncommon, rare, very_rare, legendary)")

    model_config = ConfigDict(extra="forbid")


class GenerateNpcAction(BaseModel):
    """generate a new NPC."""

    role: Optional[str] = Field(None, max_length=100, description="NPC role (merchant, guard, wizard, etc)")
    faction: Optional[str] = Field(None, max_length=100, description="Faction or organization")

    model_config = ConfigDict(extra="forbid")


class GenerateQuestAction(BaseModel):
    """generate a new quest."""

    theme: Optional[str] = Field(None, max_length=100, description="Quest theme or type")
    difficulty: Optional[str] = Field(None, max_length=50, description="Difficulty level (easy, medium, hard, deadly)")

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Scene-building actions — walls, lights, sounds, full setup, map generation
# ---------------------------------------------------------------------------

class PlaceWallsAction(BaseModel):
    """place wall segments on the current scene."""

    walls: List[dict] = Field(
        ..., min_length=1, max_length=500,
        description="List of wall objects. Each: {c:[x0,y0,x1,y1], move:20, sense:20, door:0, ds:0}"
    )
    clear_existing: bool = Field(False, description="Remove all existing walls first")

    model_config = ConfigDict(extra="forbid")


class PlaceLightsAction(BaseModel):
    """place ambient light sources on the current scene."""

    lights: List[dict] = Field(
        ..., min_length=1, max_length=100,
        description="List of light objects. Each: {x, y, config:{bright, dim, color, alpha}}"
    )
    clear_existing: bool = Field(False, description="Remove all existing lights first")

    model_config = ConfigDict(extra="forbid")


class PlaceSoundsAction(BaseModel):
    """place ambient sound emitters on the current scene."""

    sounds: List[dict] = Field(
        ..., min_length=1, max_length=50,
        description="List of sound objects. Each: {x, y, path, radius, volume}"
    )
    clear_existing: bool = Field(False, description="Remove all existing sounds first")

    model_config = ConfigDict(extra="forbid")


class PlaceTokenAction(BaseModel):
    """place an actor's token on the current scene."""

    # The model targets an actor by name OR by uuid (it has both in context and
    # often passes uuid). Accept either; require at least one. Rejecting uuid as
    # an "extra" field silently dropped every enemy the AI tried to place.
    actor_name: Optional[str] = Field(None, max_length=200)
    uuid: Optional[str] = Field(None, max_length=200, description="Actor uuid (alternative to actor_name)")
    x: float = Field(..., ge=0, le=MAX_COORD)
    y: float = Field(..., ge=0, le=MAX_COORD)
    disposition: int = Field(0, ge=-1, le=1, description="-1=hostile, 0=neutral, 1=friendly")
    hidden: bool = Field(False, description="Start hidden from players")

    @model_validator(mode="after")
    def _require_identifier(self):
        if not (self.actor_name or self.uuid):
            raise ValueError("place_token requires actor_name or uuid")
        return self

    model_config = ConfigDict(extra="forbid")


class ConfigureSceneAction(BaseModel):
    """update scene-level vision, lighting, and grid settings."""

    darkness: Optional[float] = Field(None, ge=0.0, le=1.0, description="0=bright, 1=pitch black")
    global_illumination: Optional[bool] = Field(None, description="Illuminates entire scene, bypassing fog")
    fog_exploration: Optional[bool] = Field(None, description="Enable fog-of-war exploration mode")
    tokenVision: Optional[bool] = Field(None, description="Enable per-token vision")
    grid_size: Optional[int] = Field(None, ge=50, le=300, description="Pixels per grid square (typically 100)")
    scene_name: Optional[str] = Field(None, max_length=200, description="Scene to update (default: active scene)")

    model_config = ConfigDict(extra="forbid")


class SetupSceneAction(BaseModel):
    """complete scene setup — walls, lights, sounds, tokens, and config in one action.

    This is the primary world-building action. Use it to turn an empty scene
    into a fully interactive map with vision-blocking walls, atmospheric
    lighting, ambient sounds, and placed NPCs/monsters.
    """

    scene_name: Optional[str] = Field(None, max_length=200, description="Scene to set up (default: active scene)")
    background_src: Optional[str] = Field(None, max_length=500, description="Path or URL for the scene background image (e.g. 'worlds/valenthal/maps/gatehouse.webp'). Set this to give a black scene a visual map.")
    walls: Optional[List[dict]] = Field(
        None,
        description="Wall segments. Each: {c:[x0,y0,x1,y1], move:20, sense:20, door:0, ds:0}. "
                    "move/sense/sound: 0=none, 10=limited, 20=normal, 30=ethereal. "
                    "door: 0=wall, 1=door, 2=secret. ds: 0=closed, 1=open, 2=locked."
    )
    lights: Optional[List[dict]] = Field(
        None,
        description="Ambient lights. Each: {x, y, config:{bright:30, dim:60, color:'#ff4400', alpha:0.5}}"
    )
    sounds: Optional[List[dict]] = Field(
        None,
        description="Ambient sounds. Each: {x, y, path:'path/to/sound.ogg', radius:50, volume:0.5}"
    )
    tokens: Optional[List[dict]] = Field(
        None,
        description="Tokens to place. Each: {actor_name, x, y, disposition:-1/0/1, hidden:false}"
    )
    darkness: Optional[float] = Field(None, ge=0.0, le=1.0)
    grid_size: Optional[int] = Field(None, ge=50, le=300, description="Grid square size in pixels (Foundry default: 100)")
    fog_exploration: Optional[bool] = None
    global_illumination: Optional[bool] = None
    tokenVision: Optional[bool] = None
    clear_walls: bool = Field(False, description="Remove all existing walls before placing new ones")
    clear_lights: bool = Field(False, description="Remove all existing lights before placing new ones")
    clear_tokens: bool = Field(False, description="Remove all existing tokens before placing new ones (prevents orphaned tokens from prior sessions)")
    narrate: Optional[str] = Field(None, max_length=2000, description="Narration text to send after setup")

    model_config = ConfigDict(extra="forbid")


class GenerateMapAction(BaseModel):
    """generate an AI battle map image via ComfyUI and create a new Foundry scene."""

    prompt: str = Field(..., min_length=5, max_length=500,
                        description="Description of the map to generate")
    scene_name: str = Field(..., min_length=1, max_length=100,
                            description="Name for the new Foundry scene")
    style: str = Field("dungeon", description="Visual style: dungeon, overworld, fantasy_map")
    size: str = Field("medium", description="Size: small=1024px, medium=1536px, large=2048px")
    switch_to_scene: bool = Field(True, description="Activate the new scene after creation")
    narration: Optional[str] = Field(None, max_length=1000,
                                     description="Vivid scene intro played via TTS after map loads")

    model_config = ConfigDict(extra="forbid")


class PauseGameAction(BaseModel):
    """pause the game — halts AI-GM responses and pauses FoundryVTT for all players."""

    reason: Optional[str] = Field(None, max_length=200,
                                  description="Optional reason shown in chat (e.g. 'taking a short break')")

    model_config = ConfigDict(extra="forbid")


class ResumeGameAction(BaseModel):
    """resume the game after a pause — re-enables AI-GM and unpauses FoundryVTT."""

    model_config = ConfigDict(extra="forbid")


class ExecuteJSAction(BaseModel):
    """execute arbitrary Foundry JavaScript (power user / fallback action).

    Use only when no structured action covers the needed operation.
    The code runs in the Foundry client context with full API access.

    Security: `description` is validated against a strict pattern to reject
    anything that looks like a code payload. The actual JS code is gated by
    `ALLOW_EXECUTE_JS` and logged for audit.
    """

    code: str = Field(..., min_length=1, max_length=10000,
                      description="JavaScript to execute in the Foundry client")
    description: Optional[str] = Field(
        None,
        max_length=200,
        pattern=r"^[a-zA-Z0-9 _\-.,:;'!?]+$",
        description=(
            "Human-readable description. Only letters, numbers, spaces, "
            "punctuation allowed — no code, no variable names, no operators."
        )
    )

    @model_validator(mode="after")
    def _reject_code_in_description(self):
        """Reject description fields that look like JS code."""
        if self.description:
            desc_lower = self.description.lower()
            forbidden_patterns = [
                "const ", "let ", "var ", "function ", "return ",
                "await ", "async ", "=>", "()", "{}", "function(",
                "game.", "canvas.", "fromUuid", "execute_js",
            ]
            for pat in forbidden_patterns:
                if pat in desc_lower:
                    raise ValueError(
                        f"Description contains disallowed pattern '{pat}'. "
                        "Description must be natural language only."
                    )
        return self

    model_config = ConfigDict(extra="forbid")


class UseActionAction(BaseModel):
    """consume an action or bonus action in combat."""

    actor_uuid: str = Field(..., min_length=1)
    action_type: str = Field(..., min_length=1, max_length=50,
                             description="action, bonus_action, reaction, or movement")

    model_config = ConfigDict(extra="forbid")


class SkillCheckAction(BaseModel):
    """request a skill check from a player."""

    actor_uuid: str = Field(..., min_length=1, description="Actor UUID of the creature making the check")
    skill: str = Field(..., min_length=1, max_length=50, description="Skill name (e.g., stealth, perception)")
    dc: int = Field(..., ge=0, le=40, description="Difficulty class (0-40)")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for the check")
    advantage: Optional[bool] = Field(None, description="True for advantage, False for disadvantage, None for normal")

    model_config = ConfigDict(extra="forbid")


class ShortRestAction(BaseModel):
    """perform a short rest for one or more characters — hit dice recovery, class feature resets (e.g. Pact Magic)."""

    actor_uuids: list[str] = Field(..., min_length=1, max_length=10)

    model_config = ConfigDict(extra="forbid")


class LongRestAction(BaseModel):
    """perform a long rest for one or more characters — full HP, spell slots, hit dice, and feature resets."""

    actor_uuids: list[str] = Field(..., min_length=1, max_length=10)

    model_config = ConfigDict(extra="forbid")


class SetExhaustionAction(BaseModel):
    """adjust a creature's exhaustion level by delta (positive = gain, negative = recover)."""

    actor_uuid: str = Field(..., min_length=1)
    delta: int = Field(..., ge=-6, le=6, description="Levels to add (positive) or remove (negative); clamped 0-6")
    reason: Optional[str] = Field(None, max_length=200, description="What caused it, e.g. 'a forced march through the desert'")

    model_config = ConfigDict(extra="forbid")


class GrantInspirationAction(BaseModel):
    """grant Heroic Inspiration to a player character for good roleplay."""

    actor_uuid: str = Field(..., min_length=1)
    reason: Optional[str] = Field(None, max_length=300, description="What earned it, for the chat announcement")

    model_config = ConfigDict(extra="forbid")


class DeathSaveAction(BaseModel):
    """request a death saving throw from a creature at 0 HP."""

    actor_uuid: str = Field(..., min_length=1, description="Actor UUID of the creature making the death save")
    advantage: Optional[bool] = Field(None, description="True for advantage, False for disadvantage, None for normal")

    model_config = ConfigDict(extra="forbid")


class SavingThrowAction(BaseModel):
    """request an ability saving throw from a creature."""

    actor_uuid: str = Field(..., min_length=1, description="Actor UUID of the creature making the save")
    ability: str = Field(..., min_length=1, max_length=20, description="Ability (e.g., dexterity, constitution, wisdom)")
    dc: int = Field(..., ge=0, le=40, description="Difficulty class (0-40)")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for the save")
    advantage: Optional[bool] = Field(None, description="True for advantage, False for disadvantage, None for normal")

    model_config = ConfigDict(extra="forbid")


class UseSaveItemAction(BaseModel):
    """trigger a save-based item/spell (e.g. breath weapon, AoE spell) against one or more targets."""

    caster_uuid: str = Field(..., min_length=1, description="Actor UUID of the creature using the item/spell")
    item_name: str = Field(..., min_length=1, max_length=200)
    target_token_ids: list[str] = Field(..., min_length=1, max_length=20)

    model_config = ConfigDict(extra="forbid")


class EnvironmentalSaveAction(BaseModel):
    """trigger a saving throw from a trap/hazard/environmental effect against one or more targets."""

    ability: str = Field(..., min_length=1, max_length=20, description="Ability (e.g., dexterity, constitution, wisdom)")
    dc: int = Field(..., ge=0, le=40, description="Difficulty class (0-40)")
    target_token_ids: list[str] = Field(..., min_length=1, max_length=20)
    damage_formula: Optional[str] = Field(None, min_length=MIN_FORMULA_LEN, max_length=MAX_FORMULA_LEN,
                                           description="Dice formula for damage on a failed save, e.g. '2d6' (omit for no damage)")
    half_on_save: bool = Field(True, description="If True, a successful save halves damage instead of negating it")
    reason: Optional[str] = Field(None, max_length=300, description="What triggers the save, e.g. 'a poison gas trap'")

    model_config = ConfigDict(extra="forbid")


class ApplyConditionAction(BaseModel):
    """apply a condition to a creature."""

    actor_uuid: str = Field(..., min_length=1)
    condition: str = Field(..., min_length=1, max_length=100)
    duration: Optional[str] = Field(None, max_length=200, description="How long the condition lasts")

    model_config = ConfigDict(extra="forbid")


class OpportunityAttackAction(BaseModel):
    """trigger an opportunity attack when a creature moves away."""

    attacker_uuid: str = Field(..., min_length=1)
    target_uuid: str = Field(..., min_length=1)
    reason: Optional[str] = Field(None, max_length=200)

    model_config = ConfigDict(extra="forbid")


class TacticalAnalysisAction(BaseModel):
    """request tactical analysis of the current battlefield."""

    actor_uuid: str = Field(..., min_length=1)
    include_recommendations: bool = Field(True, description="Include tactical recommendations")

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Schema lookup — maps action type to its Pydantic model class.
# ---------------------------------------------------------------------------

ACTION_SCHEMAS: dict[str, type[BaseModel]] = {
    # Scene-building
    "place_walls": PlaceWallsAction,
    "place_lights": PlaceLightsAction,
    "place_sounds": PlaceSoundsAction,
    "place_token": PlaceTokenAction,
    "configure_scene": ConfigureSceneAction,
    "setup_scene": SetupSceneAction,
    "generate_map": GenerateMapAction,
    "execute_js": ExecuteJSAction,
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
    "death_save": DeathSaveAction,
    "grant_inspiration": GrantInspirationAction,
    "set_exhaustion": SetExhaustionAction,
    "short_rest": ShortRestAction,
    "long_rest": LongRestAction,
    "saving_throw": SavingThrowAction,
    "use_save_item": UseSaveItemAction,
    "environmental_save": EnvironmentalSaveAction,
    "apply_condition": ApplyConditionAction,
    "opportunity_attack": OpportunityAttackAction,
    "tactical_analysis": TacticalAnalysisAction,
    "set_weather": SetWeatherAction,
    "set_time": SetTimeAction,
    "apply_token_effect": ApplyTokenEffectAction,
    "update_vision": UpdateVisionAction,
    "generate_encounter": GenerateEncounterAction,
    "generate_treasure": GenerateTreasureAction,
    "generate_npc": GenerateNpcAction,
    "generate_quest": GenerateQuestAction,
    "pause_game": PauseGameAction,
    "resume_game": ResumeGameAction,
}
