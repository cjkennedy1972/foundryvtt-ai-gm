"""CombatMechanics bookkeeping/edge cases and the tactical snapshot renderer's less-travelled branches."""

from unittest.mock import AsyncMock

import pytest

from combat.mechanics import CombatMechanics, TacticalAnalysis
from combat.tactics import (
    blocking_segments, build_tactical_snapshot, fetch_scene_state, flanking_check, render_snapshot,
)

GRID = 64


def _tok(id, name, gx, gy, disposition, **kw):
    return {"id": id, "name": name, "x": gx * GRID, "y": gy * GRID, "width": 1, "height": 1,
            "disposition": disposition, **kw}


# ── mechanics ───────────────────────────────────────────────────────────────

def test_update_position_mutates_in_place_and_set_cover_ignores_unknown():
    m = CombatMechanics()
    m.update_position("a", 1, 1)
    first = m.positions["a"]
    m.update_position("a", 4, 5, size=10.0, is_prone=True)
    assert m.positions["a"] is first
    assert (first.x, first.y, first.size, first.is_prone) == (4, 5, 10.0, True)
    m.set_cover("ghost", True, "half")  # no such combatant: no crash, nothing created
    assert "ghost" not in m.positions
    m.set_cover("a", True, "half")
    assert m.get_cover_ac_bonus("a") == 2
    m.set_cover("a", False)
    assert m.get_cover_ac_bonus("a") == 0


def test_distance_is_chebyshev_feet_and_none_for_unknown():
    m = CombatMechanics()
    m.update_position("a", 0, 0)
    m.update_position("b", 3, 1)
    assert m.get_distance("a", "b") == 15
    assert m.get_distance("a", "nobody") is None
    assert m.is_within_reach("a", "nobody") is False
    assert m.is_within_reach("a", "b", weapon_reach=15.0) is True
    assert m.is_within_reach("a", "b") is False


def test_cover_ac_bonus_values():
    m = CombatMechanics()
    m.update_position("a", 0, 0)
    assert m.get_cover_ac_bonus("nobody") == 0
    for kind, bonus in [("half", 2), ("three_quarter", 5), ("full", float("inf")), ("weird", 0), (None, 0)]:
        m.set_cover("a", True, kind)
        assert m.get_cover_ac_bonus("a") == bonus


def test_reach_by_size_and_weapon():
    m = CombatMechanics()
    m.update_position("med", 0, 0)
    m.update_position("big", 1, 0, size=10.0)
    assert m.get_reach("nobody") == 5.0
    assert m.get_reach("med") == 5.0
    assert m.get_reach("med", "pike") == 10.0
    assert m.get_reach("big") == 10.0


def test_cover_unavailable_for_unknown_or_stacked_creatures():
    m = CombatMechanics()
    m.update_position("a", 0, 0)
    m.update_position("same", 0, 0)
    assert m.get_available_cover("a", "nobody") is None
    assert m.get_available_cover("nobody", "a") is None
    assert m.get_available_cover("a", "same") is None  # no sight line to take cover from


def test_cover_classes_by_angle():
    m = CombatMechanics()
    m.update_position("a", 0, 0)
    m.update_position("diag", 1, 1)      # 45deg: most perpendicular => three_quarter
    m.update_position("axis", 1, 0)      # along an axis: no cover
    m.update_position("shallow", 1.5, 0.26)   # ~10deg off axis: half
    assert m.get_available_cover("a", "diag") == "three_quarter"
    assert m.get_available_cover("a", "axis") is None
    assert m.get_available_cover("a", "shallow") == "half"
    m.update_position("ten_ft", 2, 2)    # exactly 10 ft away is already "too far apart"
    assert m.get_available_cover("a", "ten_ft") is None


