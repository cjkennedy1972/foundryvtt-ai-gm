#!/usr/bin/env python3
"""tts/voice_assigner.py — 201 lines, referenced by no test.

Voice assignment has to be stable: an NPC whose voice changes between two
lines of dialogue is worse than one with a plain voice. That part is sound —
md5 over the name, a session cache, and the choice persisted onto the
NPCRecord.

_detect_gender was not. It split on whitespace and compared whole words, so
any pronoun carrying punctuation missed: "She's a healer." detected nothing
and fell through to the ungendered voice list. Prose is full of contractions
and pronouns at the end of a clause.

Run:
    cd ai-engine && python -m pytest tests/test_voice_assignment.py -v
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts.voice_assigner import (
    FEMALE_VOICES,
    MALE_VOICES,
    VoiceAssigner,
    _detect_gender,
    _stable_fallback,
)


@dataclass
class _Record:
    """The NPCRecord fields the assigner reads."""
    description: str = ""
    appearance: str = ""
    class_name: str = ""
    personality: Optional[dict] = None
    voice: Optional[str] = None


# ── gender detection ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("She is a healer", "female"),
    ("He runs the inn", "male"),
    ("A weary herbalist", None),
])
def test_plain_prose(text, expected):
    assert _detect_gender(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("She's a healer.", "female"),
    ("He's the innkeeper.", "male"),
    ("The barkeep nods at her.", "female"),
    ("Nobody trusts him,", "male"),
    ("Her shop, her rules.", "female"),
])
def test_punctuation_does_not_hide_the_pronoun(text, expected):
    """Whole-word matching over a whitespace split missed every one."""
    assert _detect_gender(text) == expected


def test_a_genuine_mix_stays_undecided():
    """Both pronouns once each is a tie, not a guess."""
    assert _detect_gender("his sword, her shield") is None


def test_the_more_frequent_pronoun_wins():
    assert _detect_gender("She hired him. She pays him well. She decides.") == "female"


# ── stability ─────────────────────────────────────────────────────────────

def test_the_same_name_always_gets_the_same_voice():
    assert len({_stable_fallback("Elara", "female") for _ in range(20)}) == 1


def test_the_fallback_respects_gender():
    assert _stable_fallback("Elara", "female") in FEMALE_VOICES
    assert _stable_fallback("Grukk", "male") in MALE_VOICES


def test_different_names_are_not_all_one_voice():
    voices = {_stable_fallback(f"NPC {i}", None) for i in range(40)}

    assert len(voices) > 1


def test_a_voice_already_on_the_record_is_honoured():
    """Re-assigning would change an NPC's voice mid-session."""
    record = _Record(voice="alloy", description="She is a healer")

    assert VoiceAssigner().get_voice("Elara", record) == "alloy"


def test_the_assignment_is_written_back_to_the_record():
    record = _Record(description="She is a healer")
    assigner = VoiceAssigner()

    voice = assigner.get_voice("Elara", record)

    assert record.voice == voice


def test_the_same_npc_gets_the_same_voice_twice():
    assigner = VoiceAssigner()

    first = assigner.get_voice("Elara", _Record(description="She is a healer"))
    second = assigner.get_voice("Elara", _Record(description="She is a healer"))

    assert first == second


def test_an_npc_with_no_record_still_gets_a_stable_voice():
    assigner = VoiceAssigner()

    assert assigner.get_voice("Wandering Merchant") == assigner.get_voice("Wandering Merchant")


# ── what drives the choice ────────────────────────────────────────────────

def test_a_class_picks_a_voice_matching_the_detected_gender():
    female = VoiceAssigner().get_voice("A", _Record(class_name="Wizard", description="She studies"))
    male = VoiceAssigner().get_voice("B", _Record(class_name="Wizard", description="He studies"))

    assert female != male
    assert female in FEMALE_VOICES or male in MALE_VOICES


def test_a_personality_trait_is_used_when_there_is_no_class():
    record = _Record(description="He tends the bar", personality={"temperament": ["cheerful"]})

    assert VoiceAssigner().get_voice("Barkeep", record)


def test_an_unrecognised_trait_falls_back_rather_than_failing():
    record = _Record(description="He tends the bar", personality={"temperament": ["ineffable"]})

    assert VoiceAssigner().get_voice("Barkeep", record) in MALE_VOICES
