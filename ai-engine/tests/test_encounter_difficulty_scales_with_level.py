#!/usr/bin/env python3
"""DynamicDifficulty.calculate_difficulty ignored party level entirely.

encounter_budget was keyed by PLAYER COUNT while holding what the DMG
publishes per CHARACTER LEVEL, and calculate_difficulty computed
`level = int(party.avg_level)` and then never used it. So four wolves rated
the same against four level-1 characters as against four level-20s.

That is wrong against the module's own stated contract — the
EncounterDifficulty enum documents each band as "XP threshold =
avg_party_level * N".

It matters because the AI GM sizes encounters with this: at low level it
under-rates lethal fights, and at high level it calls routine ones deadly.
Four wolves (200 XP) is at the DMG deadly threshold for four level-1
characters and trivial for four level-20s; both came back "easy".

suggest_encounters(TRIVIAL) also raised KeyError: the budget rows carry
easy/medium/hard/deadly and the enum has a fifth member, which
/api/combat/encounter-suggestions passes straight through.

Run:
    cd ai-engine && python -m pytest tests/test_encounter_difficulty_scales_with_level.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.difficulty import (
    DynamicDifficulty,
    EncounterDifficulty,
    EncounterProfile,
    PartyComposition,
)

ORDER = [
    EncounterDifficulty.TRIVIAL,
    EncounterDifficulty.EASY,
    EncounterDifficulty.MEDIUM,
    EncounterDifficulty.HARD,
    EncounterDifficulty.DEADLY,
]


def _party(level, players=4):
    return PartyComposition(num_players=players, avg_level=level)


def _rate(encounter, level, players=4):
    return DynamicDifficulty().calculate_difficulty(encounter, _party(level, players))


WOLVES = EncounterProfile(monster_names=["Wolf"] * 4, monster_crs=[0.25] * 4)      # 200 XP
DRAGON = EncounterProfile(monster_names=["Ancient Red Dragon"], monster_crs=[20])  # 25000 XP


def test_the_same_monsters_get_harder_for_a_lower_level_party():
    assert ORDER.index(_rate(WOLVES, 1)) > ORDER.index(_rate(WOLVES, 20))


def test_four_wolves_are_not_easy_for_four_level_one_characters():
    """200 XP is at the DMG deadly threshold for a level-1 party of four."""
    assert _rate(WOLVES, 1) in (EncounterDifficulty.HARD, EncounterDifficulty.DEADLY)


def test_four_wolves_are_beneath_notice_at_level_twenty():
    assert _rate(WOLVES, 20) == EncounterDifficulty.TRIVIAL


def test_an_ancient_dragon_is_not_rated_the_same_at_level_1_and_level_20():
    assert _rate(DRAGON, 1) != _rate(DRAGON, 20)


def test_a_bigger_party_of_the_same_level_finds_the_same_fight_no_harder():
    """Thresholds are summed per character, so more characters absorb more."""
    six = ORDER.index(_rate(WOLVES, 5, players=6))
    three = ORDER.index(_rate(WOLVES, 5, players=3))
    assert six <= three


@pytest.mark.parametrize("difficulty", ORDER)
def test_every_difficulty_band_can_be_asked_for_suggestions(difficulty):
    """/api/combat/encounter-suggestions passes the enum straight through, so
    a missing budget key is a 500."""
    suggestions = DynamicDifficulty().suggest_encounters(_party(5), difficulty)

    assert suggestions, f"no suggestions for {difficulty.value}"
    assert all(s.get("xp") for s in suggestions)


def test_level_still_drives_the_suggested_challenge_rating():
    low = DynamicDifficulty().suggest_encounters(_party(2), EncounterDifficulty.MEDIUM)
    high = DynamicDifficulty().suggest_encounters(_party(15), EncounterDifficulty.MEDIUM)

    assert high[0]["suggested_cr"] > low[0]["suggested_cr"]
    assert high[0]["xp"] > low[0]["xp"]


# ── the band ladder ───────────────────────────────────────────────────────

def test_an_encounter_sitting_on_the_deadly_threshold_is_deadly():
    """The old ladder tested `<=` upward, so exactly-deadly read HARD and
    every band came back one softer than the DMG defines it."""
    party = _party(5)                     # 4 x level 5 -> deadly 4400
    budget = DynamicDifficulty()._party_budget(party)
    on_the_line = EncounterProfile(monster_names=["X"], monster_crs=[])
    on_the_line.total_xp = budget["deadly"]        # single monster, multiplier 1.0

    assert DynamicDifficulty().calculate_difficulty(on_the_line, party) == EncounterDifficulty.DEADLY


def test_an_encounter_just_under_a_threshold_stays_in_the_band_below():
    party = _party(5)
    budget = DynamicDifficulty()._party_budget(party)
    under = EncounterProfile(monster_names=["X"], monster_crs=[])
    under.total_xp = budget["deadly"] - 1

    assert DynamicDifficulty().calculate_difficulty(under, party) == EncounterDifficulty.HARD


# ── the monster-count multiplier ──────────────────────────────────────────

def test_the_same_xp_split_across_more_monsters_is_harder():
    """Two CR-2 monsters get two turns; the raw XP sum says otherwise."""
    one_big = EncounterProfile(monster_names=["Ogre"], monster_crs=[5])          # 1800
    four_small = EncounterProfile(monster_names=["Thug"] * 4, monster_crs=[2] * 4)  # 1800
    assert one_big.total_xp == four_small.total_xp

    party = _party(5)
    engine = DynamicDifficulty()
    assert ORDER.index(engine.calculate_difficulty(four_small, party)) > \
           ORDER.index(engine.calculate_difficulty(one_big, party))


def test_the_multiplier_follows_the_dmg_bands():
    m = DynamicDifficulty.encounter_multiplier
    assert [m(n) for n in (1, 2, 3, 6, 7, 10, 11, 14, 15, 30)] == \
           [1.0, 1.5, 2.0, 2.0, 2.5, 2.5, 3.0, 3.0, 4.0, 4.0]


def test_a_lone_monster_is_not_inflated():
    assert DynamicDifficulty.encounter_multiplier(1) == 1.0
