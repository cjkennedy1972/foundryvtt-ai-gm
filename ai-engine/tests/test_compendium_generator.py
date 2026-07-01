#!/usr/bin/env python3
"""
Test suite for CompendiumEncounterGenerator.

These tests assert the *real* D&D 5e encounter-building math, not the skeleton's
original behavior:
  - budget scales with party LEVEL and SIZE (not size alone)
  - the encounter multiplier (monster count) is applied to difficulty
  - selection keeps adjusted XP within budget
  - placement stays within real scene bounds and snaps to the grid
  - the environment filter is a soft filter (falls back, never empties)

Run:
    cd ai-engine && python -m pytest tests/test_compendium_generator.py -v
    OR:
    cd ai-engine && python tests/test_compendium_generator.py
"""

import os
import sys
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.compendium_generator import (
    CompendiumEncounterGenerator, Monster,
    cr_to_xp, calc_budget, encounter_multiplier, adjusted_xp,
)


def _m(name: str, cr: float, env: str = "") -> Monster:
    return Monster(name=name, cr=cr, xp=cr_to_xp(cr), uuid=f"Compendium.dnd5e.monsters.Actor.{name}", environment=env)


# ============================================================================
# Budget math — the core correctness fix (level + size, not size alone)
# ============================================================================

def test_budget_scales_with_level():
    """A level-20 party must get a far larger budget than a level-1 party."""
    low = calc_budget(party_level=1, party_size=4, difficulty="medium")
    high = calc_budget(party_level=20, party_size=4, difficulty="medium")
    assert high > low * 10, f"level scaling broken: {low} -> {high}"


def test_budget_scales_with_size():
    """More party members => proportionally larger budget."""
    four = calc_budget(party_level=5, party_size=4, difficulty="medium")
    eight = calc_budget(party_level=5, party_size=8, difficulty="medium")
    assert eight == four * 2


def test_budget_difficulty_ordering():
    """easy < medium < hard < deadly for the same party."""
    b = lambda d: calc_budget(5, 4, d)
    assert b("trivial") < b("easy") < b("medium") < b("hard") < b("deadly")


def test_budget_known_value():
    """Level 5, 4 PCs, hard = 750/char * 4 = 3000 (DMG table)."""
    assert calc_budget(5, 4, "hard") == 3000


def test_budget_clamps_level():
    """Out-of-range levels clamp to [1, 20] instead of KeyError."""
    assert calc_budget(0, 4, "medium") == calc_budget(1, 4, "medium")
    assert calc_budget(99, 4, "medium") == calc_budget(20, 4, "medium")


# ============================================================================
# Encounter multiplier — the second core fix (count affects difficulty)
# ============================================================================

def test_multiplier_single_monster():
    assert encounter_multiplier(1, party_size=4) == 1.0


def test_multiplier_group():
    """3-6 monsters => x2 for a standard party."""
    assert encounter_multiplier(3, party_size=4) == 2.0
    assert encounter_multiplier(6, party_size=4) == 2.0


def test_multiplier_small_party_harder():
    """A party < 3 shifts one tier up (encounters feel harder)."""
    assert encounter_multiplier(2, party_size=2) > encounter_multiplier(2, party_size=4)


def test_multiplier_large_party_easier():
    """A party >= 6 shifts one tier down (encounters feel easier)."""
    assert encounter_multiplier(3, party_size=6) < encounter_multiplier(3, party_size=4)


def test_adjusted_xp_applies_multiplier():
    """adjusted XP = raw sum * multiplier, not the raw sum."""
    monsters = [_m("Goblin", 0.125) for _ in range(4)]  # 4 * 25 = 100 raw
    raw = sum(m.xp for m in monsters)
    assert adjusted_xp(monsters, 4) == raw * 2.0  # 4 monsters => x2


# ============================================================================
# Selection — adjusted XP must stay within budget
# ============================================================================

def test_selection_respects_adjusted_budget():
    """Selected monsters' ADJUSTED xp must not exceed the budget."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [_m(f"Goblin{i}", 0.125) for i in range(10)] + [_m("Ogre", 2), _m("Troll", 5)]
    budget = calc_budget(3, 4, "medium")  # 180 * 4 = 720

    selected = gen._select_monsters_greedy(candidates, budget, party_size=4, max_creatures=8)

    assert selected, "should select at least one monster"
    assert adjusted_xp(selected, 4) <= budget


def test_selection_prefers_variety():
    """Pass 1 picks distinct names before repeating."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [_m("Goblin", 0.125)] * 5 + [_m("Bugbear", 1)]
    budget = calc_budget(5, 4, "deadly")

    selected = gen._select_monsters_greedy(candidates, budget, party_size=4, max_creatures=4)
    assert len(set(m.name for m in selected)) >= 2


