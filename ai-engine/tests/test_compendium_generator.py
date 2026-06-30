#!/usr/bin/env python3
"""
Test suite for CompendiumEncounterGenerator.

Tests verify:
1. Monster selection fits XP budget
2. Difficulty ratings are respected
3. Tactical positioning spreads creatures
4. Greedy selection avoids duplicates first

Run:
    cd ai-engine && python -m pytest tests/test_compendium_generator.py -v
    OR:
    cd ai-engine && python tests/test_compendium_generator.py
"""

import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.compendium_generator import CompendiumEncounterGenerator, Monster
from combat.difficulty import DynamicDifficulty, EncounterDifficulty


def _make_monster(name: str, cr: float) -> Monster:
    """Helper to create a test monster."""
    xp_values = {0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800}
    xp = xp_values.get(cr, 200)
    return Monster(name=name, cr=cr, xp=xp, uuid=f"uuid_{name}")


# ============================================================================
# Basic Functionality Tests
# ============================================================================

def test_monster_creation():
    """Monster dataclass initializes correctly."""
    m = Monster(name="Goblin", cr=0.125, xp=25, uuid="test_uuid")
    assert m.name == "Goblin"
    assert m.cr == 0.125
    assert m.xp == 25


def test_generator_initialization():
    """CompendiumEncounterGenerator initializes with defaults."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    assert gen.scene_width == 800
    assert gen.scene_height == 600
    assert len(gen.XP_VALUES) > 0


def test_generator_with_custom_scene_size():
    """CompendiumEncounterGenerator accepts custom scene dimensions."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock(), scene_width=1024, scene_height=768)
    assert gen.scene_width == 1024
    assert gen.scene_height == 768


# ============================================================================
# XP Budget and Selection Tests
# ============================================================================

def test_greedy_selection_fits_budget():
    """Monsters selected should fit within XP budget."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())

    # Create test monsters: 3 goblins (25 XP each), 1 bugbear (450 XP)
    candidates = [
        _make_monster("Goblin", 0.125),   # 25 XP
        _make_monster("Goblin", 0.125),   # 25 XP
        _make_monster("Goblin", 0.125),   # 25 XP
        _make_monster("Bugbear", 1),      # 200 XP
    ]

    budget = 300  # Should fit 1 Bugbear + 2 Goblins = 200 + 50 = 250
    selected = gen._select_monsters_greedy(candidates, budget, max_creatures=5)

    total_xp = sum(m.xp for m in selected)
    assert total_xp <= budget, f"Selection {total_xp} exceeds budget {budget}"
    assert len(selected) > 0, "Should select at least one monster"


def test_greedy_selection_prefers_variety():
    """Greedy selection should pick different monster types first."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())

    # 5 goblins, 1 bugbear
    candidates = [
        _make_monster("Goblin", 0.125),
        _make_monster("Goblin", 0.125),
        _make_monster("Goblin", 0.125),
        _make_monster("Goblin", 0.125),
        _make_monster("Goblin", 0.125),
        _make_monster("Bugbear", 1),
    ]

    budget = 500
    selected = gen._select_monsters_greedy(candidates, budget, max_creatures=3)

    # Should prioritize the Bugbear (variety) and pick only 1 Goblin in first pass
    unique_names = set(m.name for m in selected)
    assert len(unique_names) >= 2, "Should have variety in selection"


def test_greedy_selection_respects_max_creatures():
    """Selection should not exceed max_creatures limit."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())

    candidates = [_make_monster(f"Goblin_{i}", 0.125) for i in range(10)]
    budget = 10000
    max_creatures = 4

    selected = gen._select_monsters_greedy(candidates, budget, max_creatures)
    assert len(selected) <= max_creatures


# ============================================================================
# Positioning Tests
# ============================================================================

def test_positioning_spreads_creatures():
    """Tactical positioning should spread creatures across the map."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock(), scene_width=800, scene_height=600)

    monsters = [
        _make_monster("Goblin", 0.125),
        _make_monster("Goblin", 0.125),
        _make_monster("Ogre", 2),
        _make_monster("Ogre", 2),
    ]

    placements = gen._position_tactically(monsters)

    assert len(placements) == len(monsters), "All monsters should be placed"

    # Check that placements are within bounds
    for p in placements:
        assert 0 <= p["x"] <= gen.scene_width, f"X coordinate out of bounds: {p['x']}"
        assert 0 <= p["y"] <= gen.scene_height, f"Y coordinate out of bounds: {p['y']}"


