"""Coverage-gap tests for campaign.layout_generator (BSP/CA edge paths, validator)."""


import campaign.layout_generator as lg
from campaign.layout_generator import (
    BSPGenerator,
    CellularAutomataGenerator,
    Corridor,
    Door,
    LayoutResult,
    Room,
    _BSPNode,
    generate_and_validate,
    validate_scene_setup,
)


class _Script:
    """Deterministic stand-in for random.Random."""

    def __init__(self, ints=(), floats=()):
        self.ints = list(ints)
        self.floats = list(floats)

    def randint(self, a, b):
        return self.ints.pop(0) if self.ints else a

    def random(self):
        return self.floats.pop(0) if self.floats else 0.0


def _bsp(**kw):
    return BSPGenerator(seed=1, **kw)


# ─── BSP ──────────────────────────────────────────────────────────────────


def test_bsp_single_leaf_when_max_leaves_is_one():
    res = _bsp(max_leaves=1).generate()
    assert len(res.rooms) == 1
    assert res.corridors == [] and res.doors == []


def test_bsp_degenerate_split_range_leaves_node_unsplit():
    g = _bsp()
    # width 10 = 2*min_child_dim(5) -> min_pos == max_pos -> no split
    node = _BSPNode(0, 0, 10, 4, 5)
    g._split(node)
    assert node.is_leaf and g._leaf_count == 1
    node = _BSPNode(0, 0, 4, 10, 5)
    g._split(node)
    assert node.is_leaf and g._leaf_count == 1


def test_bsp_vertical_only_split_creates_top_bottom():
    g = _bsp()
    node = _BSPNode(0, 0, 6, 14, 5)  # only height can split
    g._split(node)
    assert node.top is not None and node.bottom is not None
    assert node.left is None
    assert node.top.h + node.bottom.h == node.h + 1  # children share the split row


def test_random_room_in_too_small_node_is_clamped_inside():
    g = _bsp()
    room = g._random_room_in_node(_BSPNode(0, 0, 4, 4, 5))
    assert (room.x, room.y, room.w, room.h) == (1, 1, 3, 3)
    assert room.x + room.w <= 4 and room.y + room.h <= 4


def _rooms_node():
    g = _bsp()
    a, b, c = Room(1, 1, 3, 3), Room(10, 1, 3, 3), Room(1, 10, 3, 3)
    root = _BSPNode(0, 0, 20, 20, 5)
    root.left, root.right, root.top = _BSPNode(0, 0, 1, 1, 5), _BSPNode(0, 0, 1, 1, 5), _BSPNode(0, 0, 1, 1, 5)
    root.left.room, root.right.room, root.top.room = a, b, c
    return g, root


def test_connect_rooms_three_children_chains_third_when_roll_low():
    g, root = _rooms_node()
    g._leaf_count = 1
    g.rng = _Script(floats=[0.1])
    g._connect_rooms(root)
    # first pair: 2 segments, chained third: 2 more
    assert len(g._layout_corridors) == 4
    assert g._layout_corridors[0] == Corridor(2, 2, 11, 2)
    assert g._layout_corridors[2] == Corridor(11, 2, 2, 2) or g._layout_corridors[2].x0 == 11


def test_connect_rooms_three_children_skips_third_when_roll_high():
    g, root = _rooms_node()
    g._leaf_count = 1
    g.rng = _Script(floats=[0.9])
    g._connect_rooms(root)
    assert len(g._layout_corridors) == 2


def test_connect_rooms_empty_child_subtree_contributes_nothing():
    g = _bsp()
    root = _BSPNode(0, 0, 20, 20, 5)
    root.left, root.right = _BSPNode(0, 0, 1, 1, 5), _BSPNode(0, 0, 1, 1, 5)
    root.left.room = Room(1, 1, 3, 3)  # right has no room
    g._connect_rooms(root)
    assert g._layout_corridors == []


def test_add_l_corridor_rejects_out_of_grid_points():
    g = _bsp(grid_w=10, grid_h=10)
    g._add_l_corridor(-1, 0, 5, 5)
    g._add_l_corridor(0, 0, 10, 5)
    g._add_l_corridor(0, 0, 5, 10)
    assert g._layout_corridors == [] and g._corridor_count == 0
    g._add_l_corridor(1, 2, 5, 6)
    assert g._layout_corridors == [Corridor(1, 2, 5, 2), Corridor(5, 2, 5, 6)]


