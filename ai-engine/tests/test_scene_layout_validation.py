#!/usr/bin/env python3
"""Every hand-authored scene layout was rejected, and the fallback was dead code.

The campaign-generation prompt tells the model to draw walls in grid squares
and to "draw perimeter first". Its canonical example is a 20x15 room whose
perimeter is [[0,0,20,0],[20,0,20,15],[20,15,0,15],[0,15,0,0]] — wall segments
run along grid VERTICES, so the far edge of a 20-wide room is at x=20.

validate_scene_setup rejected coordinates at `>= grid_w`, which is the far
edge. Running the prompt's own example through it:

    the prompt's own example validates: False
      - Walls out of bounds: max (20,0)
      - Walls out of bounds: max (20,15)
      - Walls out of bounds: max (20,15)
      - Walls out of bounds: max (0,15)

All four perimeter walls. The procedural generator passes only because it
works in cell indices and stops at 19, so the two producers disagreed about
the coordinate system and the validator implemented one of them.

The second half: on failure _generate_scene_maps logs "activating procedural
fallback" and sets needs_fallback, and the block that would run the fallback
is guarded by `if (walls or doors) and not needs_fallback`. The
`if needs_fallback:` branch inside it could never be reached. So the message
named a fallback that never ran, and the scene fell through to plain
text-to-image with no layout guidance — the model's walls, doors and secret
doors shaped nothing about the map image.

Run:
    cd ai-engine && python -m pytest tests/test_scene_layout_validation.py -v
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.layout_generator import generate_layout, validate_scene_setup
from campaign.orchestrator import CampaignOrchestrator

# Verbatim from the scene_setup example in campaign/generator.py's prompt.
PROMPT_EXAMPLE = {
    "grid_width": 20,
    "grid_height": 15,
    "grid_size_px": 64,
    "walls": [
        [0, 0, 20, 0], [20, 0, 20, 15], [20, 15, 0, 15], [0, 15, 0, 0],
        [8, 0, 8, 6], [8, 6, 14, 6], [14, 6, 14, 0],
    ],
    "doors": [
        {"c": [4, 0, 8, 0], "door": 1, "ds": 0},
        {"c": [16, 6, 18, 6], "door": 2, "ds": 2},
    ],
    "_scene_type": "dungeon",
}


# ── the validator agrees with the prompt ──────────────────────────────────

def test_the_example_the_model_is_given_passes_validation():
    ok, warnings = validate_scene_setup(dict(PROMPT_EXAMPLE))

    assert ok, f"the prompt's own example is rejected: {warnings}"


def test_a_perimeter_wall_sits_on_the_far_edge_not_one_square_short():
    setup = {"grid_width": 16, "grid_height": 12, "_scene_type": "dungeon",
             "walls": [[0, 0, 16, 0], [16, 0, 16, 12], [16, 12, 0, 12], [0, 12, 0, 0]],
             "doors": [{"c": [4, 0, 8, 0], "door": 1, "ds": 0}]}

    ok, warnings = validate_scene_setup(setup)

    assert ok, warnings


def test_a_wall_genuinely_past_the_edge_is_still_caught():
    setup = {"grid_width": 16, "grid_height": 12, "_scene_type": "dungeon",
             "walls": [[0, 0, 17, 0]]}

    ok, warnings = validate_scene_setup(setup)

    assert not ok
    assert any("out of bounds" in w for w in warnings)


def test_a_dict_format_wall_past_the_edge_is_still_caught():
    setup = {"grid_width": 16, "grid_height": 12, "_scene_type": "dungeon",
             "walls": [{"type": "h", "x": 17, "y": 0}]}

    ok, warnings = validate_scene_setup(setup)

    assert not ok


def test_a_dict_format_wall_on_the_far_edge_is_accepted():
    setup = {"grid_width": 16, "grid_height": 12, "_scene_type": "dungeon",
             "walls": [{"type": "h", "x": 16, "y": 0}, {"type": "v", "x": 0, "y": 12}],
             "doors": [{"c": [4, 0, 8, 0], "door": 1, "ds": 0}]}

    ok, warnings = validate_scene_setup(setup)

    assert ok, warnings


def test_the_procedural_generator_still_validates():
    """It works in cell indices and stops short of the edge, which is why it
    passed all along. Loosening the bound must not break it."""
    setup = generate_layout(
        scene_type="dungeon", grid_width=20, grid_height=15, seed=7, method="bsp"
    ).to_scene_setup(20, 15)

    ok, warnings = validate_scene_setup({**setup, "_scene_type": "dungeon"})

    assert ok, warnings


# ── the fallback is reachable ─────────────────────────────────────────────

def _run_scene_maps(setup, tmp_path):
    """Drive _generate_scene_maps far enough to see which layout call is made."""
    scene = {"name": "The Sunken Chapel", "type": "dungeon", "scene_setup": setup}

    # generate_map and generate_map_controlnet are typed -> Dict[str, Any] and
    # always return one; the caller subscripts ["status"] unguarded.
    no_provider = {"status": "error", "error": "no ComfyUI in tests", "provider": "none"}
    map_generator = MagicMock()
    map_generator.generate_layout_mask = AsyncMock(return_value=None)
    map_generator.fallback_layout_for_scene = AsyncMock(return_value=None)
    map_generator.generate_map = AsyncMock(return_value=no_provider)
    map_generator.generate_map_controlnet = AsyncMock(return_value=no_provider)

    orch = CampaignOrchestrator()
    asyncio.run(orch._generate_scene_maps(
        [scene], map_generator, Path(tmp_path), {"maps": [], "errors": []}
    ))
    return map_generator


def test_a_valid_layout_is_used_to_guide_the_map(tmp_path):
    gen = _run_scene_maps(dict(PROMPT_EXAMPLE), tmp_path)

    gen.generate_layout_mask.assert_awaited_once()
    gen.fallback_layout_for_scene.assert_not_awaited()


def test_an_invalid_layout_actually_reaches_the_fallback(tmp_path):
    """The log said "activating procedural fallback" and then skipped the
    block the fallback lives in."""
    broken = {**PROMPT_EXAMPLE, "walls": [[0, 0, 99, 0], [99, 0, 99, 99]]}

    gen = _run_scene_maps(broken, tmp_path)

    gen.fallback_layout_for_scene.assert_awaited_once()
    gen.generate_layout_mask.assert_not_awaited()
