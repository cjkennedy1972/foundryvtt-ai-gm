"""Voice assignment for TTS narration.

Maps the GM narrator and each NPC to one of fifteen archetype voices based
on personality, class, and appearance cues extracted from their NPCRecord /
NPCPersonality. Archetypes are abstract tokens (not tied to any one TTS
backend's voice IDs) resolved to real model voices via TTS_VOICE_MAP.

Assignment is deterministic: once a voice is chosen it is stored on the
NPCRecord so the same character always sounds the same within a session.
"""

import hashlib
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from npc.registry import NPCRecord

logger = logging.getLogger(__name__)

# Archetype tokens available for NPC assignment (the GM narrator uses a
# separate reserved token, "fable", so no NPC ever sounds like the GM).
_DEEP_MALE     = "deep_male"      # villains, dwarves, warlocks, cold stern authority
_GRUFF_MALE    = "gruff_male"     # barbarians, brutes, aggressive fighters
_SAGE_MALE     = "sage_male"      # wizards, sorcerers, artificers, scholars
_REVERENT_MALE = "reverent_male"  # clerics, druids, monks, paladins
_HEARTY_MALE   = "hearty_male"    # merchants, innkeepers, bards, charismatic talkers
_SLY_MALE      = "sly_male"       # rogues, thieves, assassins, rangers
_PLAIN_MALE    = "plain_male"     # guards, soldiers, generic male NPCs
_NOBLE_MALE    = "noble_male"     # kings, lords, dukes

_MYSTIC_FEM  = "mystic_female"    # wizards, sorceresses, artificers, scholars
_WARM_FEM    = "warm_female"      # healers, clerics, druids, monks, innkeepers
_FIERCE_FEM  = "fierce_female"    # barbarians, fighters, aggressive warriors
_LIGHT_FEM   = "light_female"     # bards, sprites, halflings, young/cheerful/tricksters
_SLY_FEM     = "sly_female"       # rogues, thieves, assassins, rangers
_NOBLE_FEM   = "noble_female"     # queens, duchesses, regal nobles
_PLAIN_FEM   = "plain_female"     # guards, merchants, generic female NPCs

MALE_VOICES = [_DEEP_MALE, _GRUFF_MALE, _SAGE_MALE, _REVERENT_MALE,
               _HEARTY_MALE, _SLY_MALE, _PLAIN_MALE, _NOBLE_MALE]
FEMALE_VOICES = [_MYSTIC_FEM, _WARM_FEM, _FIERCE_FEM, _LIGHT_FEM,
                 _SLY_FEM, _NOBLE_FEM, _PLAIN_FEM]
VOICES = MALE_VOICES + FEMALE_VOICES

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
    "wizard":     (_SAGE_MALE, _MYSTIC_FEM),
    "sorcerer":   (_SAGE_MALE, _MYSTIC_FEM),
    "warlock":    (_DEEP_MALE, _MYSTIC_FEM),
    "cleric":     (_REVERENT_MALE, _WARM_FEM),
    "druid":      (_REVERENT_MALE, _WARM_FEM),
    "paladin":    (_REVERENT_MALE, _WARM_FEM),
    "fighter":    (_GRUFF_MALE, _FIERCE_FEM),
    "barbarian":  (_GRUFF_MALE, _FIERCE_FEM),
    "ranger":     (_SLY_MALE, _SLY_FEM),
    "rogue":      (_SLY_MALE, _SLY_FEM),
    "bard":       (_HEARTY_MALE, _LIGHT_FEM),
    "monk":       (_REVERENT_MALE, _WARM_FEM),
    "artificer":  (_SAGE_MALE, _MYSTIC_FEM),
    "scholar":    (_SAGE_MALE, _MYSTIC_FEM),
    "merchant":   (_HEARTY_MALE, _PLAIN_FEM),
    "guard":      (_PLAIN_MALE, _PLAIN_FEM),
    "innkeeper":  (_HEARTY_MALE, _WARM_FEM),
    "noble":      (_NOBLE_MALE, _NOBLE_FEM),
    "assassin":   (_SLY_MALE, _SLY_FEM),
    "thief":      (_SLY_MALE, _SLY_FEM),
}

# Personality trait → voice preference (applied when class match fails)
_TRAIT_VOICE_MALE: dict[str, str] = {
    "aggressive": _GRUFF_MALE,
    "stoic":      _DEEP_MALE,
    "brave":      _DEEP_MALE,
    "scholarly":  _SAGE_MALE,
    "intelligent": _SAGE_MALE,
    "cunning":    _SLY_MALE,
    "charming":   _HEARTY_MALE,
    "cheerful":   _HEARTY_MALE,
    "talkative":  _HEARTY_MALE,
    "friendly":   _HEARTY_MALE,
}

_TRAIT_VOICE_FEMALE: dict[str, str] = {
    "aggressive": _FIERCE_FEM,
    "stoic":      _PLAIN_FEM,
    "brave":      _FIERCE_FEM,
    "scholarly":  _MYSTIC_FEM,
    "intelligent": _MYSTIC_FEM,
    "cunning":    _SLY_FEM,
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
        candidates = FEMALE_VOICES
    elif gender == "male":
        candidates = MALE_VOICES
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