def test_add_random_loop_guards_and_success():
    g = _bsp()
    a, b = Room(1, 1, 3, 3), Room(10, 10, 3, 3)
    node = _BSPNode(0, 0, 20, 20, 5)
    node.left, node.right = _BSPNode(0, 0, 1, 1, 5), _BSPNode(0, 0, 1, 1, 5)
    g._add_random_loop(node)  # <2 leaf rooms below node
    assert g._layout_corridors == []
    node.left.room, node.right.room = a, b
    g._leaf_rooms = [a, b]
    g._room_count = 0  # too few rooms to bother with loops
    g._add_random_loop(node)
    assert g._layout_corridors == []
    g._room_count = 99
    g.rng = _Script(ints=[0, 0])  # same room twice
    g._add_random_loop(node)
    assert g._layout_corridors == []
    g.rng = _Script(ints=[0, 1])
    g._add_random_loop(node)
    assert g._layout_corridors == [Corridor(2, 2, 11, 2), Corridor(11, 2, 11, 11)]


def test_connect_rooms_adds_loop_when_leaf_count_high_and_roll_low():
    g = _bsp()
    a, b = Room(1, 1, 3, 3), Room(10, 10, 3, 3)
    root = _BSPNode(0, 0, 20, 20, 5)
    root.left, root.right = _BSPNode(0, 0, 1, 1, 5), _BSPNode(0, 0, 1, 1, 5)
    root.left.room, root.right.room = a, b
    g._leaf_rooms = [a, b]
    g._room_count = 99
    g._leaf_count = g.min_leaves + 1
    g.rng = _Script(ints=[0, 0, 0, 1], floats=[0.1])
    g._connect_rooms(root)
    assert len(g._layout_corridors) == 4  # tree edge + loop edge


def test_place_doors_dedups_identical_midpoints_and_orients():
    g = _bsp()
    g._layout_corridors = [Corridor(0, 4, 4, 4), Corridor(0, 4, 4, 4), Corridor(2, 0, 2, 4)]
    g._place_doors()
    assert g._layout_doors == [Door(2, 4, "h"), Door(2, 2, "v")]
    g._layout_corridors = []
    g._place_doors()
    assert g._layout_doors == []


# ─── CA ───────────────────────────────────────────────────────────────────


def test_ca_falls_back_to_simple_grid_when_no_rooms_fit():
    g = CellularAutomataGenerator(seed=1, grid_w=20, grid_h=15, num_rooms=0)
    g.rng = _Script(floats=[0.0] * 50)  # every cell passes the 0.6 roll
    res = g.generate()
    assert res.rooms and all(r.w == r.h == 3 for r in res.rooms)
    assert res.corridors == []


def test_ca_simple_fallback_skips_cells_on_high_roll():
    g = CellularAutomataGenerator(seed=1, grid_w=20, grid_h=15)
    g.rng = _Script(floats=[0.99] * 50)
    assert g._generate_simple().rooms == []


def test_ca_generate_handles_diagonal_and_offgrid_corridors():
    g = CellularAutomataGenerator(seed=1, grid_w=20, grid_h=15)
    rooms = [Room(2, 2, 3, 3), Room(12, 8, 3, 3)]
    g._place_rooms = lambda: rooms
    cors = [Corridor(3, 3, 8, 8), Corridor(30, 3, 30, 9), Corridor(3, 30, 9, 30)]
    g._connect_rooms_list = lambda r: cors  # diagonal (skipped) + OOB vertical/horizontal
    res = g.generate()
    assert res.rooms == rooms and res.corridors == cors


def test_ca_keep_largest_connected_all_walls_returns_same_grid():
    g = CellularAutomataGenerator(seed=1, grid_w=4, grid_h=4)
    grid = [[True] * 4 for _ in range(4)]
    assert g._keep_largest_connected(grid) is grid


def test_ca_connect_rooms_list_without_result_still_returns_corridors():
    g = CellularAutomataGenerator(seed=1, grid_w=20, grid_h=15)
    cors = g._connect_rooms_list([Room(2, 2, 3, 3), Room(10, 8, 3, 3)])
    assert cors == [Corridor(3, 3, 11, 3), Corridor(11, 3, 11, 9)]
    g.result = LayoutResult()
    g._connect_rooms_list([Room(2, 2, 3, 3), Room(10, 2, 3, 3)])
    assert g.result.doors == [Door(7, 3, "h")]


