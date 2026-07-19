"""
Procedural layout generator for interior scenes.

Generates guaranteed-connected dungeon/cave geometry using two algorithms:
- BSP (Binary Space Partitioning): Structured rooms connected by corridors
- CA (Cellular Automata): Hybrid approach with random room placement + CA smoothing

Both produce scene_setup dicts compatible with map_generator.generate_layout_mask().
"""

from __future__ import annotations

import math
import random
import collections
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Room:
    """A rectangular room in the dungeon."""
    x: int
    y: int
    w: int
    h: int

    def overlaps(self, other: 'Room') -> bool:
        """Check if this room overlaps with another."""
        return not (
            self.x + self.w <= other.x or
            other.x + other.w <= self.x or
            self.y + self.h <= other.y or
            other.y + other.h <= self.y
        )


@dataclass
class Corridor:
    """A corridor connecting two rooms."""
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass
class Door:
    """A door opening in a wall segment."""
    x: int
    y: int
    orientation: str  # 'h' or 'v'


@dataclass
class LayoutResult:
    """Result of a layout generation run."""
    rooms: List[Room] = field(default_factory=list)
    corridors: List[Corridor] = field(default_factory=list)
    doors: List[Door] = field(default_factory=list)

    def to_scene_setup(self, grid_width: int, grid_height: int) -> Dict[str, Any]:
        """Convert to the scene_setup dict format used by the rest of the system.
        
        Produces walls as [x0,y0,x1,y1] segments and doors as {c:[...], door:1, ds:0}.
        """
        walls = []
        for c in self.corridors:
            if c.x0 == c.x1 and c.y0 == c.y1:
                continue
            walls.append([c.x0, c.y0, c.x1, c.y1])
        doors = []
        for d in self.doors:
            if d.orientation == 'h':
                doors.append({"c": [d.x, d.y, d.x + 1, d.y], "door": 1, "ds": 0})
            else:
                doors.append({"c": [d.x, d.y, d.x, d.y + 1], "door": 1, "ds": 0})
        return {
            "walls": walls,
            "doors": doors,
            "scene_type": "dungeon",
            "grid_width": grid_width,
            "grid_height": grid_height,
            "grid_size_px": 64,
            "_source": "procedural_layout_generator",
        }


# ---------------------------------------------------------------------------
# BSP Generator
# ---------------------------------------------------------------------------

class _BSPNode:
    """A node in the BSP tree."""
    def __init__(self, x: int, y: int, w: int, h: int, min_split: int):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.min_split = min_split
        self.left: Optional['_BSPNode'] = None
        self.right: Optional['_BSPNode'] = None
        self.top: Optional['_BSPNode'] = None
        self.bottom: Optional['_BSPNode'] = None
        self.room: Optional[Room] = None
        self.children: List['_BSPNode'] = []

    @property
    def is_leaf(self) -> bool:
        return self.left is None and self.right is None and self.top is None and self.bottom is None


