#!/usr/bin/env python3
"""procedural/treasures.py — reachable from the generate_treasure action,
referenced by no test.

The gold "variance" did nothing. random.randint(-20, 20) // 10 yields -2..2,
so a CR 5 hoard at level 5 came out between 548 and 550 every time: four
gold of spread on a base of 550, in a random treasure generator. Two
encounters of the same CR produced the same purse.

Run:
    cd ai-engine && python -m pytest tests/test_treasure_generation.py -v
"""

import os
import statistics
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from procedural.treasures import TreasureGenerator


@pytest.fixture
def gen():
    return TreasureGenerator()


# ── gold ──────────────────────────────────────────────────────────────────

def test_two_hoards_of_the_same_cr_are_not_the_same_purse(gen):
    rolls = {gen._generate_gold(5, 5) for _ in range(200)}

    assert len(rolls) > 10, f"only {len(rolls)} distinct values: {sorted(rolls)}"


def test_the_spread_is_a_share_of_the_hoard_not_a_few_coins(gen):
    rolls = [gen._generate_gold(5, 5) for _ in range(400)]

    spread = max(rolls) - min(rolls)
    assert spread > 0.1 * statistics.mean(rolls), f"spread {spread} on mean {statistics.mean(rolls):.0f}"


def test_gold_still_scales_with_challenge_rating(gen):
    low = statistics.mean(gen._generate_gold(1, 5) for _ in range(200))
    high = statistics.mean(gen._generate_gold(10, 5) for _ in range(200))

    assert high > low * 3


def test_gold_still_scales_with_party_level(gen):
    """A margin wider than the variance. `high > low` alone passes by luck
    once the spread is +/-20%: at CR 2 the level term takes the base from
    210 to 400, so anything under about 1.4x is noise."""
    low = statistics.mean(gen._generate_gold(2, 1) for _ in range(300))
    high = statistics.mean(gen._generate_gold(2, 20) for _ in range(300))

    assert high > low * 1.4, f"level 1 {low:.0f} vs level 20 {high:.0f}"


def test_a_trivial_encounter_still_yields_something(gen):
    assert all(gen._generate_gold(0, 1) >= 10 for _ in range(100))


def test_gold_is_never_negative_however_the_variance_falls(gen):
    assert all(gen._generate_gold(0.125, 1) > 0 for _ in range(200))


# ── value parsing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("100gp", 100),
    ("10000gp", 10000),
    ("100-500gp", 300),
    ("10-50gp", 30),
])
def test_values_are_read_including_ranges(gen, text, expected):
    assert gen._estimate_value(text) == expected


# ── gems scale with CR ────────────────────────────────────────────────────

def test_a_low_cr_hoard_has_no_thousand_gold_gems(gen):
    for _ in range(100):
        for gem in gen._generate_gems(1):
            assert gem["value"] in ("100gp", "50gp", "10gp")


def test_a_high_cr_hoard_has_the_good_stones(gen):
    seen = {gem["value"] for _ in range(100) for gem in gen._generate_gems(8)}

    assert seen <= {"1000gp", "500gp"}


def test_every_gem_is_named_from_its_own_value_band(gen):
    for _ in range(100):
        for gem in gen._generate_gems(6):
            assert gem["name"] in TreasureGenerator.GEM_TYPES[gem["value"]]


# ── magic items scale with CR and level ───────────────────────────────────

def test_a_cantrip_tier_hoard_holds_nothing_legendary(gen):
    seen = {m["rarity"] for _ in range(200) for m in gen._generate_magical_items(0.25, 1)}

    assert seen <= {"common"}


def test_a_high_tier_hoard_can_hold_the_rare_things(gen):
    seen = {m["rarity"] for _ in range(200) for m in gen._generate_magical_items(8, 12)}

    assert seen <= {"rare", "very_rare"}
    assert seen, "no magic item appeared in 200 high-CR hoards"


# ── the whole thing ───────────────────────────────────────────────────────

def test_the_total_is_the_sum_of_what_is_in_the_hoard(gen):
    t = gen.generate(5, 5)

    parts = (
        t.gold
        + sum(gen._estimate_value(g["value"]) for g in t.gems)
        + sum(gen._estimate_value(i["value"]) for i in t.items)
        + sum(gen._estimate_value(m["value"]) for m in t.magical_items)
    )
    assert t.total_value == parts


def test_a_generated_hoard_has_every_field_the_executor_reads(gen):
    """execute_generate_treasure reads all five."""
    t = gen.generate(3, 5)

    assert isinstance(t.gold, int)
    for collection in (t.gems, t.items, t.magical_items):
        assert isinstance(collection, list)
    assert t.total_value >= t.gold
