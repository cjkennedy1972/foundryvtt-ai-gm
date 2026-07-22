"""Tests for npc.personality — keyword-based personality parsing and the
consistency check used to keep NPC dialogue in character."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npc.personality import PersonalityEngine, NPCPersonality


DESC = (
    "A brave and intelligent but evil warlord. Charming to allies, aggressive "
    "to foes. motivation: revenge. flaw: greed. He speaks with a thick northern accent."
)


def test_parse_extracts_traits_across_categories():
    eng = PersonalityEngine()
    p = eng.parse_npc_description("npc1", "Vorlag", DESC)
    assert "aggressive" in p.traits.get("temperament", [])
    assert "intelligent" in p.traits.get("intellect", [])
    assert "evil" in p.traits.get("morality", [])
    assert "brave" in p.traits.get("courage", [])
    assert "charming" in p.traits.get("sociability", [])


def test_parse_extracts_sections_and_speech():
    eng = PersonalityEngine()
    p = eng.parse_npc_description("npc1", "Vorlag", DESC)
    assert any("revenge" in m for m in p.motivations)
    assert any("greed" in f for f in p.flaws)
    assert p.speech_pattern is not None
    assert "accent" in p.speech_pattern or "northern" in p.speech_pattern


def test_parse_stores_and_retrieves():
    eng = PersonalityEngine()
    eng.parse_npc_description("npc1", "Vorlag", DESC)
    assert eng.get_npc_personality("npc1") is not None
    assert eng.get_npc_personality("missing") is None


def test_get_context_empty_for_unknown():
    eng = PersonalityEngine()
    assert eng.get_npc_context("nobody") == ""


def test_prompt_context_renders_all_populated_sections():
    p = NPCPersonality(
        npc_id="x", npc_name="Mara", description="",
        traits={"temperament": ["calm"]},
        strengths=["diplomacy"], flaws=["pride"], motivations=["peace"],
        mannerisms=["taps cane"], speech_pattern="formal",
        relationships={"King": "advisor"},
    )
    ctx = p.to_prompt_context()
    for token in ("Mara", "calm", "diplomacy", "pride", "peace", "taps cane", "formal", "advisor"):
        assert token in ctx


def test_prompt_context_minimal_is_just_name():
    p = NPCPersonality(npc_id="x", npc_name="Blank", description="")
    assert p.to_prompt_context() == "**Blank**"


def test_consistency_no_profile_returns_true():
    eng = PersonalityEngine()
    ok, msg = eng.check_consistency("unknown", ["hello"])
    assert ok is True
    assert "No personality" in msg


def test_consistency_matching_dialogue_is_consistent():
    eng = PersonalityEngine()
    eng.parse_npc_description("npc1", "Vorlag", "He is aggressive and evil.")
    ok, msg = eng.check_consistency("npc1", ["I will crush you, aggressive and evil as I am!"])
    assert ok is True
    assert "Consistency" in msg


def test_consistency_offcharacter_dialogue_flagged():
    eng = PersonalityEngine()
    eng.parse_npc_description("npc1", "Vorlag", "He is aggressive and evil and cunning.")
    ok, _ = eng.check_consistency("npc1", ["What a peaceful, cheerful, lovely morning it is."])
    assert ok is False