def test_flanking_needs_adjacent_opposite_ally_and_valid_ids():
    m = CombatMechanics()
    m.update_position("me", 0, 1)
    m.update_position("tgt", 1, 1)
    m.update_position("opp", 2, 1)      # directly opposite
    m.update_position("side", 1, 0)     # 90deg: beside the target, not across it
    m.update_position("same_side", 0, 2)
    m.update_position("far", 5, 1)
    assert m.is_flanking("me", "tgt", ["opp"]) is True
    assert m.is_flanking("me", "tgt", ["side"]) is False             # 90deg is outside the 120-240 cone
    assert m.is_flanking("me", "ghost", ["opp"]) is False
    assert m.is_flanking("ghost", "tgt", ["opp"]) is False
    assert m.is_flanking("me", "tgt", ["far"]) is False              # ally not adjacent to target
    assert m.is_flanking("me", "tgt", ["ghost"]) is False            # unknown ally ignored
    assert m.is_flanking("me", "tgt", ["me", "tgt"]) is False        # self / target never count
    assert m.is_flanking("far", "tgt", ["opp"]) is False             # attacker itself not adjacent
    assert m.is_flanking("me", "tgt", ["same_side"]) is False        # 45deg apart


def test_flanking_angle_wraparound():
    """Attacker at +350deg and ally at +10deg from the target are 20deg apart, not 340."""
    m = CombatMechanics()
    m.update_position("t", 0, 0)
    m.update_position("a", 1, 0)    # 0deg
    m.update_position("b", 1, 1)    # 45deg
    assert m.is_flanking("a", "t", ["b"]) is False
    m.update_position("c", 1, -1)   # 315deg: 45deg from a via wraparound
    assert m.is_flanking("a", "t", ["c"]) is False


def test_opportunity_attacks_include_same_square_and_skip_unknown():
    m = CombatMechanics()
    m.update_position("me", 0, 0)
    m.update_position("same", 0, 0)
    m.update_position("adj", 1, 1)
    m.update_position("far", 3, 0)
    assert m.can_opportunity_attack("ghost", ["adj"]) == []
    assert m.can_opportunity_attack("me", ["same", "adj", "far", "ghost"]) == ["same", "adj"]


def test_tactical_analysis_aggregates_and_recommends():
    m = CombatMechanics()
    m.update_position("me", 0, 1)
    m.update_position("g1", 1, 1)
    m.update_position("ally", 2, 1)   # flanks g1 with me
    a = m.get_tactical_analysis("me", ["g1"], ["ally"])
    assert a.flanking_enemies == ["g1"]
    assert a.enemies_in_range == ["g1"]
    assert a.opportunity_attack_threats == ["g1"]
    recs = a.get_recommendations()
    assert any("flanking 1 enemy" in r for r in recs)
    assert any("provoke opportunity attacks" in r for r in recs)
    assert not any("No enemies within melee range" in r for r in recs)


def test_recommendations_for_each_cover_class_and_isolation():
    base = dict(actor_id="a", flanking_allies=[], flanking_enemies=[], opportunity_attack_threats=[])
    r = TacticalAnalysis(enemies_in_range=[], available_cover="three_quarter", **base).get_recommendations()
    assert r == ["Take three-quarter cover (+5 AC)", "No enemies within melee range - use ranged attacks or move to engage"]
    r = TacticalAnalysis(enemies_in_range=["x"], available_cover="half", **base).get_recommendations()
    assert r == ["Take half cover (+2 AC)"]
    r = TacticalAnalysis(actor_id="a", flanking_allies=["f"], flanking_enemies=[], enemies_in_range=["x"],
                         available_cover=None, opportunity_attack_threats=[]).get_recommendations()
    assert r == ["1 ally/ies are flanking enemies with you"]


def test_analysis_with_no_hostiles_has_no_cover():
    m = CombatMechanics()
    m.update_position("me", 0, 0)
    a = m.get_tactical_analysis("me", [], [])
    assert a.available_cover is None and a.enemies_in_range == []


# ── tactics ─────────────────────────────────────────────────────────────────

def test_blocking_segments_drop_malformed_and_open_doors():
    walls = [{"c": [0, 0, 10, 10]}, {"c": [1, 2, 3]}, {"c": None}, {"c": [0, 0, 5, 5], "door": 1, "ds": 1},
             {"c": [0, 0, 5, 5], "door": 1, "ds": 0}, {"c": [0, 0, 5, 5], "door": 1, "ds": 2}]
    assert blocking_segments(walls) == [(0, 0, 10, 10), (0, 0, 5, 5), (0, 0, 5, 5)]
    assert blocking_segments(None) == []


