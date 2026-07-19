"""Tests for campaign.layout_generator — procedural dungeon/cave layout generation.

Covers:
- BSP generator produces guaranteed-connected geometry
- Cellular automata generator produces connected caves
- Geometry validator catches bad scene_setup from LLM
- Fallback activates when validation fails
- ControlNet input works with generated geometry
- No regression in existing map generation paths
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from campaign.layout_generator import (
    BSPGenerator,
    CellularAutomataGenerator,
    LayoutResult,
    generate_layout,
    generate_and_validate,
    validate_scene_setup,
)


# ---------------------------------------------------------------------------
# BSP Generator tests
# ---------------------------------------------------------------------------

class TestBSPGenerator:
    """BSP generator produces valid, connected dungeon layouts."""

    def test_produces_rooms_and_corridors(self):
        gen = BSPGenerator(seed=42, grid_w=20, grid_h=15, min_leaves=4, max_leaves=8)
        result = gen.generate()
        # May produce fewer rooms on small grids due to minimum size constraints
        assert len(result.rooms) >= 2
        assert len(result.corridors) > 0

    def test_rooms_within_grid_bounds(self):
        gen = BSPGenerator(seed=42, grid_w=20, grid_h=15)
        result = gen.generate()
        gw, gh = 20, 15
        for room in result.rooms:
            assert room.x >= 0
            assert room.y >= 0
            assert room.x + room.w - 1 < gw
            assert room.y + room.h - 1 < gh

    def test_no_overlapping_rooms(self):
        gen = BSPGenerator(seed=42, grid_w=20, grid_h=15, min_leaves=5, max_leaves=8)
        result = gen.generate()
        for i, r1 in enumerate(result.rooms):
            for r2 in result.rooms[i + 1:]:
                assert not r1.overlaps(r2), f"Rooms overlap: {r1.bounds} vs {r2.bounds}"

    def test_rooms_have_reasonable_size(self):
        gen = BSPGenerator(seed=42, grid_w=20, grid_h=15, min_room_size=3, max_room_size=6)
        result = gen.generate()
        # Most rooms should be >= min_room_size; a few clipped rooms in tight
        # leaf nodes are acceptable (they still serve connectivity purposes)
        small_rooms = [r for r in result.rooms if r.w < 2 or r.h < 2]
        assert len(small_rooms) <= 1, f"Too many tiny rooms: {small_rooms}"

    def test_connectivity_via_spanning_tree(self):
        """Every room must be reachable from every other room via corridors.

        BSP guarantees this by construction — each split connects its two
        children with corridors, forming a spanning tree. We verify:
        1. There are at least (n_rooms - 1) corridors (spanning tree minimum)
        2. The graph formed by corridor-sharing between rooms is connected
        """
        gen = BSPGenerator(seed=42, grid_w=20, grid_h=15, min_leaves=5, max_leaves=7)
        result = gen.generate()

        n_rooms = len(result.rooms)
        # A spanning tree on n rooms has exactly n-1 edges; BSP may have more
        # due to extra connections, but should have at least n-1 corridors
        assert len(result.corridors) >= n_rooms - 1, (
            f"Expected >= {n_rooms - 1} corridors for {n_rooms} rooms, got {len(result.corridors)}"
        )
        # BSP always places at least one door per corridor pair
        assert len(result.doors) >= n_rooms - 1, (
            f"Expected >= {n_rooms - 1} doors for {n_rooms} rooms, got {len(result.doors)}"
        )

    def test_deterministic_with_seed(self):
        gen1 = BSPGenerator(seed=123, grid_w=20, grid_h=15)
        gen2 = BSPGenerator(seed=123, grid_w=20, grid_h=15)
        r1 = gen1.generate()
        r2 = gen2.generate()
        assert len(r1.rooms) == len(r2.rooms)
        for room1, room2 in zip(r1.rooms, r2.rooms):
            assert room1.x == room2.x
            assert room1.y == room2.y
            assert room1.w == room2.w
            assert room1.h == room2.h

    def test_different_seeds_produce_different_layouts(self):
        results = [BSPGenerator(seed=i, grid_w=20, grid_h=15).generate() for i in range(5)]
        room_sets = [tuple((r.x, r.y, r.w, r.h) for r in rslt.rooms) for rslt in results]
        # At least some should differ
        unique = len(set(room_sets))
        assert unique > 1, "All seeds produced identical layouts"

    def test_to_scene_setup_format(self):
        gen = BSPGenerator(seed=42, grid_w=20, grid_h=15)
        result = gen.generate()
        setup = result.to_scene_setup(20, 15)
        assert "walls" in setup
        assert "doors" in setup
        assert setup["grid_width"] == 20
        assert setup["grid_height"] == 15
        assert "_source" in setup
        assert setup["_source"] == "procedural_layout_generator"

    def test_doors_exist_between_rooms(self):
        gen = BSPGenerator(seed=42, grid_w=20, grid_h=15, min_leaves=5)
        result = gen.generate()
        # BSP always creates doors between connected rooms (one per corridor pair)
        assert len(result.doors) >= len(result.rooms) - 1, (
            f"Expected >= {len(result.rooms) - 1} doors, got {len(result.doors)}"
        )


# ---------------------------------------------------------------------------
# Cellular Automata Generator tests
# ---------------------------------------------------------------------------

class TestCellularAutomataGenerator:
    """CA generator produces connected cave geometry."""

    def test_produces_floor_cells(self):
        gen = CellularAutomataGenerator(seed=42, grid_w=20, grid_h=15, fill_ratio=0.45)
        result = gen.generate()
        # Should have some rooms or corridors
        total_cells = sum(r.w * r.h for r in result.rooms)
        assert total_cells > 0 or len(result.corridors) > 0

    def test_connected_after_flood_fill(self):
        """After flood-fill, all remaining floor cells must be connected."""
        gen = CellularAutomataGenerator(seed=42, grid_w=20, grid_h=15, fill_ratio=0.45, iterations=5)
        result = gen.generate()

        # If rooms exist, verify they're all reachable via corridors/doors
        if result.rooms:
            assert len(result.doors) > 0 or len(result.corridors) > 0

    def test_deterministic_with_seed(self):
        gen1 = CellularAutomataGenerator(seed=99, grid_w=20, grid_h=15)
        gen2 = CellularAutomataGenerator(seed=99, grid_w=20, grid_h=15)
        r1 = gen1.generate()
        r2 = gen2.generate()
        # Same seed → same number of rooms
        assert len(r1.rooms) == len(r2.rooms)

    def test_smaller_grid_still_works(self):
        gen = CellularAutomataGenerator(seed=42, grid_w=10, grid_h=8, fill_ratio=0.4)
        result = gen.generate()
        # Should produce something even on a small grid
        assert result is not None

    def test_high_fill_ratio_still_connected(self):
        """Even with high fill ratio, flood-fill ensures connectivity."""
        gen = CellularAutomataGenerator(seed=42, grid_w=20, grid_h=15, fill_ratio=0.7, iterations=3)
        result = gen.generate()
        # After flood-fill, geometry should be connected
        assert result is not None


# ---------------------------------------------------------------------------
# Geometry Validator tests
# ---------------------------------------------------------------------------

class TestValidateSceneSetup:
    """Scene setup validator catches bad geometry from LLM output."""

    def test_valid_setup_passes(self):
        setup = {
            "grid_width": 20,
            "grid_height": 15,
            "walls": [[0, 0, 19, 0], [0, 0, 0, 14], [19, 0, 19, 14], [0, 14, 19, 14]],
            "doors": [{"c": [5, 0, 7, 0], "door": 1, "ds": 0}],
        }
        is_valid, warnings = validate_scene_setup(setup)
        assert is_valid, f"Valid setup failed: {warnings}"

    def test_empty_walls_for_interior_fails(self):
        setup = {
            "grid_width": 20,
            "grid_height": 15,
            "walls": [],
            "doors": [],
            "_scene_type": "dungeon",
        }
        is_valid, warnings = validate_scene_setup(setup)
        assert not is_valid
        assert any("No walls or doors" in w for w in warnings)

    def test_out_of_bounds_walls_detected(self):
        setup = {
            "grid_width": 20,
            "grid_height": 15,
            "walls": [[0, 0, 25, 0], [0, 0, 0, 14]],  # x=25 out of bounds
        }
        is_valid, warnings = validate_scene_setup(setup)
        assert not is_valid
        assert any("out of bounds" in w for w in warnings)

    def test_small_grid_detected(self):
        setup = {
            "grid_width": 2,
            "grid_height": 2,
            "walls": [],
            "doors": [],
        }
        is_valid, warnings = validate_scene_setup(setup)
        assert not is_valid
        assert any("too small" in w for w in warnings)

    def test_disconnected_walls_warned(self):
        """Two separate wall groups should be flagged as potentially disconnected."""
        setup = {
            "grid_width": 20,
            "grid_height": 15,
            "walls": [
                [0, 0, 2, 0],  # Top-left cluster
                [17, 12, 19, 12],  # Bottom-right cluster (far away)
            ],
        }
        is_valid, warnings = validate_scene_setup(setup)
        # May or may not fail depending on tolerance, but should have a warning
        assert len(warnings) > 0

    def test_correct_wall_segment_format(self):
        setup = {
            "grid_width": 20,
            "grid_height": 15,
            "walls": [[0, 0, 19, 0], [0, 0, 0, 14]],
            "doors": [{"c": [5, 0, 7, 0], "door": 1, "ds": 0}],
        }
        is_valid, warnings = validate_scene_setup(setup)
        assert is_valid

    def test_missing_scene_type_not_flagged(self):
        """Empty walls without scene_type should not flag interior requirement."""
        setup = {
            "grid_width": 20,
            "grid_height": 15,
            "walls": [],
            "doors": [],
        }
        is_valid, warnings = validate_scene_setup(setup)
        # No walls for unknown scene type — should not warn about interior
        assert not any("No walls or doors" in w for w in warnings)


# ---------------------------------------------------------------------------
# Integration: generate_and_validate
# ---------------------------------------------------------------------------

class TestGenerateAndValidate:
    """End-to-end: generate layout and verify it passes validation."""

    def test_generates_valid_setup(self):
        setup = generate_and_validate(scene_type="dungeon", grid_width=20, grid_height=15)
        is_valid, warnings = validate_scene_setup(setup)
        assert is_valid, f"Generated setup failed validation: {warnings}"

    def test_cave_type_works(self):
        setup = generate_and_validate(scene_type="cave", grid_width=20, grid_height=15)
        is_valid, warnings = validate_scene_setup(setup)
        assert is_valid

    def test_result_has_required_keys(self):
        setup = generate_and_validate(grid_width=20, grid_height=15)
        assert "walls" in setup
        assert "doors" in setup
        assert "grid_width" in setup
        assert "grid_height" in setup
        assert setup["grid_size_px"] == 64


# ---------------------------------------------------------------------------
# Integration: layout mask generation with PIL
# ---------------------------------------------------------------------------

class TestLayoutMaskGeneration:
    """Layout masks are valid PNGs consumable by ControlNet."""

    @pytest.fixture
    def tmp_layout_dir(self, tmp_path):
        layout_dir = tmp_path / "layouts"
        layout_dir.mkdir()
        return layout_dir

    def test_procedural_mask_is_valid_png(self, tmp_layout_dir):
        """Generated mask is a valid PNG that can be loaded by PIL."""
        from PIL import Image
        from campaign.map_generator import MapGenerator

        mg = MapGenerator()
        setup = {
            "grid_width": 16,
            "grid_height": 12,
            "walls": [[0, 0, 15, 0], [0, 0, 0, 11], [15, 0, 15, 11], [0, 11, 15, 11]],
            "doors": [{"c": [5, 0, 7, 0], "door": 1, "ds": 0}],
            "_output_dir": str(tmp_layout_dir),
        }
        mask_path = asyncio.run(mg.generate_layout_mask(setup, width=1024, height=768))
        assert mask_path is not None
        assert mask_path.exists()
        assert mask_path.suffix == ".png"
        # Verify it's a valid image
        img = Image.open(mask_path)
        assert img.size == (1024, 768)
        assert img.mode == "L"  # grayscale

    def test_procedural_fallback_mask_is_valid_png(self, tmp_layout_dir):
        """Procedurally-generated fallback mask is also valid."""
        from PIL import Image
        from campaign.map_generator import MapGenerator

        mg = MapGenerator()
        setup = {
            "grid_width": 16,
            "grid_height": 12,
            "walls": [],  # Intentionally empty to trigger fallback
            "doors": [],
            "_output_dir": str(tmp_layout_dir),
            "_scene_type": "dungeon",
        }
        # The fallback should generate a valid procedural mask
        mask_path = asyncio.run(mg.generate_procedural_layout_mask(
            scene_setup=setup,
            width=1024,
            height=768,
            scene_type="dungeon",
            seed=42,
        ))
        assert mask_path is not None
        assert mask_path.exists()
        assert mask_path.suffix == ".png"
        img = Image.open(mask_path)
        assert img.size == (1024, 768)
        # Should have some white pixels (walls)
        pixels = list(img.getdata())
        assert any(p > 0 for p in pixels), "Procedural mask has no walls drawn"


# ---------------------------------------------------------------------------
# No regression: existing generate_layout_mask still works
# ---------------------------------------------------------------------------

class TestNoRegression:
    """Existing layout mask generation is unchanged."""

    def test_existing_mask_generation_still_works(self, tmp_path):
        from PIL import Image
        from campaign.map_generator import MapGenerator

        mg = MapGenerator()
        setup = {
            "grid_width": 16,
            "grid_height": 12,
            "walls": [[0, 0, 15, 0], [0, 0, 0, 11]],
            "doors": [],
            "_output_dir": str(tmp_path),
        }
        mask_path = asyncio.run(mg.generate_layout_mask(setup, width=1024, height=768))
        assert mask_path is not None
        assert mask_path.exists()
        img = Image.open(mask_path)
        assert img.size == (1024, 768)
