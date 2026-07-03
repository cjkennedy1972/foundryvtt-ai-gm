"""Checks for live tactical analysis: wall cover and flanking from scene state."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from combat.tactics import blocking_segments, cover_between, render_snapshot

GRID = 64


def _tok(id, name, gx, gy, disposition, hidden=False):
    """Token at grid square (gx, gy), 1x1, pixel coords like Foundry."""
    return {
        "id": id, "name": name, "x": gx * GRID, "y": gy * GRID,
        "width": 1, "height": 1, "disposition": disposition, "hidden": hidden,
    }


def _state(tokens, walls=None):
    return {"grid": GRID, "tokens": tokens, "walls": walls or []}


def test_cover_detected_when_wall_crosses_attack_line():
    # Vertical wall at x=128 between attacker (32,32) and target (224,32)
    segs = blocking_segments([{"c": [128, 0, 128, 64]}])
    assert cover_between((32, 32), (224, 32), segs) == "half"
    # Open door does not grant cover
    assert blocking_segments([{"c": [128, 0, 128, 64], "door": 1, "ds": 1}]) == []
    # No wall in the way
    assert cover_between((32, 32), (224, 32), []) is None


def test_flanking_reported_for_opposite_adjacent_ally():
    me = _tok("me", "Skeleton", 4, 4, -1)
    ally = _tok("ally", "Zombie", 6, 4, -1)      # opposite side of target
    target = _tok("pc", "Beringar", 5, 4, 1)     # sandwiched between
    out = render_snapshot("me", _state([me, ally, target]))
    assert "FLANKING" in out
    assert "Beringar: 5 ft" in out
    # And the PC's own snapshot warns about being flanked
    pc_view = render_snapshot("pc", _state([me, ally, target]))
    assert "being flanked" in pc_view


def test_hidden_enemies_and_empty_scenes_stay_quiet():
    me = _tok("me", "Beringar", 2, 2, 1)
    lurker = _tok("h1", "Assassin", 3, 2, -1, hidden=True)
    assert render_snapshot("me", _state([me, lurker])) == ""  # unrevealed GM info
    assert render_snapshot("me", _state([me])) == ""          # no enemies at all
    assert render_snapshot("missing", _state([me])) == ""     # actor not on scene
    assert render_snapshot("me", None) == ""                  # scene fetch failed
