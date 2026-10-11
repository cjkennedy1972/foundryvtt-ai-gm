"""Coverage-gap tests: settlement_integration, compendium_generator, obsidian_sync."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.settlement_integration import SettlementIntegration
from combat.compendium_generator import (
    CompendiumEncounterGenerator,
    Monster,
    cr_to_xp,
    encounter_multiplier,
)


# ─── settlement_integration ───────────────────────────────────────────────


def _integration(generate):
    si = SettlementIntegration(MagicMock())
    si.generator = SimpleNamespace(generate=generate)
    return si


def test_settlements_none_found_returns_empty_without_calling_generator():
    gen = AsyncMock()
    out = asyncio.run(_integration(gen).generate_settlements_from_campaign({"locations": "x", "scenes": [1]}, "ctx"))
    assert out == {}
    gen.assert_not_awaited()


def test_settlement_failure_is_skipped_and_others_still_generated():
    async def gen(settlement_name, campaign_context, population_hint):
        if settlement_name == "Bad":
            raise RuntimeError("llm down")
        return SimpleNamespace(id=f"id-{settlement_name}", hint=population_hint, ctx=campaign_context)

    data = {"locations": [
        {"name": "Bad", "type": "Town"}, {"name": "Good", "type": "village", "description": "A Small place"},
        {"name": "Cave", "type": "dungeon"}, "junk",
    ]}
    out = asyncio.run(_integration(gen).generate_settlements_from_campaign(data, "CTX"))
    assert list(out) == ["id-Good"]
    assert out["id-Good"].hint == "small village" and out["id-Good"].ctx == "CTX"


def test_settlement_names_fall_back_to_scene_descriptions_and_respect_limit():
    si = _integration(AsyncMock())
    scenes = [{"name": f"S{i}", "description": "A busy MARKET square"} for i in range(5)]
    scenes += [{"name": "Dull", "description": "a swamp"}, "junk"]
    names = si._extract_settlement_names({"locations": [], "scenes": scenes}, limit=2)
    assert len(names) == 2 and set(names) <= {f"S{i}" for i in range(5)}
    assert si._extract_settlement_names({"scenes": [{"name": "D", "description": "swamp"}]}) == []
    assert si._extract_settlement_names({"scenes": "x"}) == []


@pytest.mark.parametrize("desc,expected", [
    ("The great capital", "large city"),
    ("A tiny hamlet", "small village"),
    ("A river crossing", "trade town"),
    ("Nothing notable", "trade town"),
])
def test_population_hint_from_description(desc, expected):
    si = _integration(AsyncMock())
    data = {"locations": [{"name": "X", "description": desc}, "junk"]}
    assert si._infer_population_hint(data, "X") == expected
    assert si._infer_population_hint(data, "Unknown") == "trade town"
    assert si._infer_population_hint({"locations": None}, "X") == "trade town"


# ─── compendium_generator ─────────────────────────────────────────────────


@pytest.mark.parametrize("count,party,mult", [
    (0, 4, 1.0), (7, 4, 2.5), (10, 4, 2.5), (11, 4, 3.0), (14, 4, 3.0), (15, 4, 4.0),
    (1, 2, 1.5),      # small party shifts a tier up
    (3, 6, 1.5),      # large party shifts a tier down
    (1, 6, 1.0),      # clamped at the lowest tier
    (20, 2, 4.0),     # clamped at the highest tier
])
def test_encounter_multiplier_tiers(count, party, mult):
    assert encounter_multiplier(count, party) == mult


def _gen(execute_js):
    return CompendiumEncounterGenerator(SimpleNamespace(execute_js=execute_js))


def test_query_compendium_merges_sources_and_drops_unknown_cr_and_junk():
    payload = {
        "world": [{"name": "Strahd", "cr": 15, "uuid": "Actor.s", "source": "world"}, "junk",
                  {"name": "Weird", "cr": 77, "uuid": "Actor.w"}],
        "compendium": [{"name": "Goblin", "cr": 0.25, "uuid": "C.g", "size": "sm", "environment": None}, None],
    }
    gen = _gen(AsyncMock(return_value={"result": payload}))
    out = asyncio.run(gen._query_compendium(5))
    assert [(m.name, m.xp, m.source, m.size, m.environment) for m in out] == [
        ("Strahd", cr_to_xp(15), "world", "med", ""),
        ("Goblin", cr_to_xp(0.25), "compendium", "sm", ""),
    ]
    assert "<= 8" in gen.foundry.execute_js.await_args.args[0] and "<= 10" in gen.foundry.execute_js.await_args.args[0]


def test_query_compendium_returns_empty_on_bad_payload_or_exception():
    assert asyncio.run(_gen(AsyncMock(return_value={"result": ["x"]}))._query_compendium(3)) == []
    assert asyncio.run(_gen(AsyncMock(side_effect=RuntimeError("ws")))._query_compendium(3)) == []
    out = asyncio.run(_gen(AsyncMock(return_value={"compendium": None, "world": None}))._query_compendium(3))
    assert out == []   # dict without a "result" key is not a payload with monsters


def _mon(name, cr, source="compendium"):
    return Monster(name=name, cr=cr, xp=cr_to_xp(cr), uuid=f"U.{name}", source=source)


def test_select_for_count_skips_duplicate_names_in_variety_pass_then_repeats():
    gen = _gen(None)
    pool = [_mon("Goblin", 0.25), _mon("Goblin", 0.25), _mon("Wolf", 0.25)]
    sel = gen._select_for_count(pool, budget=10_000, party_size=4, target_count=3)
    assert sorted(m.name for m in sel) == ["Goblin", "Goblin", "Wolf"]
    assert gen._select_for_count([], 100, 4, 2) == []


def test_select_for_count_prefers_world_actor_at_equal_fit():
    gen = _gen(None)
    sel = gen._select_for_count([_mon("A", 1), _mon("B", 1, "world")], budget=10_000, party_size=4, target_count=1)
    assert [m.name for m in sel] == ["B"]


def test_select_for_count_unattainable_target_keeps_what_fits_and_nothing_affordable_is_empty():
    gen = _gen(None)
    # budget 100 XP: Ogre (450xp) never fits; two Rats (2 x 25xp x 1.5) fit, a horde of 20 does not.
    pool = [_mon("Rat", 0.125), _mon("Ogre", 2)]
    sel = gen._select_for_count(pool, budget=100, party_size=4, target_count=20)
    assert [m.name for m in sel] == ["Rat", "Rat"]
    assert gen._select_for_count([_mon("Dragon", 20)], budget=10, party_size=4, target_count=2) == []