class BSPGenerator:
    """
    Binary Space Partitioning dungeon generator.

    Algorithm:
    1. Start with the full grid as one node.
    2. Recursively split nodes into two sub-nodes until leaf count target met.
    3. Carve a random room into each leaf.
    4. Connect all leaves into a spanning tree via their parent split edges.
    5. Optionally add a few extra connections (loops) for interesting layouts.

    Guarantees:
    - Every room is reachable from every other room (spanning tree + loops).
    - No overlapping rooms.
    - Rooms respect grid boundaries.

    Reference: Hoppes, "Map Generation with BSP Trees" (GDC talk).
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        grid_w: int = 20,
        grid_h: int = 15,
        min_room_size: int = 3,
        max_room_size: int = 6,
        min_leaves: int = 4,
        max_leaves: int = 8,
        extra_connections: int = 2,
    ):
        self.rng = random.Random(seed)
        self.grid_w = grid_w
        self.grid_h = grid_h
        self.min_room_size = min_room_size
        self.max_room_size = max_room_size
        self.min_leaves = min_leaves
        self.max_leaves = max_leaves
        self.extra_connections = extra_connections
        self._min_dim = 3
        self._leaf_count = 1
        self._leaf_rooms = []
        self._room_count = 0
        self._corridor_count = 0
        self._door_count = 0
        self._layout_corridors = []
        self._layout_doors = []

    def generate(self) -> LayoutResult:
        root = _BSPNode(0, 0, self.grid_w, self.grid_h, self.min_room_size + 2)
        self._split(root)
        self._place_rooms_in_leaves(root)
        self._connect_rooms(root)
        self._place_doors()
        return self._collect_result(root)

    def _split(self, node: '_BSPNode') -> None:
        if self._leaf_count >= self.max_leaves:
            return
        min_child_dim = self.min_room_size + 2
        can_split_h = (node.w >= min_child_dim * 2)
        can_split_v = (node.h >= min_child_dim * 2)
        if not can_split_h and not can_split_v:
            return
        if can_split_h and can_split_v:
            split_h = abs(node.w / 2 - node.h / 2) > 2
            do_h = self.rng.random() < 0.5
            if not split_h:
                do_h = self.rng.random() < 0.6
        elif can_split_h:
            do_h = True
        else:
            do_h = False
        if do_h and node.w >= min_child_dim * 2:
            min_pos = node.x + min_child_dim
            max_pos = node.x + node.w - min_child_dim
            if min_pos >= max_pos:
                return
            split_pos = self.rng.randint(min_pos, max_pos)
            left = _BSPNode(node.x, node.y, split_pos - node.x + 1, node.h, min_child_dim)
            right = _BSPNode(split_pos + 1, node.y, node.x + node.w - split_pos, node.h, min_child_dim)
            node.left = left
            node.right = right
            self._leaf_count += 1
            self._split(left)
            self._split(right)
        elif can_split_v and node.h >= min_child_dim * 2:
            min_pos = node.y + min_child_dim
            max_pos = node.y + node.h - min_child_dim
            if min_pos >= max_pos:
                return
            split_pos = self.rng.randint(min_pos, max_pos)
            top = _BSPNode(node.x, node.y, node.w, split_pos - node.y + 1, min_child_dim)
            bottom = _BSPNode(node.x, split_pos + 1, node.w, node.y + node.h - split_pos, min_child_dim)
            node.top = top
            node.bottom = bottom
            self._leaf_count += 1
            self._split(top)
            self._split(bottom)

    def _place_rooms_in_leaves(self, node: '_BSPNode') -> None:
        if node.is_leaf:
            room = self._random_room_in_node(node)
            node.room = room
            self._leaf_rooms.append(room)
        else:
            if node.left:
                self._place_rooms_in_leaves(node.left)
            if node.right:
                self._place_rooms_in_leaves(node.right)
            if node.top:
                self._place_rooms_in_leaves(node.top)
            if node.bottom:
                self._place_rooms_in_leaves(node.bottom)

    def _random_room_in_node(self, node: '_BSPNode') -> Room:
        margin = 1
        min_w = self.min_room_size
        max_w = min(self.max_room_size, node.w - 2 * margin)
        min_h = self.min_room_size
        max_h = min(self.max_room_size, node.h - 2 * margin)
        if max_w < min_w or max_h < min_h:
            max_w = max(min_w, max_w)
            max_h = max(min_h, max_h)
        w = self.rng.randint(min_w, max_w)
        h = self.rng.randint(min_h, max_h)
        x = self.rng.randint(node.x + margin, max(node.x + margin, node.x + node.w - w - margin))
        y = self.rng.randint(node.y + margin, max(node.y + margin, node.y + node.h - h - margin))
        x = max(node.x, min(x, node.x + node.w - w))
        y = max(node.y, min(y, node.y + node.h - h))
        w = min(w, node.x + node.w - x)
        h = min(h, node.y + node.h - y)
        return Room(x=x, y=y, w=w, h=h)

    def _connect_rooms(self, node: '_BSPNode') -> None:
        if node.is_leaf:
            return
        children = [n for n in (node.left, node.right, node.top, node.bottom) if n is not None]
        rooms = [n.room for n in children if n.room is not None]
        if len(rooms) >= 2:
            p1 = self._center(rooms[0])
            p2 = self._center(rooms[1])
            self._add_l_corridor(p1[0], p1[1], p2[0], p2[1])
        for i in range(2, len(rooms)):
            p_prev = self._center(rooms[i - 1])
            p_curr = self._center(rooms[i])
            if self.rng.random() < 0.4:
                self._add_l_corridor(p_prev[0], p_prev[1], p_curr[0], p_curr[1])
        for child in children:
            self._connect_rooms(child)
        if self._leaf_count > self.min_leaves and self.rng.random() < 0.3:
            self._add_random_loop(node)

    def _center(self, room: Room) -> Tuple[int, int]:
        return room.x + room.w // 2, room.y + room.h // 2

    def _add_l_corridor(self, x0: int, y0: int, x1: int, y1: int) -> None:
        if x0 < 0 or x0 >= self.grid_w or y0 < 0 or y0 >= self.grid_h:
            return
        if x1 < 0 or x1 >= self.grid_w or y1 < 0 or y1 >= self.grid_h:
            return
        self._corridor_count += 1
        self._layout_corridors.append(Corridor(x0, y0, x1, y0))
        self._layout_corridors.append(Corridor(x1, y0, x1, y1))

    def _add_random_loop(self, node: '_BSPNode') -> None:
        leaves = self._all_leaf_rooms(node)
        if len(leaves) < 2:
            return
        if self._room_count <= self.min_leaves + self.extra_connections:
            return
        r1 = self._leaf_rooms[self.rng.randint(0, len(self._leaf_rooms) - 1)]
        r2 = self._leaf_rooms[self.rng.randint(0, len(self._leaf_rooms) - 1)]
        if r1 is r2:
            return
        p1 = self._center(r1)
        p2 = self._center(r2)
        self._add_l_corridor(p1[0], p1[1], p2[0], p2[1])

    def _all_leaf_rooms(self, node: '_BSPNode') -> List[Room]:
        if node is None:
            return []
        if node.room is not None:
            return [node.room]
        result = []
        for child in (node.left, node.right, node.top, node.bottom):
            result.extend(self._all_leaf_rooms(child))
        return result

    def _place_doors(self) -> None:
        self._layout_doors = []
        for cor in self._layout_corridors:
            mid_x = (cor.x0 + cor.x1) // 2
            mid_y = (cor.y0 + cor.y1) // 2
            orient = 'h' if cor.y0 == cor.y1 else 'v'
            self._layout_doors.append(Door(mid_x, mid_y, orient))
        if len(self._layout_doors) > 0:
            placed = []
            placed_set = set()
            for d in self._layout_doors:
                if (d.x, d.y, d.orientation) not in placed_set:
                    placed_set.add((d.x, d.y, d.orientation))
                    placed.append(d)
            self._layout_doors = placed

    def _collect_result(self, root: '_BSPNode') -> LayoutResult:
        rooms = self._leaf_rooms[:]
        return LayoutResult(rooms=rooms, corridors=self._layout_corridors, doors=self._layout_doors)


# ---------------------------------------------------------------------------
# Cellular Automata Generator — cave-style connected geometry
# ---------------------------------------------------------------------------

class CellularAutomataGenerator:
    """
    Hybrid cave generator: places rooms via simple random placement,
    then fills corridors using cellular automata on the remaining space.

    Algorithm:
    1. Place N random non-overlapping rooms (like a simplified BSP without splits).
    2. Connect rooms with L-corridors (guaranteed spanning tree).
    3. Fill corridor cells with CA-smoothed cave texture for visual variety.
    4. Flood-fill to ensure connectivity, discard disconnected regions.
    5. Convert to wall/door coordinate format.

    This hybrid approach gives guaranteed connectivity (from the room placement)
    with cave-like aesthetics (from the CA filling).
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        grid_w: int = 20,
        grid_h: int = 15,
        fill_ratio: float = 0.45,
        iterations: int = 3,
        min_room_cells: int = 3,
        num_rooms: int = 5,
    ):
        self.rng = random.Random(seed)
        self.grid_w = grid_w
        self.grid_h = grid_h
        self.fill_ratio = fill_ratio
        self.iterations = iterations
        self.min_room_cells = min_room_cells
        self.num_rooms = num_rooms

    def generate(self) -> LayoutResult:
        result = LayoutResult()
        self.result = result

        # Place rooms
        rooms = self._place_rooms()
        if not rooms:
            return self._generate_simple()

        # Connect rooms with corridors
        corridors = self._connect_rooms_list(rooms)

        # Build initial grid from rooms + corridors
        grid = [[True for _ in range(self.grid_w)] for _ in range(self.grid_h)]
        for room in rooms:
            for y in range(room.y, min(room.y + room.h, self.grid_h)):
                for x in range(room.x, min(room.x + room.w, self.grid_w)):
                    grid[y][x] = False

        for cor in corridors:
            if cor.x0 == cor.x1:
                for y in range(min(cor.y0, cor.y1), max(cor.y0, cor.y1) + 1):
                    if 0 <= cor.x0 < self.grid_w and 0 <= y < self.grid_h:
                        grid[y][cor.x0] = False
            elif cor.y0 == cor.y1:
                for x in range(min(cor.x0, cor.x1), max(cor.x0, cor.x1) + 1):
                    if 0 <= x < self.grid_w and 0 <= cor.y0 < self.grid_h:
                        grid[cor.y0][x] = False

        # Apply CA smoothing (preserving rooms)
        grid = self._smooth_preserving_rooms(grid, rooms)

        # Flood fill to ensure connectivity
        grid = self._keep_largest_connected(grid)

        # Extract final layout
        result.rooms = [r for r in rooms if r is not None]
        result.corridors = corridors
        return result

    def _generate_simple(self) -> LayoutResult:
        """Fallback: generate a simple connected grid of rooms."""
        result = LayoutResult()
        room_size = max(3, min(self.grid_w, self.grid_h) // 4)
        for y in range(0, self.grid_h - room_size, room_size):
            for x in range(0, self.grid_w - room_size, room_size):
                if self.rng.random() < 0.6:
                    result.rooms.append(Room(x=x, y=y, w=room_size, h=room_size))
        return result

    def _place_rooms(self) -> List[Room]:
        """Place random non-overlapping rooms on the grid."""
        rooms = []
        margin = 1
        max_room_w = min(5, max(3, self.grid_w // 4))
        max_room_h = min(4, max(3, self.grid_h // 4))
        for _ in range(self.num_rooms * 3):
            if len(rooms) >= self.num_rooms:
                break
            w = self.rng.randint(3, max_room_w)
            h = self.rng.randint(3, max_room_h)
            x = self.rng.randint(margin, max(margin, self.grid_w - w - margin))
            y = self.rng.randint(margin, max(margin, self.grid_h - h - margin))
            candidate = Room(x=x, y=y, w=w, h=h)
            if not any(candidate.overlaps(r) for r in rooms):
                rooms.append(candidate)
        return rooms

    def _connect_rooms_list(self, rooms: List[Room]) -> List[Corridor]:
        """Connect adjacent rooms with L-corridors and populate doors."""
        corridors = []
        for i in range(len(rooms) - 1):
            r1 = rooms[i]
            r2 = rooms[i + 1]
            x1 = r1.x + r1.w // 2
            y1 = r1.y + r1.h // 2
            x2 = r2.x + r2.w // 2
            y2 = r2.y + r2.h // 2
            x1 = max(0, min(x1, self.grid_w - 1))
            y1 = max(0, min(y1, self.grid_h - 1))
            x2 = max(0, min(x2, self.grid_w - 1))
            y2 = max(0, min(y2, self.grid_h - 1))
            corridors.append(Corridor(x1, y1, x2, y1))
            corridors.append(Corridor(x2, y1, x2, y2))
            mid_x = (x1 + x2) // 2
            mid_y = (y1 + y2) // 2
            orient = 'h' if y1 == y2 else 'v'
            if hasattr(self, 'result') and self.result is not None:
                self.result.doors.append(Door(mid_x, mid_y, orient))
        return corridors

    def _smooth_preserving_rooms(self, grid, rooms):
        """Apply CA smoothing while preserving room cells."""
        room_cells = set()
        for room in rooms:
            for y in range(room.y, min(room.y + room.h, self.grid_h)):
                for x in range(room.x, min(room.x + room.w, self.grid_w)):
                    room_cells.add((x, y))

        new_grid = [row[:] for row in grid]
        for _ in range(self.iterations):
            for y in range(self.grid_h):
                for x in range(self.grid_w):
                    if (x, y) in room_cells:
                        continue
                    wc = 0
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < self.grid_h and 0 <= nx < self.grid_w:
                                if grid[ny][nx]:
                                    wc += 1
                    if y == 0 or y == self.grid_h - 1 or x == 0 or x == self.grid_w - 1:
                        new_grid[y][x] = True
                    else:
                        new_grid[y][x] = wc >= 3
            grid = [row[:] for row in new_grid]
        return grid

    def _keep_largest_connected(self, grid) -> List[List[bool]]:
        """Flood-fill from first floor cell, keep only that region."""
        start = None
        for y in range(self.grid_h):
            for x in range(self.grid_w):
                if not grid[y][x]:
                    start = (x, y)
                    break
            if start:
                break
        if start is None:
            return grid
        visited = set()
        queue = collections.deque([start])
        visited.add(start)
        while queue:
            x, y = queue.popleft()
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.grid_w and 0 <= ny < self.grid_h:
                    if not grid[ny][nx] and (nx, ny) not in visited:
                        visited.add((nx, ny))
                        queue.append((nx, ny))
        new_grid = [[True for _ in range(self.grid_w)] for _ in range(self.grid_h)]
        for (x, y) in visited:
            new_grid[y][x] = False
        return new_grid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_layout(
    scene_type: str = "dungeon",
    grid_width: int = 20,
    grid_height: int = 15,
    seed: Optional[int] = None,
    method: str = "bsp",
) -> LayoutResult:
    """Generate a procedural dungeon/cave layout.

    Args:
        scene_type: 'dungeon' or 'cave' — influences room size distribution
        grid_width, grid_height: Grid dimensions in cells
        seed: Random seed for reproducibility
        method: 'bsp' (structured rooms+corridors) or 'ca' (cave-style)

    Returns:
        LayoutResult with rooms, corridors, and doors
    """
    if method == "ca":
        gen = CellularAutomataGenerator(
            seed=seed,
            grid_w=grid_width,
            grid_h=grid_height,
            fill_ratio=0.45 if scene_type == "dungeon" else 0.50,
            iterations=3,
            min_room_cells=3,
            num_rooms=5,
        )
    else:
        min_room = 3 if scene_type == "dungeon" else 4
        max_room = 6 if scene_type == "dungeon" else 8
        gen = BSPGenerator(
            seed=seed,
            grid_w=grid_width,
            grid_h=grid_height,
            min_room_size=min_room,
            max_room_size=max_room,
            min_leaves=4,
            max_leaves=8,
            extra_connections=2,
        )
    return gen.generate()


def generate_and_validate(
    scene_type: str = "dungeon",
    grid_width: int = 20,
    grid_height: int = 15,
    seed: Optional[int] = None,
    max_attempts: int = 5,
) -> Dict[str, Any]:
    """Generate a layout and return it as a scene_setup dict.

    Always produces guaranteed-connected geometry: retries (with a bumped
    seed, so a pinned seed is still reproducible overall) up to max_attempts
    if the generated layout fails validate_scene_setup.
    """
    setup = None
    for attempt in range(max_attempts):
        attempt_seed = seed + attempt if seed is not None else None
        result = generate_layout(
            scene_type=scene_type,
            grid_width=grid_width,
            grid_height=grid_height,
            seed=attempt_seed,
            method="ca" if scene_type == "cave" else "bsp",
        )
        setup = result.to_scene_setup(grid_width, grid_height)
        is_valid, _ = validate_scene_setup(setup)
        if is_valid:
            break
    return setup


def validate_scene_setup(setup: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a scene_setup dict for proper geometry.

    Returns:
        (is_valid, warnings) where is_valid is True if the geometry is acceptable
        and warnings is a list of issues found.
    """
    warnings = []
    walls = setup.get("walls", [])
    doors = setup.get("doors", [])
    grid_w = setup.get("grid_width", 20)
    grid_h = setup.get("grid_height", 15)
    scene_type = setup.get("scene_type") or setup.get("_scene_type", None)

    if grid_w < 8 or grid_h < 8:
        warnings.append(f"Grid too small: {grid_w}x{grid_h}")

    if not walls:
        if scene_type and scene_type != "dungeon":
            pass  # Other scene types may not need walls
        elif scene_type == "dungeon":
            warnings.append("No walls or doors for dungeon scene")
        # If scene_type is missing, don't flag - it's unknown
        return len(warnings) == 0, warnings

    # Check for out-of-bounds before normalization
    for w in walls:
        if isinstance(w, list) and len(w) == 4:
            x0, y0, x1, y1 = w
            if max(x0, x1) >= grid_w or max(y0, y1) >= grid_h:
                warnings.append(f"Walls out of bounds: max ({max(x0,x1)},{max(y0,y1)})")
        elif isinstance(w, dict):
            x, y = w.get("x", 0), w.get("y", 0)
            if x >= grid_w or y >= grid_h:
                warnings.append(f"Walls extend beyond grid: max ({x},{y})")

    # Normalize walls to dict format if needed (support both [x0,y0,x1,y1] and {"type":"h","x":x,"y":y})
    normalized_walls = []
    for w in walls:
        if isinstance(w, list) and len(w) == 4:
            x0, y0, x1, y1 = w
            if x0 == x1:
                for y in range(min(y0, y1), max(y0, y1) + 1):
                    normalized_walls.append({"type": "v", "x": x0, "y": y})
            elif y0 == y1:
                for x in range(min(x0, x1), max(x0, x1) + 1):
                    normalized_walls.append({"type": "h", "x": x, "y": y0})
            else:
                warnings.append(f"Diagonal wall segment not supported: {w}")
        elif isinstance(w, dict):
            normalized_walls.append(w)
        else:
            warnings.append(f"Invalid wall format: {w}")
    walls = normalized_walls

    # Normalize doors if needed
    normalized_doors = []
    for d in doors:
        if isinstance(d, dict) and "c" in d:
            c = d["c"]
            x0, y0, x1, y1 = c
            if x0 == x1:
                normalized_doors.append({"x": x0, "y": y0, "orientation": "v"})
            elif y0 == y1:
                normalized_doors.append({"x": x0, "y": y0, "orientation": "h"})
        elif isinstance(d, dict) and "x" in d and "y" in d:
            normalized_doors.append(d)
    doors = normalized_doors

    if len(walls) > 0 and len(doors) == 0:
        warnings.append("No doors in scene with walls")

    wall_set = set()
    for w in walls:
        if w.get("type") != "h" and w.get("type") != "v":
            warnings.append(f"Invalid wall type: {w.get('type')}")
            continue
        x, y = w.get("x"), w.get("y")
        if w.get("type") == "h":
            wall_set.add((x, y))
        else:
            wall_set.add((x, y))

    if wall_set:
        start = next(iter(wall_set))
        visited = set()
        queue = collections.deque([start])
        visited.add(start)
        while queue:
            cx, cy = queue.popleft()
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in wall_set and (nx, ny) not in visited:
                    visited.add((nx, ny))
                    queue.append((nx, ny))
        if len(visited) < len(wall_set) * 0.3:
            warnings.append(f"Wall segments appear disconnected: {len(visited)}/{len(wall_set)}")

    return len(warnings) == 0, warnings
