"""Voice assignment for TTS narration.

Maps the GM narrator and each NPC to one of the six LocalAI/OpenAI TTS voices
(alloy, echo, fable, onyx, nova, shimmer) based on personality, class, and
appearance cues extracted from their NPCRecord / NPCPersonality.

Assignment is deterministic: once a voice is chosen it is stored on the
NPCRecord so the same character always sounds the same within a session.
"""

import hashlib
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from npc.registry import NPCRecord

logger = logging.getLogger(__name__)

# All six available voices
VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

# Voice personality profiles — roughly maps to character archetypes
_DEEP_MALE   = "onyx"    # warriors, villains, dwarves, stern authority
_SAGE_MALE   = "fable"   # wizards, scholars, sages, priests, storytellers
_NEUTRAL_M   = "echo"    # guards, soldiers, merchants, generic male NPCs
_WARM_FEM    = "nova"    # healers, nobles, elves, commanding women
_LIGHT_FEM   = "shimmer" # sprites, bards, halflings, young women, tricksters
_NEUTRAL_F   = "alloy"   # generic female NPCs, rogues, travelers

# Keywords in NPC descriptions/appearance that indicate female gender
_FEMALE_WORDS = {
    "she", "her", "hers", "woman", "female", "lady", "queen", "princess",
    "duchess", "baroness", "countess", "witch", "priestess", "sorceress",
    "maiden", "sister", "daughter", "matron", "abbess", "empress",
}

# Keywords that indicate male gender
_MALE_WORDS = {
    "he", "his", "him", "man", "male", "lord", "king", "prince", "duke",
    "baron", "count", "knight", "warrior", "soldier", "priest", "wizard",
    "sorcerer", "warlock", "monk", "paladin", "ranger", "brother", "father",
}

# Class → voice mapping (checked before generic trait logic)
_CLASS_VOICE: dict[str, tuple[str, str]] = {
    # (male_voice, female_voice)
    "wizard":     (_SAGE_MALE, _WARM_FEM),
    "sorcerer":   (_SAGE_MALE, _WARM_FEM),
    "warlock":    (_DEEP_MALE, _LIGHT_FEM),
    "cleric":     (_SAGE_MALE, _WARM_FEM),
    "druid":      (_SAGE_MALE, _WARM_FEM),
    "paladin":    (_DEEP_MALE, _WARM_FEM),
    "fighter":    (_DEEP_MALE, _NEUTRAL_F),
    "barbarian":  (_DEEP_MALE, _NEUTRAL_F),
    "ranger":     (_NEUTRAL_M, _NEUTRAL_F),
    "rogue":      (_NEUTRAL_M, _NEUTRAL_F),
    "bard":       (_NEUTRAL_M, _LIGHT_FEM),
    "monk":       (_SAGE_MALE, _WARM_FEM),
    "artificer":  (_SAGE_MALE, _NEUTRAL_F),
    "scholar":    (_SAGE_MALE, _WARM_FEM),
    "merchant":   (_NEUTRAL_M, _NEUTRAL_F),
    "guard":      (_NEUTRAL_M, _NEUTRAL_F),
    "innkeeper":  (_NEUTRAL_M, _WARM_FEM),
    "noble":      (_NEUTRAL_M, _WARM_FEM),
    "assassin":   (_NEUTRAL_M, _NEUTRAL_F),
    "thief":      (_NEUTRAL_M, _NEUTRAL_F),
}

# Personality trait → voice preference (applied when class match fails)
_TRAIT_VOICE_MALE: dict[str, str] = {
    "aggressive": _DEEP_MALE,
    "stoic":      _DEEP_MALE,
    "brave":      _DEEP_MALE,
    "scholarly":  _SAGE_MALE,
    "intelligent": _SAGE_MALE,
    "cunning":    _NEUTRAL_M,
    "charming":   _NEUTRAL_M,
    "cheerful":   _NEUTRAL_M,
    "talkative":  _NEUTRAL_M,
    "friendly":   _NEUTRAL_M,
}

_TRAIT_VOICE_FEMALE: dict[str, str] = {
    "aggressive": _NEUTRAL_F,
    "stoic":      _NEUTRAL_F,
    "brave":      _WARM_FEM,
    "scholarly":  _WARM_FEM,
    "intelligent": _WARM_FEM,
    "cunning":    _NEUTRAL_F,
    "charming":   _WARM_FEM,
    "cheerful":   _LIGHT_FEM,
    "talkative":  _LIGHT_FEM,
    "friendly":   _WARM_FEM,
}


def _detect_gender(text: str) -> Optional[str]:
    """Return 'female', 'male', or None based on pronoun/keyword presence."""
    words = set(text.lower().split())
    female_hits = words & _FEMALE_WORDS
    male_hits   = words & _MALE_WORDS
    if female_hits and not male_hits:
        return "female"
    if male_hits and not female_hits:
        return "male"
    # Score by count when both appear (e.g. "she hired him")
    if len(female_hits) > len(male_hits):
        return "female"
    if len(male_hits) > len(female_hits):
        return "male"
    return None


def _stable_fallback(npc_name: str, gender: Optional[str]) -> str:
    """Pick a voice deterministically from the name hash."""
    h = int(hashlib.md5(npc_name.encode()).hexdigest(), 16)
    if gender == "female":
        candidates = [_WARM_FEM, _LIGHT_FEM, _NEUTRAL_F]
    elif gender == "male":
        candidates = [_NEUTRAL_M, _SAGE_MALE, _DEEP_MALE]
    else:
        candidates = VOICES
    return candidates[h % len(candidates)]


class VoiceAssigner:
    """Assigns and caches TTS voices for named speakers."""

    def __init__(self):
        # name → voice (session-level cache, supplements NPCRecord.voice)
        self._cache: dict[str, str] = {}

    def get_voice(self, npc_name: str, npc_record: Optional["NPCRecord"] = None) -> str:
        """Return the voice for an NPC, assigning one if not yet set."""
        # 1. Honour an already-persisted assignment on the record
        if npc_record is not None and getattr(npc_record, "voice", None):
            return npc_record.voice

        # 2. Session cache hit
        if npc_name in self._cache:
            return self._cache[npc_name]

        voice = self._assign(npc_name, npc_record)
        self._cache[npc_name] = voice

        # Persist on record so it survives re-lookups within the session
        if npc_record is not None:
            npc_record.voice = voice

        logger.info(f"[TTS] Assigned voice '{voice}' to NPC '{npc_name}'")
        return voice

    def _assign(self, npc_name: str, npc_record: Optional["NPCRecord"]) -> str:
        if npc_record is None:
            return _stable_fallback(npc_name, None)

        # Gather all descriptive text for gender detection
        text_blob = " ".join(filter(None, [
            npc_record.description or "",
            npc_record.appearance or "",
            npc_name,
        ]))
        gender = _detect_gender(text_blob)

        # Class-based assignment
        class_name = (npc_record.class_name or "").lower()
        for cls_key, (male_v, female_v) in _CLASS_VOICE.items():
            if cls_key in class_name:
                return female_v if gender == "female" else male_v

        # Personality trait-based assignment
        personality = getattr(npc_record, "personality", None)
        if personality:
            all_traits: list[str] = []
            if isinstance(personality, dict):
                for trait_list in personality.values():
                    if isinstance(trait_list, list):
                        all_traits.extend(trait_list)

            trait_map = _TRAIT_VOICE_FEMALE if gender == "female" else _TRAIT_VOICE_MALE
            for trait in all_traits:
                if trait in trait_map:
                    return trait_map[trait]

        return _stable_fallback(npc_name, gender)