def test_positioning_separates_front_and_back():
    """Front-line and back-line creatures should be separated."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())

    monsters = [
        _make_monster("Goblin", 0.125),      # Small, front
        _make_monster("Goblin", 0.125),      # Small, front
        _make_monster("Hydra", 5),           # Large, back
        _make_monster("Hydra", 5),           # Large, back
    ]

    placements = gen._position_tactically(monsters)

    # Extract X coordinates
    front_creatures = [p for p in placements if p["x"] < 300]
    back_creatures = [p for p in placements if p["x"] >= 300]

    assert len(front_creatures) > 0, "Should have front-line creatures"
    assert len(back_creatures) > 0, "Should have back-line creatures"


def test_positioning_spreads_y_axis():
    """Creatures should be spread across Y axis to avoid stacking."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock(), scene_height=600)

    monsters = [_make_monster(f"Goblin_{i}", 0.125) for i in range(3)]
    placements = gen._position_tactically(monsters)

    y_coords = [p["y"] for p in placements]
    # Not all Y coords should be identical
    assert len(set(y_coords)) > 1, "Creatures should be spread across Y axis"


def test_positioning_includes_metadata():
    """Placements should include uuid, name, and hidden flag."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())

    monsters = [_make_monster("Goblin", 0.125)]
    placements = gen._position_tactically(monsters)

    assert len(placements) == 1
    p = placements[0]
    assert "uuid" in p
    assert "name" in p
    assert "x" in p
    assert "y" in p
    assert "hidden" in p


# ============================================================================
# Notes Generation Tests
# ============================================================================

def test_notes_include_creature_count():
    """Generated notes should include creature count."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())

    monsters = [
        _make_monster("Goblin", 0.125),
        _make_monster("Bugbear", 1),
    ]
    placements = [
        {"x": 100, "y": 100},
        {"x": 100, "y": 300},
    ]

    notes = gen._generate_notes(monsters, placements)
    assert "2" in notes or "two" in notes.lower(), "Notes should mention creature count"


def test_notes_empty_encounter():
    """Notes for empty encounter should be handled gracefully."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())

    notes = gen._generate_notes([], [])
    assert "Empty" in notes or "No" in notes.lower()


# ============================================================================
# Async Generation Tests
# ============================================================================

async def test_generate_returns_valid_structure():
    """Full generation should return valid encounter structure."""
    gen = CompendiumEncounterGenerator(foundry=AsyncMock())

    # Mock the JavaScript query result
    mock_monsters = [
        {"name": "Goblin", "cr": 0.125, "uuid": "uuid1"},
        {"name": "Bugbear", "cr": 1, "uuid": "uuid2"},
    ]
    gen.foundry.execute_js = AsyncMock(return_value=mock_monsters)

    result = await gen.generate(
        party_level=3,
        party_size=4,
        difficulty="medium",
        max_creatures=2
    )

    assert "creatures" in result
    assert "placements" in result
    assert "total_xp" in result
    assert "difficulty_rating" in result
    assert "notes" in result


async def test_generate_respects_difficulty():
    """Generation should respect difficulty levels."""
    gen = CompendiumEncounterGenerator(foundry=AsyncMock())

    gen.foundry.execute_js = AsyncMock(return_value=[
        {"name": "Goblin", "cr": 0.125, "uuid": "uuid1"},
    ])

    result_easy = await gen.generate(3, 4, difficulty="easy")
    result_hard = await gen.generate(3, 4, difficulty="hard")

    # Hard should have higher total XP than easy
    assert result_hard["total_xp"] >= result_easy["total_xp"]


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import asyncio

    print("=== Basic Functionality Tests ===")
    test_monster_creation()
    print("PASS  monster creation")
    test_generator_initialization()
    print("PASS  generator initialization")
    test_generator_with_custom_scene_size()
    print("PASS  generator with custom scene size")

    print("\n=== XP Budget and Selection Tests ===")
    test_greedy_selection_fits_budget()
    print("PASS  greedy selection fits budget")
    test_greedy_selection_prefers_variety()
    print("PASS  greedy selection prefers variety")
    test_greedy_selection_respects_max_creatures()
    print("PASS  greedy selection respects max creatures")

    print("\n=== Positioning Tests ===")
    test_positioning_spreads_creatures()
    print("PASS  positioning spreads creatures")
    test_positioning_separates_front_and_back()
    print("PASS  positioning separates front and back")
    test_positioning_spreads_y_axis()
    print("PASS  positioning spreads Y axis")
    test_positioning_includes_metadata()
    print("PASS  positioning includes metadata")

    print("\n=== Notes Generation Tests ===")
    test_notes_include_creature_count()
    print("PASS  notes include creature count")
    test_notes_empty_encounter()
    print("PASS  notes handle empty encounter")

    print("\n=== Async Generation Tests ===")
    asyncio.run(test_generate_returns_valid_structure())
    print("PASS  generate returns valid structure")
    asyncio.run(test_generate_respects_difficulty())
    print("PASS  generate respects difficulty")

    print("\nAll compendium generator tests passed!")