# ─── generate_and_validate retry ──────────────────────────────────────────


def test_generate_and_validate_retries_with_bumped_seed(monkeypatch):
    seeds = []
    bad = LayoutResult()

    class _Good(LayoutResult):
        pass

    def fake_generate_layout(scene_type, grid_width, grid_height, seed, method):
        seeds.append((seed, method))
        return bad

    monkeypatch.setattr(lg, "generate_layout", fake_generate_layout)
    # empty layout -> dungeon with no walls -> invalid every time
    setup = generate_and_validate("dungeon", 20, 15, seed=10, max_attempts=3)
    assert seeds == [(10, "bsp"), (11, "bsp"), (12, "bsp")]
    assert setup["walls"] == []
    seeds.clear()
    generate_and_validate("cave", 20, 15, seed=None, max_attempts=2)
    assert seeds == [(None, "ca"), (None, "ca")]


def test_generate_and_validate_stops_on_first_valid(monkeypatch):
    calls = []
    good = LayoutResult(corridors=[Corridor(0, 0, 4, 0)], doors=[Door(2, 0, "h")])
    monkeypatch.setattr(lg, "generate_layout", lambda **k: calls.append(1) or good)
    setup = generate_and_validate("dungeon", 20, 15, seed=3)
    assert len(calls) == 1 and setup["walls"] == [[0, 0, 4, 0]]


# ─── validate_scene_setup ─────────────────────────────────────────────────


def _setup(**kw):
    base = {"grid_width": 20, "grid_height": 15, "scene_type": "dungeon"}
    base.update(kw)
    return base


def test_validate_small_grid_and_missing_walls():
    ok, w = validate_scene_setup(_setup(grid_width=5, walls=[]))
    assert not ok and "Grid too small: 5x15" in w and "No walls or doors for dungeon scene" in w
    ok, w = validate_scene_setup({"grid_width": 20, "grid_height": 15})
    assert ok and w == []
    ok, w = validate_scene_setup(_setup(scene_type="tavern", walls=[]))
    assert ok and w == []


def test_validate_flags_out_of_bounds_list_and_dict_walls():
    ok, w = validate_scene_setup(_setup(
        walls=[[0, 0, 25, 0], {"type": "h", "x": 99, "y": 0}],
        doors=[{"x": 1, "y": 1}],
    ))
    assert not ok
    assert "Walls out of bounds: max (25,0)" in w
    assert "Walls extend beyond grid: max (99,0)" in w


def test_validate_diagonal_invalid_format_and_bad_type():
    ok, w = validate_scene_setup(_setup(
        walls=[[0, 0, 3, 3], "junk", {"type": "z", "x": 1, "y": 1}, {"type": "h", "x": 1, "y": 1}],
        doors=[{"x": 1, "y": 1}],
    ))
    assert not ok
    assert "Diagonal wall segment not supported: [0, 0, 3, 3]" in w
    assert "Invalid wall format: junk" in w
    assert "Invalid wall type: z" in w


def test_validate_door_normalisation_and_missing_doors():
    walls = [[0, 0, 0, 3]]
    ok, w = validate_scene_setup(_setup(walls=walls, doors=[{"c": [1, 1, 1, 2]}, {"c": [1, 1, 2, 1]}, {"c": [0, 0, 1, 1]}, "x"]))
    assert ok and w == []
    ok, w = validate_scene_setup(_setup(walls=walls, doors=[]))
    assert not ok and "No doors in scene with walls" in w


def test_validate_flags_disconnected_wall_clusters():
    walls = [{"type": "h", "x": x, "y": y} for y in (0, 4, 8, 12) for x in (0, 1)]
    ok, w = validate_scene_setup(_setup(walls=walls, doors=[{"x": 1, "y": 1}]))
    assert not ok
    assert any(m.startswith("Wall segments appear disconnected: 2/8") for m in w)


def test_validate_accepts_perimeter_at_grid_edge():
    walls = [[0, 0, 20, 0], [20, 0, 20, 15]]
    ok, w = validate_scene_setup(_setup(walls=walls, doors=[{"x": 1, "y": 0}]))
    assert ok, w
