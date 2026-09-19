#!/usr/bin/env python3
"""CombatMechanics grid geometry, against the 5e grid rules.

Positions are stored in grid squares (combat/tactics.py divides pixel centres
by the grid size) and get_distance multiplies by 5 to get feet. It measured
Euclidean distance, so a diagonally adjacent creature came out at 7.07 feet
rather than 5.

On a grid, 5e counts every square you move through as 5 feet, diagonals
included (PHB, "Variant: Playing on a Grid"; the 5-then-10 alternation is the
DMG optional variant). Chebyshev distance, not Euclidean.

Three predicates compare that distance against a 5-foot threshold, so all
three were wrong for exactly the creatures standing corner to corner:
is_within_reach, is_flanking and can_opportunity_attack. Fixing the shared
measurement fixes all three.

The rendered distance in combat/tactics.py was unaffected: it rounds to the
nearest 5, and 7.07 already rounded to 5. Only the booleans broke.

Run:
    cd ai-engine && python -m pytest tests/test_combat_geometry.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.mechanics import CombatMechanics, TacticalAnalysis


def _mech(**placements):
    """Place actors by grid square: _mech(hero=(0, 0), goblin=(1, 1))."""
    mech = CombatMechanics()
    for actor_id, spec in placements.items():
        x, y = spec[0], spec[1]
        size = spec[2] if len(spec) > 2 else 5.0
        mech.update_position(actor_id, x, y, size=size)
    return mech


# ── distance on a grid ────────────────────────────────────────────────────

def test_an_orthogonally_adjacent_square_is_five_feet():
    assert _mech(a=(0, 0), b=(1, 0)).get_distance("a", "b") == 5.0


def test_a_diagonally_adjacent_square_is_also_five_feet():
    """The Euclidean measurement called this 7.07."""
    assert _mech(a=(0, 0), b=(1, 1)).get_distance("a", "b") == 5.0


def test_a_knights_move_is_two_squares():
    """Chebyshev takes the longer axis: max(2, 1) squares."""
    assert _mech(a=(0, 0), b=(2, 1)).get_distance("a", "b") == 10.0


def test_the_same_square_is_no_distance():
    assert _mech(a=(3, 3), b=(3, 3)).get_distance("a", "b") == 0.0


def test_distance_is_symmetric():
    mech = _mech(a=(0, 0), b=(4, 7))
    assert mech.get_distance("a", "b") == mech.get_distance("b", "a")


def test_an_unknown_actor_has_no_distance():
    assert _mech(a=(0, 0)).get_distance("a", "nobody") is None


# ── reach ─────────────────────────────────────────────────────────────────

def test_a_diagonally_adjacent_enemy_is_within_melee_reach():
    assert _mech(hero=(0, 0), goblin=(1, 1)).is_within_reach("hero", "goblin") is True


def test_two_squares_away_is_out_of_a_five_foot_reach():
    assert _mech(hero=(0, 0), goblin=(2, 0)).is_within_reach("hero", "goblin") is False


def test_a_reach_weapon_covers_ten_feet():
    mech = _mech(hero=(0, 0), goblin=(2, 0))
    assert mech.is_within_reach("hero", "goblin", weapon_reach=10.0) is True


def test_a_large_creature_reaches_ten_feet():
    assert _mech(ogre=(0, 0, 10.0)).get_reach("ogre") == 10.0


def test_a_polearm_reaches_ten_feet():
    assert _mech(hero=(0, 0)).get_reach("hero", weapon="polearm") == 10.0


def test_an_unplaced_actor_falls_back_to_five_feet():
    assert CombatMechanics().get_reach("ghost") == 5.0


# ── flanking ──────────────────────────────────────────────────────────────

def test_two_allies_on_opposite_sides_are_flanking():
    mech = _mech(hero=(0, 1), rogue=(2, 1), goblin=(1, 1))
    assert mech.is_flanking("hero", "goblin", ["rogue"]) is True


def test_flanking_works_across_a_diagonal():
    """Corner to corner through the target: adjacent by the grid rules, and
    the pair the Euclidean measurement excluded."""
    mech = _mech(hero=(0, 0), rogue=(2, 2), goblin=(1, 1))
    assert mech.is_flanking("hero", "goblin", ["rogue"]) is True


def test_allies_on_the_same_side_are_not_flanking():
    mech = _mech(hero=(0, 1), rogue=(0, 2), goblin=(1, 1))
    assert mech.is_flanking("hero", "goblin", ["rogue"]) is False


def test_an_ally_too_far_from_the_target_does_not_flank():
    mech = _mech(hero=(0, 1), rogue=(6, 1), goblin=(1, 1))
    assert mech.is_flanking("hero", "goblin", ["rogue"]) is False


def test_an_attacker_out_of_reach_is_not_flanking():
    mech = _mech(hero=(5, 1), rogue=(2, 1), goblin=(1, 1))
    assert mech.is_flanking("hero", "goblin", ["rogue"]) is False


def test_flanking_needs_an_ally():
    mech = _mech(hero=(0, 1), goblin=(1, 1))
    assert mech.is_flanking("hero", "goblin", []) is False


def test_the_target_is_not_its_own_flanking_ally():
    mech = _mech(hero=(0, 1), goblin=(1, 1))
    assert mech.is_flanking("hero", "goblin", ["goblin", "hero"]) is False


# ── opportunity attacks ───────────────────────────────────────────────────

def test_a_diagonally_adjacent_enemy_threatens_an_opportunity_attack():
    mech = _mech(runner=(0, 0), goblin=(1, 1))
    assert mech.can_opportunity_attack("runner", ["goblin"]) == ["goblin"]


def test_an_enemy_sharing_the_square_still_threatens():
    """`if distance and distance <= 5` dropped a distance of exactly zero."""
    mech = _mech(runner=(2, 2), swarm=(2, 2))
    assert mech.can_opportunity_attack("runner", ["swarm"]) == ["swarm"]


def test_distant_enemies_do_not_threaten():
    mech = _mech(runner=(0, 0), archer=(8, 8))
    assert mech.can_opportunity_attack("runner", ["archer"]) == []


def test_an_unplaced_defender_threatens_nobody():
    assert CombatMechanics().can_opportunity_attack("ghost", ["goblin"]) == []


# ── cover ─────────────────────────────────────────────────────────────────

def test_cover_grants_the_ac_bonus_the_rules_give_it():
    mech = _mech(hero=(0, 0))
    mech.set_cover("hero", True, "half")
    assert mech.get_cover_ac_bonus("hero") == 2
    mech.set_cover("hero", True, "three_quarter")
    assert mech.get_cover_ac_bonus("hero") == 5


def test_no_cover_is_no_bonus():
    mech = _mech(hero=(0, 0))
    assert mech.get_cover_ac_bonus("hero") == 0


def test_full_cover_cannot_be_targeted():
    mech = _mech(hero=(0, 0))
    mech.set_cover("hero", True, "full")
    assert mech.get_cover_ac_bonus("hero") == float("inf")


def test_cover_is_not_offered_across_the_room():
    mech = _mech(hero=(0, 0), archer=(9, 0))
    assert mech.get_available_cover("hero", "archer") is None


# ── the analysis the GM reads ─────────────────────────────────────────────

def test_the_analysis_reports_enemies_in_reach():
    mech = _mech(hero=(0, 0), goblin=(1, 1), archer=(9, 9))
    analysis = mech.get_tactical_analysis("hero", ["goblin", "archer"], [])

    assert "goblin" in analysis.enemies_in_range
    assert "archer" not in analysis.enemies_in_range


def test_the_recommendations_say_something_useful_when_flanking():
    analysis = TacticalAnalysis(
        actor_id="hero", flanking_allies=[], flanking_enemies=["goblin"],
        enemies_in_range=["goblin"], available_cover=None,
        opportunity_attack_threats=[],
    )
    assert any("advantage" in r for r in analysis.get_recommendations())


def test_the_recommendations_tell_a_ranged_actor_to_close_or_shoot():
    analysis = TacticalAnalysis(
        actor_id="hero", flanking_allies=[], flanking_enemies=[],
        enemies_in_range=[], available_cover=None, opportunity_attack_threats=[],
    )
    assert any("ranged" in r for r in analysis.get_recommendations())
