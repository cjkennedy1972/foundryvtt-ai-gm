#!/usr/bin/env python3
"""Parameters the model is invited to set, that changed nothing.

The system prompt's action table tells the model it can ask for a treasure
of a given rarity, an NPC with a role and a faction, and a themed quest.
Every one of those arguments was accepted, validated against the schema,
bound to the executor, and then never read. The model asked for a Blacksmith
of the Iron Guild and got a random Tiefling Barbarian; it asked for legendary
loot and got whatever the CR table rolled.

`ruff --select ARG001,ARG002` flags 141 unused arguments across the engine.
Most are structural (`source`, `state`, `mods` on uniform signatures). These
four were not: each one is exposed in the JSON schema, so it reaches the
model as a promise.

Run:
    cd ai-engine && python -m pytest tests/test_generation_parameters_are_honoured.py -v
"""

import os
import random
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import generation_actions
from procedural.quests import QuestGenerator
from procedural.treasures import TreasureGenerator


def _foundry():
    """A connected client that records what was written to the world."""
    f = MagicMock()
    f.is_connected = True
    f.create_entity = AsyncMock(return_value={"uuid": "JournalEntry.abc"})
    f.place_token = AsyncMock(return_value={"id": "tok1"})
    f.get_scene_tokens = AsyncMock(return_value=[])
    return f


def _entity_payload(foundry):
    return foundry.create_entity.await_args.args[1]


# ── generate_npc: role and faction ────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_requested_role_reaches_the_result():
    out = await generation_actions.execute_generate_npc(role="Blacksmith")

    assert out.get("error") is None, out
    assert out["npc"].get("role") == "Blacksmith"


@pytest.mark.asyncio
async def test_a_requested_faction_reaches_the_result():
    out = await generation_actions.execute_generate_npc(faction="The Iron Guild")

    assert out.get("error") is None, out
    assert out["npc"].get("faction") == "The Iron Guild"


@pytest.mark.asyncio
async def test_the_role_and_faction_reach_the_actor_sheet():
    """A GM opening the sheet should see who they asked for."""
    foundry = _foundry()

    await generation_actions.execute_generate_npc(
        role="Harbourmaster", faction="Dockers' Union", foundry=foundry
    )

    bio = _entity_payload(foundry)["system"]["details"]["biography"]["value"]
    assert "Harbourmaster" in bio
    assert "Dockers&#x27; Union" in bio or "Dockers' Union" in bio


@pytest.mark.asyncio
async def test_an_npc_asked_for_nothing_in_particular_still_generates():
    out = await generation_actions.execute_generate_npc()

    assert out.get("error") is None, out
    assert out["npc"]["name"]
    assert "role" not in out["npc"]


@pytest.mark.asyncio
async def test_a_role_containing_markup_is_escaped_into_the_sheet():
    foundry = _foundry()

    await generation_actions.execute_generate_npc(
        role="<script>alert(1)</script>", foundry=foundry
    )

    bio = _entity_payload(foundry)["system"]["details"]["biography"]["value"]
    assert "<script>" not in bio


# ── generate_quest: theme ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_requested_theme_reaches_the_result():
    out = await generation_actions.execute_generate_quest(theme="a dangerous monster")

    assert out.get("error") is None, out
    assert out["quest"].get("theme") == "a dangerous monster"


@pytest.mark.asyncio
async def test_the_executor_steers_the_quest_and_does_not_just_echo_the_theme():
    """Recording the theme on the result is not the same as using it."""
    random.seed(23)

    out = await generation_actions.execute_generate_quest(theme="monster")

    assert out.get("error") is None, out
    assert "monster" in out["quest"]["objective"].lower()


def test_a_theme_steers_the_quest_toward_matching_content():
    """"monster" should not produce twenty quests about feuding factions."""
    gen = QuestGenerator()
    rng = random.Random(7)
    random.seed(7)

    hits = sum(
        1 for _ in range(20)
        if "monster" in gen.generate(theme="monster").objective.lower()
    )

    assert hits == 20, f"the theme steered {hits}/20 quests"
    assert rng  # keep the seed explicit for the reader