def test_selection_empty_candidates():
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    assert gen._select_monsters_greedy([], 1000, 4, 5) == []


def test_selection_prefers_world_npc_at_equal_cr():
    """At equal CR, an existing campaign NPC is chosen over a generic monster."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    generic = Monster(name="Ogre", cr=2, xp=cr_to_xp(2), uuid="Compendium.x.Actor.ogre", source="compendium")
    villain = Monster(name="Doomed Knight", cr=2, xp=cr_to_xp(2), uuid="Actor.kn", source="world")
    budget = calc_budget(5, 4, "easy")  # room for exactly one CR-2
    selected = gen._select_monsters_greedy([generic, villain], budget, party_size=4, max_creatures=1)
    assert len(selected) == 1
    assert selected[0].is_world_actor, "should pick the campaign NPC at equal CR"


def test_query_parses_both_pools_from_envelope():
    """_query_compendium unwraps the relay envelope and merges world + compendium."""
    import asyncio
    gen = CompendiumEncounterGenerator(foundry=AsyncMock())
    gen.foundry.execute_js = AsyncMock(return_value={"result": {
        "compendium": [{"name": "Goblin", "cr": 0.125, "uuid": "Compendium.x.Actor.g", "source": "compendium"}],
        "world": [{"name": "Lich", "cr": 21, "uuid": "Actor.lich", "source": "world"}],
    }})
    monsters = asyncio.run(gen._query_compendium(party_level=20, environment=None))
    by_name = {m.name: m for m in monsters}
    assert by_name["Goblin"].source == "compendium"
    assert by_name["Lich"].source == "world" and by_name["Lich"].is_world_actor
    assert by_name["Lich"].xp == cr_to_xp(21)


def test_query_handles_bad_envelope():
    """A non-dict payload yields an empty candidate list, not a crash."""
    import asyncio
    gen = CompendiumEncounterGenerator(foundry=AsyncMock())
    gen.foundry.execute_js = AsyncMock(return_value={"result": None})
    assert asyncio.run(gen._query_compendium(party_level=5)) == []


def test_selection_bigger_threats_first():
    """High-level budget should pull in the larger CR monster, not only swarm."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [_m(f"Goblin{i}", 0.125) for i in range(10)] + [_m("Adult Red Dragon", 17)]
    budget = calc_budget(17, 4, "deadly")  # huge

    selected = gen._select_monsters_greedy(candidates, budget, party_size=4, max_creatures=8)
    assert any(m.cr >= 17 for m in selected), "should include the centerpiece threat"


# ============================================================================
# Positioning — within real scene bounds, snapped to grid
# ============================================================================

def test_positioning_within_bounds():
    gen = CompendiumEncounterGenerator(foundry=MagicMock(), scene_width=1000, scene_height=800, grid_size=100)
    placements = gen._position_cluster([_m(f"Goblin{i}", 0.125) for i in range(5)])
    assert len(placements) == 5
    for p in placements:
        assert 0 <= p["x"] <= 1000
        assert 0 <= p["y"] <= 800


def test_positioning_snaps_to_grid():
    gen = CompendiumEncounterGenerator(foundry=MagicMock(), scene_width=1000, scene_height=800, grid_size=100)
    placements = gen._position_cluster([_m(f"Goblin{i}", 0.125) for i in range(4)])
    for p in placements:
        assert p["x"] % 100 == 0, f"x not grid-snapped: {p['x']}"
        assert p["y"] % 100 == 0, f"y not grid-snapped: {p['y']}"


def test_positioning_includes_cr_for_deployment():
    """Placements must carry cr so the executor can import the right stat block."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    placements = gen._position_cluster([_m("Ogre", 2)])
    assert placements[0]["cr"] == 2
    assert placements[0]["uuid"].startswith("Compendium.")


def test_positioning_small_scene_no_overflow():
    """A tiny scene must not push tokens off-canvas."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock(), scene_width=200, scene_height=200, grid_size=100)
    placements = gen._position_cluster([_m(f"G{i}", 0.125) for i in range(6)])
    for p in placements:
        assert 0 <= p["x"] <= 200
        assert 0 <= p["y"] <= 200


# ============================================================================
# Environment filter — soft, never empties the pool
# ============================================================================

def test_environment_filter_restricts_when_enough_matches():
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = (
        [_m(f"Cave{i}", 1, env="underdark") for i in range(4)]
        + [_m("Pixie", 0.25, env="forest")]
    )
    filtered = gen._apply_environment_filter(candidates, "underdark")
    assert len(filtered) == 4
    assert all("underdark" in m.environment for m in filtered)


