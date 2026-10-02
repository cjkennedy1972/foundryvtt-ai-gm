#!/usr/bin/env python3
"""RulesEngine (rules/engine.py): spell/condition lookups, modifiers and DCs.

These assert actual computed values against the 5e rules the methods claim to
implement, not just "it returned something" — ability modifiers, proficiency
bonus by level and the advantage/disadvantage condition sets are all checked
against their real PHB values.

Run:
    cd ai-engine && python -m pytest tests/test_rules_engine.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules.engine import RulesEngine


def _engine():
    return RulesEngine()


# ── spells ──────────────────────────────────────────────────────────────

def test_get_spell_is_case_insensitive_and_returns_the_real_entry():
    engine = _engine()
    spell = engine.get_spell("FIREBALL")
    assert spell is not None
    assert spell["level"] == 3
    assert spell["school"] == "evocation"


def test_get_spell_returns_none_for_an_unknown_spell():
    assert _engine().get_spell("nonexistent spell") is None


def test_search_spells_matches_by_name_substring():
    results = _engine().search_spells("fire")
    names = [r["name"] for r in results]
    assert names == ["fireball"]
    assert results[0]["level"] == 3


def test_search_spells_matches_by_description_keyword_not_just_name():
    """"missile" only appears in magic missile's description/name; "mightiest"
    only appears in wish's description, so a hit here proves the description
    branch actually runs, not just the name branch."""
    results = _engine().search_spells("mightiest")
    assert [r["name"] for r in results] == ["wish"]


def test_search_spells_returns_empty_list_for_no_match():
    assert _engine().search_spells("xyzzynotaspell") == []


# ── conditions ──────────────────────────────────────────────────────────

def test_get_condition_is_case_insensitive_and_returns_the_real_text():
    description = _engine().get_condition("Prone")
    assert description is not None
    assert "crawling" in description.lower()


def test_get_condition_returns_none_for_an_unknown_condition():
    assert _engine().get_condition("not-a-condition") is None


# ── ability modifiers ────────────────────────────────────────────────────

def test_ability_modifier_matches_the_phb_table():
    engine = _engine()
    assert engine.get_ability_modifier(10) == 0
    assert engine.get_ability_modifier(11) == 0
    assert engine.get_ability_modifier(20) == 5
    assert engine.get_ability_modifier(1) == -5
    assert engine.get_ability_modifier(8) == -1
    assert engine.get_ability_modifier(7) == -2


# ── DCs ───────────────────────────────────────────────────────────────────

def test_suggest_dc_is_case_insensitive_and_matches_the_table():
    engine = _engine()
    assert engine.suggest_dc("Easy") == 10
    assert engine.suggest_dc("very_hard") == 25


def test_suggest_dc_falls_back_to_medium_for_an_unknown_difficulty():
    assert _engine().suggest_dc("impossible-typo") == 15


def test_suggest_skill_dc_combines_dc_table_and_skill_ability():
    result = _engine().suggest_skill_dc("stealth", difficulty="hard")
    assert result["dc"] == 20
    assert result["ability"] == "dexterity"
    assert result["skill"] == "stealth"
    assert "stealth" in result["notes"] and "dexterity" in result["notes"]


def test_suggest_skill_dc_defaults_to_medium_difficulty():
    result = _engine().suggest_skill_dc("perception")
    assert result["dc"] == 15
    assert result["ability"] == "wisdom"


def test_suggest_skill_dc_returns_none_ability_for_an_unknown_skill():
    result = _engine().suggest_skill_dc("not-a-real-skill")
    assert result["ability"] is None


# ── proficiency bonus / skill modifier ──────────────────────────────────

def test_proficiency_bonus_matches_the_5e_progression():
    engine = _engine()
    assert engine.calculate_proficiency_bonus(1) == 2
    assert engine.calculate_proficiency_bonus(4) == 2
    assert engine.calculate_proficiency_bonus(5) == 3
    assert engine.calculate_proficiency_bonus(8) == 3
    assert engine.calculate_proficiency_bonus(9) == 4
    assert engine.calculate_proficiency_bonus(17) == 6
    assert engine.calculate_proficiency_bonus(20) == 6


def test_skill_modifier_adds_proficiency_only_when_proficient():
    engine = _engine()
    assert engine.calculate_skill_modifier(ability_modifier=3, is_proficient=True, character_level=5) == 6
    assert engine.calculate_skill_modifier(ability_modifier=3, is_proficient=False, character_level=5) == 3


def test_get_skill_ability_is_case_insensitive():
    assert _engine().get_skill_ability("Athletics") == "strength"
    assert _engine().get_skill_ability("unknown") is None


# ── hit dice ──────────────────────────────────────────────────────────────

def test_get_hit_die_is_case_insensitive_and_matches_class():
    engine = _engine()
    assert engine.get_hit_die("Wizard") == 6
    assert engine.get_hit_die("barbarian") == 12
    assert engine.get_hit_die("not-a-class") is None


# ── advantage / disadvantage conditions ─────────────────────────────────

def test_advantage_conditions_are_exactly_the_documented_set():
    engine = _engine()
    for condition in ("prone", "invisible", "restrained"):
        assert engine.is_advantage_condition(condition) is True
    assert engine.is_advantage_condition("PRONE") is True
    for condition in ("blinded", "charmed", "stunned", "grappled"):
        assert engine.is_advantage_condition(condition) is False


def test_disadvantage_conditions_are_exactly_the_documented_set():
    engine = _engine()
    for condition in (
        "blinded", "charmed", "frightened", "paralyzed", "petrified",
        "poisoned", "prone", "restrained", "stunned", "unconscious",
    ):
        assert engine.is_disadvantage_condition(condition) is True
    assert engine.is_disadvantage_condition("INVISIBLE") is False
    assert engine.is_disadvantage_condition("deafened") is False


# ── reference summary ────────────────────────────────────────────────────

def test_reference_summary_reports_real_counts():
    engine = _engine()
    summary = engine.reference_summary()
    assert summary["spells"] == len(engine.spells)
    assert summary["conditions"] == len(engine.conditions)
    assert summary["skills"] == len(engine.skill_abilities)
    assert summary["classes"] == len(engine.class_hit_dice)
    assert summary["abilities"] == list(engine.ability_scores.keys())
    assert summary["spells"] > 0 and summary["classes"] > 0