def test_snapshot_reports_elevation_and_heavy_cover():
    me = _tok("me", "Rogue", 0, 0, 1)
    up = _tok("up", "Archer", 4, 0, -1, elevation=10)
    down = _tok("down", "Pit Thing", 0, 4, -1, elevation=-5)
    walls = [{"c": [2 * GRID, -GRID, 2 * GRID, GRID]}, {"c": [2.5 * GRID, -GRID, 2.5 * GRID, GRID]}]
    out = render_snapshot("me", {"grid": GRID, "tokens": [me, up, down], "walls": walls})
    assert "Archer: 20 ft, 10 ft above you, heavy cover from you (+5 AC" in out
    assert "Pit Thing: 20 ft, 5 ft below you" in out


def test_snapshot_empty_for_unusable_inputs():
    me = _tok("me", "Rogue", 0, 0, 1)
    assert render_snapshot("me", None) == ""
    assert render_snapshot("me", {"grid": GRID, "tokens": [me]}) == ""              # no enemies
    assert render_snapshot("nobody", {"grid": GRID, "tokens": [me, _tok("e", "E", 1, 0, -1)]}) == ""
    assert flanking_check("me", "e", None) is None


def test_hidden_enemy_is_not_leaked_but_hidden_self_resolves():
    me = _tok("me", "Rogue", 0, 0, 1, hidden=True)
    secret = _tok("e", "Ambusher", 1, 0, -1, hidden=True)
    assert render_snapshot("me", {"grid": GRID, "tokens": [me, secret]}) == ""
    seen = _tok("e2", "Goblin", 1, 0, -1)
    assert "Goblin" in render_snapshot("me", {"grid": GRID, "tokens": [me, secret, seen]})
    assert "Ambusher" not in render_snapshot("me", {"grid": GRID, "tokens": [me, secret, seen]})


@pytest.mark.asyncio
async def test_fetch_scene_state_and_snapshot_degrade_gracefully():
    foundry = AsyncMock()
    foundry.execute_js.side_effect = RuntimeError("relay")
    assert await fetch_scene_state(foundry) is None
    assert await build_tactical_snapshot(foundry, "me") == ""
    foundry.execute_js.side_effect = None
    foundry.execute_js.return_value = "not a dict"
    assert await fetch_scene_state(foundry) is None
    me, e = _tok("me", "Rogue", 0, 0, 1), _tok("e", "Goblin", 1, 0, -1)
    foundry.execute_js.return_value = {"result": {"grid": GRID, "tokens": [me, e]}}
    assert "Goblin: 5 ft" in await build_tactical_snapshot(foundry, "me")


def test_a_hostile_in_the_same_square_is_the_nearest_not_the_farthest():
    from combat.mechanics import CombatMechanics, _dist_or_far

    assert _dist_or_far(0) < _dist_or_far(5) < _dist_or_far(None)
    m = CombatMechanics()
    m.update_position("me", 0, 0)
    m.update_position("stacked", 0, 0)
    m.update_position("far", 500, 0)
    ids = ["far", "stacked"]
    assert min(ids, key=lambda e: _dist_or_far(m.get_distance("me", e))) == "stacked"


def _polar_ally(deg):
    """An ally one square from the target at `deg` degrees; the attacker stands at 0 degrees."""
    import math
    m = CombatMechanics()
    m.update_position("tgt", 10, 10)
    m.update_position("me", 11, 10)
    m.update_position("ally", 10 + math.cos(math.radians(deg)), 10 + math.sin(math.radians(deg)))
    return m


@pytest.mark.parametrize("deg,flanks", [
    (180, True),                    # directly opposite
    (150, True), (210, True),       # a little off, either side
    (125, True), (235, True),       # inside the 120-240 cone, mirror images of each other
    (115, False), (245, False),     # just outside it
    (90, False), (270, False),      # beside the target is not flanking
    (45, False), (0, False),
])
def test_flanking_needs_an_ally_roughly_directly_opposite(deg, flanks):
    assert _polar_ally(deg).is_flanking("me", "tgt", ["ally"]) is flanks


def test_the_flanking_cone_edges_are_120_and_240_inclusive():
    m = _polar_ally(180)
    for deg, flanks in ((120.0, True), (119.9, False), (240.0, True), (240.1, False)):
        m._get_angle = lambda frm, to, _d=deg: 0.0 if to is m.positions["me"] else _d
        assert m.is_flanking("me", "tgt", ["ally"]) is flanks, deg