def test_environment_filter_falls_back_when_too_few():
    """Fewer than 3 matches => keep the full pool, don't return an empty set."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [_m("Pixie", 0.25, env="forest"), _m("Goblin", 0.125, env="")]
    filtered = gen._apply_environment_filter(candidates, "underdark")
    assert filtered == candidates


def test_environment_filter_none_passthrough():
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [_m("Goblin", 0.125)]
    assert gen._apply_environment_filter(candidates, None) == candidates


# ============================================================================
# Full generation — structure + guarantees
# ============================================================================

def _gen_with_pool(pool):
    gen = CompendiumEncounterGenerator(foundry=AsyncMock(), scene_width=1000, scene_height=800, grid_size=100)
    # Bypass JS: feed the candidate pool directly.
    async def fake_query(party_level, environment=None):
        return list(pool)
    gen._query_compendium = fake_query
    return gen


def test_generate_structure_and_budget_guarantee():
    import asyncio, random
    pool = [_m(f"Goblin{i}", 0.125) for i in range(10)] + [_m("Ogre", 2), _m("Troll", 5)]
    gen = _gen_with_pool(pool)

    result = asyncio.run(gen.generate(party_level=5, party_size=4, difficulty="medium",
                                      rng=random.Random(0)))

    for key in ("creatures", "placements", "budget", "adjusted_xp", "notes", "shape", "target_count"):
        assert key in result
    assert result["adjusted_xp"] <= result["budget"]
    assert len(result["placements"]) == len(result["creatures"])


def test_generate_level_scaling_uses_more_xp():
    """Same pool + same shape, higher level => more adjusted XP committed."""
    import asyncio, random
    pool = [_m(f"Goblin{i}", 0.125) for i in range(20)] + [_m("Ogre", 2), _m("Troll", 5), _m("Giant", 9)]
    gen = _gen_with_pool(pool)

    low = asyncio.run(gen.generate(party_level=1, party_size=4, difficulty="medium", shape="group",
                                   rng=random.Random(0)))
    high = asyncio.run(gen.generate(party_level=12, party_size=4, difficulty="medium", shape="group",
                                    rng=random.Random(0)))
    assert high["adjusted_xp"] > low["adjusted_xp"]


def test_generate_empty_pool():
    import asyncio, random
    gen = _gen_with_pool([])
    result = asyncio.run(gen.generate(party_level=5, party_size=4, difficulty="medium",
                                      rng=random.Random(0)))
    assert result["creatures"] == []
    assert "Empty encounter" in result["notes"]


# ============================================================================
# Encounter shape — random/situational group sizing
# ============================================================================

def test_choose_shape_deadly_favors_solo_over_easy():
    """Over many draws, deadly yields solo far more often than easy does."""
    import random
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    rng = random.Random(42)
    deadly_solo = sum(gen._choose_shape("deadly", rng)[0] == "solo" for _ in range(400))
    easy_solo = sum(gen._choose_shape("easy", rng)[0] == "solo" for _ in range(400))
    assert deadly_solo > easy_solo


def test_choose_shape_count_in_range():
    import random
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    rng = random.Random(1)
    for _ in range(50):
        shape, count = gen._choose_shape("medium", rng)
        lo, hi = __import__("combat.compendium_generator", fromlist=["SHAPES"]).SHAPES[shape]
        assert lo <= count <= hi


def test_select_for_count_horde_gets_many():
    """A horde target with a big budget and cheap monsters yields several."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [_m(f"Goblin{i}", 0.125) for i in range(20)]  # 25 XP each
    budget = calc_budget(10, 4, "deadly")  # large
    selected = gen._select_for_count(candidates, budget, party_size=4, target_count=7)
    assert 5 <= len(selected) <= 7


def test_select_for_count_solo_gets_one():
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [_m(f"Goblin{i}", 0.125) for i in range(10)] + [_m("Dragon", 10)]
    budget = calc_budget(10, 4, "deadly")
    selected = gen._select_for_count(candidates, budget, party_size=4, target_count=1)
    assert len(selected) == 1


def test_generate_forced_shape_solo():
    import asyncio, random
    pool = [_m(f"Goblin{i}", 0.125) for i in range(10)] + [_m("Ogre", 2), _m("Troll", 5)]
    gen = _gen_with_pool(pool)
    result = asyncio.run(gen.generate(party_level=8, party_size=4, difficulty="hard",
                                      shape="solo", rng=random.Random(0)))
    assert result["shape"] == "solo"
    assert len(result["creatures"]) == 1


# ============================================================================
# Main entry point (no pytest required)
# ============================================================================

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(fns)} compendium generator tests passed!")
