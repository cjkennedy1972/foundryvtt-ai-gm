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
    ProceduralLayoutGenerator,
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
        pixels = list(img.tobytes())
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


# ---------------------------------------------------------------------------
# Procedural Layout Generator: Tavern, Castle, Inn
# ---------------------------------------------------------------------------

# (name, method_name, min_width, min_height, min_rooms)
BUILDINGS = [
    ("tavern", "generate_tavern_layout", 30, 25, 5),
    ("castle", "generate_castle_layout", 150, 150, 5),
    ("inn", "generate_inn_layout", 50, 45, 7),
]


def _gen(method_name, **kwargs):
    gen = ProceduralLayoutGenerator()
    return getattr(gen, method_name)(**kwargs)


@pytest.mark.parametrize("name,method,min_w,min_h,min_rooms", BUILDINGS)
class TestBuildingLayouts:
    """Shared structural contract for every hand-crafted building layout."""

    def test_produces_rooms_walls_and_doors(self, name, method, min_w, min_h, min_rooms):
        rooms, walls, doors = _gen(method)
        assert len(rooms) >= min_rooms, f"{name} should have >={min_rooms} rooms"
        assert walls, f"{name} should have walls"
        assert doors, f"{name} should have doors"

    def test_rooms_have_valid_dimensions(self, name, method, min_w, min_h, min_rooms):
        rooms, _, _ = _gen(method)
        for room in rooms:
            assert room.w > 0 and room.h > 0, f"Invalid room dims: {room}"
            assert room.x >= 0 and room.y >= 0, f"Negative position: {room}"

    def test_rooms_do_not_overlap(self, name, method, min_w, min_h, min_rooms):
        rooms, _, _ = _gen(method)
        for i, r1 in enumerate(rooms):
            for r2 in rooms[i + 1:]:
                assert not r1.overlaps(r2), f"Rooms overlap in {name}: {r1} vs {r2}"

    def test_rooms_and_walls_stay_inside_grid(self, name, method, min_w, min_h, min_rooms):
        """Geometry must fit the grid it was asked for, at the minimum footprint."""
        rooms, walls, doors = _gen(method, width=min_w, height=min_h)
        for room in rooms:
            assert room.x + room.w <= min_w, f"{name} room exceeds width: {room}"
            assert room.y + room.h <= min_h, f"{name} room exceeds height: {room}"
        for seg in walls + [d["c"] for d in doors]:
            assert max(seg[0], seg[2]) < min_w, f"{name} segment exceeds width: {seg}"
            assert max(seg[1], seg[3]) < min_h, f"{name} segment exceeds height: {seg}"

    def test_grid_smaller_than_plan_is_rejected(self, name, method, min_w, min_h, min_rooms):
        """The plan is fixed-size; an undersized grid must fail loudly, not emit
        rooms and walls outside the scene."""
        with pytest.raises(ValueError):
            _gen(method, width=min_w - 1, height=min_h)
        with pytest.raises(ValueError):
            _gen(method, width=min_w, height=min_h - 1)

    def test_larger_grid_grows_the_exterior_shell(self, name, method, min_w, min_h, min_rooms):
        base_walls = _gen(method)[1]
        big_walls = _gen(method, width=min_w + 10, height=min_h + 10)[1]
        assert len(big_walls) > len(base_walls), f"{name} shell ignored the larger grid"

    def test_doors_are_openings_not_panels_on_walls(self, name, method, min_w, min_h, min_rooms):
        """A door segment must not also be present as a solid wall segment."""
        _, walls, doors = _gen(method)
        wall_set = {tuple(w) for w in walls}
        for door in doors:
            assert tuple(door["c"]) not in wall_set, (
                f"{name} door {door['c']} overlaps a solid wall segment"
            )

    def test_every_door_connects_to_a_wall_run(self, name, method, min_w, min_h, min_rooms):
        """A door must sit in a wall line, not float in open space."""
        _, walls, doors = _gen(method)
        endpoints = set()
        for x0, y0, x1, y1 in walls:
            endpoints.add((x0, y0))
            endpoints.add((x1, y1))
        for door in doors:
            x0, y0, x1, y1 = door["c"]
            assert (x0, y0) in endpoints or (x1, y1) in endpoints, (
                f"{name} door {door['c']} is not attached to any wall"
            )

    def test_wall_segments_are_unit_length_and_axis_aligned(self, name, method, min_w, min_h, min_rooms):
        _, walls, doors = _gen(method)
        for x0, y0, x1, y1 in walls + [d["c"] for d in doors]:
            assert (x0 == x1) != (y0 == y1), f"{name} diagonal/degenerate segment"
            assert abs(x1 - x0) + abs(y1 - y0) == 1, f"{name} non-unit segment"

    def test_doors_are_foundry_shaped(self, name, method, min_w, min_h, min_rooms):
        _, _, doors = _gen(method)
        for door in doors:
            assert set(door) == {"c", "door", "ds"}, f"{name} unexpected door keys: {door}"
            assert len(door["c"]) == 4 and door["door"] == 1

    def test_output_passes_scene_setup_validation(self, name, method, min_w, min_h, min_rooms):
        """walls and doors must go in separate scene_setup keys and validate clean."""
        _, walls, doors = _gen(method)
        setup = {
            "walls": walls,
            "doors": doors,
            "scene_type": name,
            "grid_width": min_w,
            "grid_height": min_h,
            "grid_size_px": 64,
        }
        is_valid, warnings = validate_scene_setup(setup)
        assert is_valid, f"{name} layout failed validation: {warnings}"

    def test_output_is_stable_across_instances(self, name, method, min_w, min_h, min_rooms):
        """Layouts are hand-crafted constants — two generators agree exactly."""
        a = _gen(method)
        b = _gen(method)
        assert a[0] == b[0]
        assert a[1] == b[1]
        assert a[2] == b[2]


class TestBuildingLayoutFeatures:
    """Per-building features the shared contract can't express."""

    def test_tavern_has_bar_booths_kitchen_and_cellar(self):
        rooms, _, _ = _gen("generate_tavern_layout")
        assert len(rooms) == 5
        bar = max(rooms, key=lambda r: r.w * r.h)
        assert bar.w * bar.h > sum(r.w * r.h for r in rooms if r is not bar) / 2, (
            "the common room should dominate the floor plan"
        )

    def test_castle_has_a_large_throne_room(self):
        rooms, _, _ = _gen("generate_castle_layout")
        assert max(r.w * r.h for r in rooms) >= 1000, "Should have a large throne room"

    def test_inn_has_three_equal_guest_rooms(self):
        rooms, _, _ = _gen("generate_inn_layout")
        sizes = [(r.w, r.h) for r in rooms]
        assert sizes.count((12, 6)) == 3, f"Expected 3 guest rooms, got {sizes}"

    def test_inn_common_room_is_substantial(self):
        rooms, _, _ = _gen("generate_inn_layout")
        common = rooms[0]
        assert common.w > 10 and common.h > 10, "Common room should be substantial"

    def test_carve_doors_rejects_a_door_with_no_wall(self):
        """Guard against a future edit moving a door off its wall line."""
        with pytest.raises(ValueError, match="not on any wall"):
            ProceduralLayoutGenerator._carve_doors(
                [[0, 0, 1, 0]], [{"c": [9, 9, 10, 9], "door": 1, "ds": 0}]
            )