def test_a_theme_that_matches_nothing_still_produces_a_quest():
    gen = QuestGenerator()

    quest = gen.generate(theme="interdimensional tax audit")

    assert quest.title and quest.objective and quest.reward


@pytest.mark.asyncio
async def test_the_theme_reaches_the_journal_entry():
    foundry = _foundry()

    await generation_actions.execute_generate_quest(theme="undead", foundry=foundry)

    content = _entity_payload(foundry)["pages"][0]["text"]["content"]
    assert "undead" in content


# ── generate_treasure: rarity_preference ──────────────────────────────────

def test_a_rarity_preference_selects_from_that_tier():
    """A CR 1 hoard rolls common/uncommon; asking for rare should give rare."""
    gen = TreasureGenerator()
    random.seed(3)

    rarities = set()
    for _ in range(40):
        t = gen.generate(1.0, rarity="rare")
        rarities.update(m["rarity"] for m in t.magical_items)

    assert rarities <= {"rare"}, f"asked for rare, got {rarities}"
    assert rarities, "no magical item was generated in 40 hoards"


def test_a_rarity_the_table_does_not_stock_falls_back_instead_of_raising():
    """The schema advertises 'legendary'; MAGICAL_ITEMS has no such tier."""
    gen = TreasureGenerator()
    random.seed(5)

    for _ in range(20):
        t = gen.generate(10.0, rarity="legendary")
        for m in t.magical_items:
            assert m["rarity"] in gen.MAGICAL_ITEMS


def test_no_preference_leaves_the_cr_table_in_charge():
    gen = TreasureGenerator()
    random.seed(11)

    rarities = {m["rarity"] for _ in range(40) for m in gen.generate(0.5).magical_items}

    assert rarities <= {"common"}, f"a CR 0.5 hoard produced {rarities}"


@pytest.mark.asyncio
async def test_the_executor_forwards_the_rarity_preference():
    random.seed(2)

    out = await generation_actions.execute_generate_treasure(
        cr=1.0, rarity_preference="very_rare"
    )

    assert out.get("error") is None, out
    for m in out["treasure"]["magical_items"]:
        assert m["rarity"] == "very_rare"


# ── the schema promises nothing the engine drops ──────────────────────────

def test_no_schema_field_is_ignored_by_its_executor():
    """A field in the schema is a promise to the model. Keep them honest.

    Bound by AST rather than substring: a default value or a docstring
    mentioning the name is not the same as the body reading it.
    """
    import ast
    import inspect
    import textwrap

    from actions.executors import ACTION_HANDLERS
    from actions.schemas import ACTION_SCHEMAS

    ignored = []
    for action_type, handler in ACTION_HANDLERS.items():
        model = ACTION_SCHEMAS.get(action_type)
        if model is None:
            continue
        fn = inspect.unwrap(handler)
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        except (OSError, TypeError, SyntaxError):
            continue
        func = tree.body[0]
        params = set(inspect.signature(fn).parameters)
        read = {
            n.id for n in ast.walk(ast.Module(body=func.body, type_ignores=[]))
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        for field in model.model_fields:
            if field in ("source", "reason") or field not in params:
                continue
            if field not in read:
                ignored.append(f"{action_type}.{field}")

    assert ignored == [], f"schema fields the executor never reads: {sorted(ignored)}"


# ── /api/procedural/encounter: difficulty ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_encounter_route_forwards_the_requested_difficulty():
    """The route took `difficulty`, built a dispatcher payload without it,
    and every request got a medium encounter."""
    from api.routes import procedural

    state = MagicMock()
    state.action_dispatcher.execute = AsyncMock(return_value={"ok": True})

    await procedural.generate_encounter(difficulty="deadly", state=state)

    payload = state.action_dispatcher.execute.await_args.args[0]
    assert payload.get("difficulty") == "deadly"


@pytest.mark.asyncio
async def test_the_quest_route_steers_by_theme():
    from api.routes import procedural

    random.seed(13)
    out = await procedural.generate_quest(theme="monster", state=MagicMock())

    assert "monster" in out["quest"]["objective"].lower()
