#!/usr/bin/env python3
"""midi_qol._parse_damage_formula turns an LLM damage string into the
dnd5e 5.x damage parts a spell or weapon activity is built from.

campaign/modules/midi_qol.py is one of 33 modules no test referenced. The
parser is pure and checkable, which is where the encounter-difficulty (#179)
and grid-distance (#183) bugs were, and it had three defects:

  "d8" and "d20" — standard notation with an implied leading 1 — did not
  match, so they fell back to the 1d6 default. A d20 attack became a d6.

  "2d6-1" dropped the penalty silently.

  "2d6+1d8" became 2d6+1. Not a fallback: the second die term was read as a
  flat +1, so 4.5 average damage turned into 1 and nothing looked wrong.

The return type is already a list, and build_attack_activity/
build_save_activity take damage_parts, so several terms need no new shape.

Run:
    cd ai-engine && python -m pytest tests/test_damage_formula_parsing.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.modules.midi_qol import _parse_damage_formula


def _terms(formula, damage_type="slashing"):
    """(number, denomination, bonus) per part, for readable assertions."""
    return [(p["number"], p["denomination"], p["bonus"]) for p in _parse_damage_formula(formula, damage_type)]


# ── what already worked ───────────────────────────────────────────────────

@pytest.mark.parametrize("formula,expected", [
    ("1d10", [(1, 10, "")]),
    ("2d6+3", [(2, 6, "3")]),
    ("1d8 + 4", [(1, 8, "4")]),
    ("3d12", [(3, 12, "")]),
])
def test_ordinary_formulas(formula, expected):
    assert _terms(formula) == expected


# ── an implied leading 1 ──────────────────────────────────────────────────

@pytest.mark.parametrize("formula,expected", [
    ("d8", [(1, 8, "")]),
    ("d20", [(1, 20, "")]),
    ("d4 + 2", [(1, 4, "2")]),
])
def test_a_die_written_without_its_count(formula, expected):
    """Standard notation. These fell back to 1d6, so a d20 became a d6."""
    assert _terms(formula) == expected


# ── penalties ─────────────────────────────────────────────────────────────

def test_a_negative_bonus_is_kept():
    assert _terms("2d6-1") == [(2, 6, "-1")]


def test_a_spaced_negative_bonus_is_kept():
    assert _terms("1d8 - 2") == [(1, 8, "-2")]


# ── several dice ──────────────────────────────────────────────────────────

def test_a_second_die_is_not_read_as_a_flat_bonus():
    """"2d6+1d8" became 2d6+1 — 4.5 average damage turned into 1."""
    assert _terms("2d6+1d8") == [(2, 6, ""), (1, 8, "")]


def test_three_terms_all_survive():
    assert _terms("1d6+1d8+2d4") == [(1, 6, ""), (1, 8, ""), (2, 4, "")]


def test_a_bonus_after_several_dice_lands_on_the_last_term():
    assert _terms("1d6+1d8+3") == [(1, 6, ""), (1, 8, "3")]


# ── the fallback, which is still needed ───────────────────────────────────

@pytest.mark.parametrize("formula", ["", None, "a lot", "1d6 + PB"])
def test_something_unparseable_still_yields_a_usable_part(formula):
    """The activity builders need a damage part; they cannot take nothing."""
    parts = _parse_damage_formula(formula, "fire")

    assert len(parts) >= 1
    assert parts[0]["number"] >= 1 and parts[0]["denomination"] >= 1


def test_a_flat_number_is_not_silently_a_d6():
    """"3" has no die at all. Whatever it becomes, it must not claim to be
    a 1d6 roll averaging 3.5."""
    parts = _parse_damage_formula("3", "fire")

    assert len(parts) == 1
    number, denom, bonus = parts[0]["number"], parts[0]["denomination"], parts[0]["bonus"]
    assert bonus == "3" or (number, denom) != (1, 6)


# ── the damage type rides along ───────────────────────────────────────────

def test_the_damage_type_is_carried_on_every_part():
    for part in _parse_damage_formula("2d6+1d8", "radiant"):
        assert part["types"] == ["radiant"]


def test_a_missing_damage_type_defaults_to_force():
    assert _parse_damage_formula("1d6", "")[0]["types"] == ["force"]
