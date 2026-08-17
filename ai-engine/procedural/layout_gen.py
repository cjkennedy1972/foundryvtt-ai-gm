"""Procedural layout generation — BSP dungeon generation for interior maps.

Generates dungeon layouts procedurally when manual maps don't exist.
"""

import random
import logging
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class RoomType(Enum):
    """Types of rooms in a dungeon."""
    CHAMBER = "chamber"      # Main room
    CORRIDOR = "corridor"    # Connecting passage
    TREASURE = "treasure"    # Treasure room
    TRAP = "trap"           # Trapped room
    GUARD = "guard"         # Guard post


@dataclass
class Room:
    """A rectangular room in a dungeon."""
    x: int
    y: int
    width: int
    height: int
    room_type: RoomType = RoomType.CHAMBER
    id: Optional[int] = None

    def center(self) -> Tuple[int, int]:
        """Get room center coordinates."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_foundry_walls(self) -> List[Dict]:
        """Convert room boundaries to Foundry wall objects."""
        walls = []
        # Top wall
        walls.append({
            "x": self.x,
            "y": self.y,
            "x2": self.x + self.width,
            "y2": self.y,
            "light": 0,
            "move": 50,
            "sense": 0,
            "dir": 0,
            "door": 0,
            "wall": 1
        })
        # Right wall
        walls.append({
            "x": self.x + self.width,
            "y": self.y,
            "x2": self.x + self.width,
            "y2": self.y + self.height,
            "light": 0,
            "move": 50,
            "sense": 0,
            "dir": 1,
            "door": 0,
            "wall": 1
        })
        # Bottom wall
        walls.append({
            "x": self.x + self.width,
            "y": self.y + self.height,
            "x2": self.x,
            "y2": self.y + self.height,
            "light": 0,
            "move": 50,
            "sense": 0,
            "dir": 2,
            "door": 0,
            "wall": 1
        })
        # Left wall
        walls.append({
            "x": self.x,
            "y": self.y + self.height,
            "x2": self.x,
            "y2": self.y,
            "light": 0,
            "move": 50,
            "sense": 0,
            "dir": 3,
            "door": 0,
            "wall": 1
        })
        return walls


class BSPNode:
    """Node in BSP tree for dungeon generation."""

    def __init__(self, x: int, y: int, width: int, height: int):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.room: Optional[Room] = None
        self.left: Optional['BSPNode'] = None
        self.right: Optional['BSPNode'] = None
        self.is_leaf = True

    def split(self, min_size: int = 20) -> bool:
        """Recursively split node into left and right children."""
        if self.width < min_size * 2 or self.height < min_size * 2:
            return False

        # Choose split direction (vertical or horizontal)
        if self.width > self.height:
            split_vertical = True
        elif self.height > self.width:
            split_vertical = False
        else:
            split_vertical = random.choice([True, False])

        if split_vertical:
            # Split vertically
            split_pos = random.randint(min_size, self.width - min_size)
            self.left = BSPNode(self.x, self.y, split_pos, self.height)
            self.right = BSPNode(self.x + split_pos, self.y, self.width - split_pos, self.height)
        else:
            # Split horizontally
            split_pos = random.randint(min_size, self.height - min_size)
            self.left = BSPNode(self.x, self.y, self.width, split_pos)
            self.right = BSPNode(self.x, self.y + split_pos, self.width, self.height - split_pos)

        self.is_leaf = False
        return self.left.split(min_size) or self.right.split(min_size)

    def get_leaves(self) -> List['BSPNode']:
        """Get all leaf nodes (where rooms will be created)."""
        if self.is_leaf:
            return [self]
        leaves = []
        if self.left:
            leaves.extend(self.left.get_leaves())
        if self.right:
            leaves.extend(self.right.get_leaves())
        return leaves


class ProceduralLayoutGenerator:
    """Generate procedural dungeon layouts."""

    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)

    def generate_dungeon(
        self,
        width: int = 100,
        height: int = 100,
        min_room_size: int = 15,
        max_room_size: int = 30,
        room_count_target: int = 8
    ) -> Tuple[List[Room], List[Dict]]:
        """Generate a dungeon layout using BSP.

        Returns:
            (rooms, walls) where rooms are Room objects and walls are Foundry wall dicts
        """
        # Start with BSP tree
        root = BSPNode(0, 0, width, height)
        root.split(min_size=(width // room_count_target))

        # Create rooms in leaf nodes
        rooms = []
        room_id = 0
        for node in root.get_leaves():
            # Skip nodes that are too small
            if node.width < min_room_size or node.height < min_room_size:
                continue

            # Random room size within bounds
            max_width = min(max_room_size, node.width - 2)
            max_height = min(max_room_size, node.height - 2)

            # Clamp to node size
            min_width = min(min_room_size, node.width - 2)
            min_height = min(min_room_size, node.height - 2)

            if min_width >= max_width:
                room_width = min_width
            else:
                room_width = random.randint(min_width, max_width)

            if min_height >= max_height:
                room_height = min_height
            else:
                room_height = random.randint(min_height, max_height)

            # Place room randomly within the node with padding
            padding = 1
            max_x = node.x + node.width - room_width - padding
            max_y = node.y + node.height - room_height - padding
            min_x = node.x + padding
            min_y = node.y + padding

            room_x = random.randint(min_x, max(min_x, max_x))
            room_y = random.randint(min_y, max(min_y, max_y))

            room = Room(
                x=room_x,
                y=room_y,
                width=room_width,
                height=room_height,
                id=room_id,
                room_type=self._pick_room_type(room_id)
            )
            rooms.append(room)
            node.room = room
            room_id += 1

        # Generate walls
        walls = []
        for room in rooms:
            walls.extend(room.to_foundry_walls())

        # Generate corridors between adjacent rooms
        corridors = self._generate_corridors(rooms)
        walls.extend(corridors)

        return rooms, walls

    def _pick_room_type(self, room_id: int) -> RoomType:
        """Pick a room type based on position in dungeon."""
        if room_id == 0:
            return RoomType.CHAMBER
        elif random.random() < 0.1:
            return RoomType.TREASURE
        elif random.random() < 0.15:
            return RoomType.TRAP
        elif random.random() < 0.2:
            return RoomType.GUARD
        else:
            return RoomType.CHAMBER

    def _generate_corridors(self, rooms: List[Room]) -> List[Dict]:
        """Generate corridors connecting nearby rooms."""
        corridors = []

        # For each room, connect to nearest unconnected neighbor
        connected = set()
        for i, room in enumerate(rooms):
            if i in connected:
                continue

            # Find nearest room
            nearest = None
            nearest_dist = float('inf')
            for j, other in enumerate(rooms):
                if i == j or j in connected:
                    continue
                cx1, cy1 = room.center()
                cx2, cy2 = other.center()
                dist = ((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2) ** 0.5
                if dist < nearest_dist:
                    nearest = j
                    nearest_dist = dist

            if nearest is not None:
                # Create corridor (L-shaped)
                r1 = room
                r2 = rooms[nearest]
                cx1, cy1 = r1.center()
                cx2, cy2 = r2.center()

                # Horizontal then vertical path
                corridor_width = 3
                corridors.extend(self._create_corridor_segment(cx1, cy1, cx2, cy1, corridor_width))
                corridors.extend(self._create_corridor_segment(cx2, cy1, cx2, cy2, corridor_width))

                connected.add(i)
                connected.add(nearest)

        return corridors

    def _create_corridor_segment(self, x1: int, y1: int, x2: int, y2: int, width: int) -> List[Dict]:
        """Create walls for a corridor segment."""
        walls = []
        if x1 == x2:
            # Vertical segment
            start_y = min(y1, y2)
            end_y = max(y1, y2)
            walls.append({"x": x1 - width // 2, "y": start_y, "x2": x1 - width // 2, "y2": end_y, "wall": 1})
            walls.append({"x": x1 + width // 2, "y": start_y, "x2": x1 + width // 2, "y2": end_y, "wall": 1})
        else:
            # Horizontal segment
            start_x = min(x1, x2)
            end_x = max(x1, x2)
            walls.append({"x": start_x, "y": y1 - width // 2, "x2": end_x, "y2": y1 - width // 2, "wall": 1})
            walls.append({"x": start_x, "y": y1 + width // 2, "x2": end_x, "y2": y1 + width // 2, "wall": 1})
        return walls

    def generate_tavern_layout(self, width: int = 60, height: int = 60) -> Tuple[List[Room], List[Dict]]:
        """Generate a tavern interior."""
        # Tavern: bar, seating areas, back room, kitchen
        rooms = [
            Room(5, 5, 20, 15, RoomType.CHAMBER, id=0),      # Main hall
            Room(30, 5, 25, 15, RoomType.CHAMBER, id=1),     # Seating
            Room(5, 25, 15, 20, RoomType.CHAMBER, id=2),     # Bar
            Room(25, 25, 30, 20, RoomType.CHAMBER, id=3),    # Back room
        ]

        walls = []
        for room in rooms:
            walls.extend(room.to_foundry_walls())

        return rooms, walls
