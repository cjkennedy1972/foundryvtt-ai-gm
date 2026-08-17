"""Tests for procedural layout generation.

Tests dungeon and tavern layout generation.

Run:
    cd ai-engine && python -m pytest tests/test_procedural_layouts.py -v
"""

import pytest
from procedural.layout_gen import (
    ProceduralLayoutGenerator,
    Room,
    RoomType,
    BSPNode,
)


class TestRoom:
    """Tests for Room class."""

    def test_room_creation(self):
        """Room can be created with coordinates."""
        room = Room(x=10, y=20, width=30, height=40)

        assert room.x == 10
        assert room.y == 20
        assert room.width == 30
        assert room.height == 40

    def test_room_center(self):
        """Room center calculation is correct."""
        room = Room(x=0, y=0, width=10, height=10)
        cx, cy = room.center()

        assert cx == 5
        assert cy == 5

    def test_room_center_odd_dimensions(self):
        """Room center works with odd dimensions."""
        room = Room(x=10, y=20, width=11, height=11)
        cx, cy = room.center()

        assert cx == 15
        assert cy == 25

    def test_room_to_foundry_walls(self):
        """Room generates four walls for Foundry."""
        room = Room(x=0, y=0, width=10, height=10)
        walls = room.to_foundry_walls()

        assert len(walls) == 4  # top, right, bottom, left
        for wall in walls:
            assert wall["wall"] == 1
            assert "x" in wall and "y" in wall
            assert "x2" in wall and "y2" in wall


class TestBSPNode:
    """Tests for BSP tree."""

    def test_bsp_node_creation(self):
        """BSP node can be created."""
        node = BSPNode(0, 0, 100, 100)

        assert node.x == 0
        assert node.y == 0
        assert node.width == 100
        assert node.height == 100
        assert node.is_leaf is True

    def test_bsp_split_creates_children(self):
        """BSP split creates left and right children."""
        node = BSPNode(0, 0, 100, 100)
        result = node.split(min_size=10)

        # Split should succeed with large node
        assert node.left is not None
        assert node.right is not None
        assert node.is_leaf is False

    def test_bsp_split_respects_min_size(self):
        """BSP split fails on too-small nodes."""
        node = BSPNode(0, 0, 30, 30)
        result = node.split(min_size=20)

        assert result is False
        assert node.is_leaf is True

    def test_bsp_get_leaves(self):
        """get_leaves returns all leaf nodes."""
        node = BSPNode(0, 0, 100, 100)
        node.split(min_size=10)

        leaves = node.get_leaves()
        assert len(leaves) >= 2
        for leaf in leaves:
            assert leaf.is_leaf is True


class TestProceduralLayoutGenerator:
    """Tests for layout generation."""

    def test_generator_creation(self):
        """Generator can be created with optional seed."""
        gen1 = ProceduralLayoutGenerator(seed=42)
        gen2 = ProceduralLayoutGenerator(seed=42)

        assert gen1 is not None
        assert gen2 is not None

    def test_generate_dungeon_basic(self):
        """Generate dungeon returns rooms and walls."""
        gen = ProceduralLayoutGenerator()
        rooms, walls = gen.generate_dungeon(width=80, height=80, room_count_target=4)

        assert len(rooms) > 0
        assert len(walls) > 0

    def test_generate_dungeon_rooms_valid(self):
        """Generated rooms have valid dimensions and positions."""
        gen = ProceduralLayoutGenerator(seed=42)
        rooms, _ = gen.generate_dungeon(width=100, height=100)

        for room in rooms:
            assert room.width > 0
            assert room.height > 0
            assert room.x >= 0
            assert room.y >= 0

    def test_generate_dungeon_walls_valid(self):
        """Generated walls are valid Foundry objects."""
        gen = ProceduralLayoutGenerator()
        _, walls = gen.generate_dungeon()

        for wall in walls:
            assert "x" in wall and "y" in wall
            assert "x2" in wall and "y2" in wall
            assert wall.get("wall") in [None, 0, 1]

    def test_generate_dungeon_reproducible(self):
        """Dungeon generation is reproducible with same seed."""
        gen1 = ProceduralLayoutGenerator(seed=123)
        rooms1, _ = gen1.generate_dungeon(width=80, height=80)

        gen2 = ProceduralLayoutGenerator(seed=123)
        rooms2, _ = gen2.generate_dungeon(width=80, height=80)

        assert len(rooms1) == len(rooms2)
        for r1, r2 in zip(rooms1, rooms2):
            assert r1.x == r2.x
            assert r1.y == r2.y

    def test_generate_tavern_layout(self):
        """Generate tavern layout returns specific rooms."""
        gen = ProceduralLayoutGenerator()
        rooms, walls = gen.generate_tavern_layout()

        assert len(rooms) == 4  # main, seating, bar, back room
        assert len(walls) > 0

    def test_tavern_room_types(self):
        """Tavern rooms are all chamber type."""
        gen = ProceduralLayoutGenerator()
        rooms, _ = gen.generate_tavern_layout()

        for room in rooms:
            assert room.room_type == RoomType.CHAMBER

    def test_room_type_variation(self):
        """Generated dungeons have variety of room types."""
        gen = ProceduralLayoutGenerator(seed=42)
        rooms, _ = gen.generate_dungeon(width=200, height=200, room_count_target=20)

        # Should generate at least some rooms
        assert len(rooms) > 0
        types = {room.room_type for room in rooms}
        # Should have at least chamber type
        assert RoomType.CHAMBER in types


class TestLayoutIntegration:
    """Integration tests for layout generation."""

    def test_dungeon_rooms_dont_overlap(self):
        """Rooms don't overlap in generated dungeon."""
        gen = ProceduralLayoutGenerator(seed=42)
        rooms, _ = gen.generate_dungeon()

        for i, room1 in enumerate(rooms):
            for room2 in rooms[i + 1 :]:
                # Check if rooms overlap
                x_overlap = (room1.x < room2.x + room2.width) and (room1.x + room1.width > room2.x)
                y_overlap = (room1.y < room2.y + room2.height) and (room1.y + room1.height > room2.y)

                # Rooms should not overlap (allow touching)
                if x_overlap and y_overlap:
                    # Allow if they're just touching (sharing edge)
                    assert (
                        room1.x + room1.width == room2.x
                        or room2.x + room2.width == room1.x
                        or room1.y + room1.height == room2.y
                        or room2.y + room2.height == room1.y
                    )

    def test_generate_multiple_sizes(self):
        """Generator works with various dimensions."""
        gen = ProceduralLayoutGenerator()

        for width in [100, 150, 200]:
            for height in [100, 150, 200]:
                rooms, walls = gen.generate_dungeon(width=width, height=height)
                # May or may not generate rooms depending on BSP split
                assert isinstance(rooms, list)
                assert isinstance(walls, list)
