"""Coverage for actions/schemas validators and actions/generation_actions branches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

import campaign.modules  # noqa: F401  (registers item-piles)
from actions import generation_actions as ga
from actions.schemas import (
    ApplyTokenEffectAction, ExecuteJSAction, stamp_model_origin,
)


# --------------------------------------------------------------------- schemas

@pytest.mark.parametrize("raw,expected", [("5", 5), ("until_encounter_start", None), (3, 3), (None, None)])
def test_token_effect_duration_coercion(raw, expected):
    a = ApplyTokenEffectAction(token_id="t", effect_type="aura", effect_name="x", duration=raw)
    assert a.duration == expected


@pytest.mark.parametrize("desc", ["const x", "calls game.actors", "run execute_js"])
def test_execute_js_description_rejects_code(desc):
    with pytest.raises(ValidationError, match="disallowed pattern"):
        ExecuteJSAction(code="1", description=desc)


def test_execute_js_description_accepts_prose():
    a = ExecuteJSAction(code="1", description="Light the torches, please.")
    assert a.description.startswith("Light")


def test_stamp_model_origin_marks_dicts_only():
    acts = [{"type": "narrate"}, "junk"]
    out = stamp_model_origin(acts)
    assert out is acts and acts[0]["source"] == "model_turn" and acts[1] == "junk"
    assert stamp_model_origin(acts, "player_turn")[0]["source"] == "player_turn"
    assert stamp_model_origin(None) == []


# ---------------------------------------------------- _resolve_scene_dimensions

@pytest.mark.asyncio
@pytest.mark.parametrize("details,expected", [
    (None, (800, 600, 100)),
    ("junk", (800, 600, 100)),
    ({"width": 1000, "height": 500, "grid": {"size": 50}}, (1000, 500, 50)),
    ({"data": {"width": 900, "height": 400, "grid": 70}}, (900, 400, 70)),
    ({"gridSize": 25}, (800, 600, 25)),
    ({"data": {"gridSize": 30}}, (800, 600, 30)),
    ({"grid": 0}, (800, 600, 100)),
])
async def test_scene_dimensions_shapes(details, expected):
    fc = MagicMock()
    fc.get_scene_details = AsyncMock(return_value=details)
    assert await ga._resolve_scene_dimensions(fc) == expected


@pytest.mark.asyncio
async def test_scene_dimensions_no_foundry_and_errors():
    assert await ga._resolve_scene_dimensions(None) == (800, 600, 100)
    fc = MagicMock()
    fc.get_scene_details = AsyncMock(side_effect=RuntimeError("x"))
    assert await ga._resolve_scene_dimensions(fc) == (800, 600, 100)
    fc.get_scene_details = AsyncMock(return_value={"grid": {"size": 0}})
    assert (await ga._resolve_scene_dimensions(fc))[2] == 100   # never below 1/zero


# ------------------------------------------------------------------- encounter

def _encounter(placements):
    return {"difficulty_rating": "Hard", "notes": "n", "budget": 100, "adjusted_xp": 90,
            "creatures": [], "placements": placements}


def _fc(**kw):
    fc = MagicMock()
    fc.is_connected = True
    fc.place_token = AsyncMock(return_value={"id": "tok"})
    fc.get_scene_tokens = AsyncMock(return_value=[{"id": "pc1"}, {"id": ""}, {"id": "tok"}])
    fc.start_encounter = AsyncMock()
    for k, v in kw.items():
        setattr(fc, k, v)
    return fc


async def _run_encounter(fc, placements, ensure="Actor.m1"):
    with patch.object(ga, "_resolve_scene_dimensions", AsyncMock(return_value=(1, 1, 1))), \
         patch("combat.compendium_generator.CompendiumEncounterGenerator") as G, \
         patch("campaign.monster_actor.ensure_monster_actor", AsyncMock(return_value=ensure)) as ens:
        G.return_value.generate = AsyncMock(return_value=_encounter(placements))
        res = await ga.execute_generate_encounter(3, 4, foundry=fc)
    return res, ens


@pytest.mark.asyncio
async def test_encounter_world_npc_placed_by_own_uuid_and_whole_scene_in_combat():
    fc = _fc()
    res, ens = await _run_encounter(
        fc, [{"name": "Bob", "source": "world", "uuid": "Actor.bob", "x": 1, "y": 2}])
    ens.assert_not_called()
    assert fc.place_token.await_args.kwargs == {"uuid": "Actor.bob", "x": 1, "y": 2, "disposition": -1}
    fc.start_encounter.assert_awaited_once_with(["pc1", "tok"], roll_all=True)
    assert res["placed_tokens"] == ["tok"] and res["deployed_to_foundry"] is True


@pytest.mark.asyncio
async def test_encounter_skips_unresolvable_monster_and_falls_back_on_token_fetch_error():
    fc = _fc(get_scene_tokens=AsyncMock(side_effect=RuntimeError("x")))
    res, _ = await _run_encounter(fc, [
        {"name": "Ghost", "cr": 1}, ], ensure="")
    assert res["placed_tokens"] == [] and res["deployed_to_foundry"] is False
    fc.start_encounter.assert_not_called()
    res, _ = await _run_encounter(fc, [{"name": "Orc", "cr": 1}])
    fc.start_encounter.assert_awaited_once_with(["tok"], roll_all=True)
    assert res["placed_tokens"] == ["tok"]


@pytest.mark.asyncio
async def test_encounter_place_failures_do_not_count_as_placed():
    fc = _fc(place_token=AsyncMock(side_effect=[{"error": "no"}, {"weird": 1}]))
    res, _ = await _run_encounter(fc, [{"name": "A"}, {"name": "B"}])
    assert res["placed_tokens"] == []
    fc.start_encounter.assert_not_called()


@pytest.mark.asyncio
async def test_encounter_generator_failure_is_reported():
    with patch.object(ga, "_resolve_scene_dimensions", AsyncMock(return_value=(1, 1, 1))), \
         patch("combat.compendium_generator.CompendiumEncounterGenerator") as G:
        G.return_value.generate = AsyncMock(side_effect=ValueError("no monsters"))
        res = await ga.execute_generate_encounter(3, 4, foundry=None)
    assert res == {"type": "generate_encounter", "error": "no monsters"}


# -------------------------------------------------------------------- treasure

def _treasure():
    return SimpleNamespace(
        gold=10, total_value=99,
        gems=[{"name": "Ruby<b>", "value": "50gp"}],
        items=[{"name": "Rope", "value": "garbage"}],
        magical_items=[{"name": "Wand", "rarity": "rare"}])


async def _run_treasure(fc):
    with patch("procedural.generator.ProceduralGenerator") as PG:
        PG.return_value.treasure_gen.generate.return_value = _treasure()
        PG.return_value.treasure_gen._estimate_value.side_effect = (
            lambda v: 50 if v == "50gp" else (_ for _ in ()).throw(ValueError(v)))
        res = await ga.execute_generate_treasure(2, "rare", foundry=fc)
        PG.return_value.treasure_gen.generate.assert_called_once_with(2, rarity="rare")
    return res


def _tfc(mods, **kw):
    fc = MagicMock()
    fc.is_connected = True
    fc.execute_js = AsyncMock(return_value=mods)
    fc.create_entity = AsyncMock(side_effect=[{"uuid": "J.1"}, {"uuid": "Actor.pile"}])
    fc.place_token = AsyncMock(return_value={"id": "pt"})
    for k, v in kw.items():
        setattr(fc, k, v)
    return fc


@pytest.mark.asyncio
async def test_treasure_journal_html_escaped_and_no_pile_without_item_piles():
    fc = _tfc({"result": [{"id": "other"}]})
    res = await _run_treasure(fc)
    html = fc.create_entity.await_args_list[0].args[1]["pages"][0]["text"]["content"]
    assert "Ruby&lt;b&gt;" in html and "<b>" not in html and "10 gold coins" in html
    assert res["journal_uuid"] == "J.1" and res["deployed_to_foundry"] is True
    assert "loot_pile_uuid" not in res and fc.create_entity.await_count == 1


@pytest.mark.asyncio
async def test_treasure_module_lookup_failure_means_no_pile():
    fc = _tfc(None, execute_js=AsyncMock(side_effect=RuntimeError("x")))
    res = await _run_treasure(fc)
    assert res["journal_uuid"] == "J.1" and "loot_pile_uuid" not in res


@pytest.mark.asyncio
async def test_treasure_item_pile_deployed_with_estimated_values():
    fc = _tfc({"result": [{"id": "item-piles"}]})
    res = await _run_treasure(fc)
    pile = fc.create_entity.await_args_list[1].args[1]
    prices = {i["name"]: i["system"]["price"]["value"] for i in pile["items"]}
    assert prices == {"Ruby<b>": 50, "Rope": 0, "Wand": 0}     # bad value strings fall back to 0
    assert res["loot_pile_uuid"] == "Actor.pile" and res["loot_pile_token_id"] == "pt"


@pytest.mark.asyncio
async def test_treasure_pile_failure_is_logged_not_fatal(caplog):
    fc = _tfc({"result": [{"id": "item-piles"}]},
              create_entity=AsyncMock(side_effect=[{"uuid": "J.1"}, RuntimeError("nope")]))
    res = await _run_treasure(fc)
    assert res["journal_uuid"] == "J.1" and "loot_pile_uuid" not in res
    assert "Loot pile creation failed" in caplog.text


@pytest.mark.asyncio
async def test_treasure_pile_actor_without_uuid_places_nothing():
    fc = _tfc({"result": [{"id": "item-piles"}]},
              create_entity=AsyncMock(side_effect=[{"uuid": "J.1"}, {}]))
    res = await _run_treasure(fc)
    fc.place_token.assert_not_called()
    assert "loot_pile_uuid" not in res


@pytest.mark.asyncio
async def test_treasure_error_is_reported():
    with patch("procedural.generator.ProceduralGenerator", side_effect=RuntimeError("bad")):
        assert await ga.execute_generate_treasure(1) == {"type": "generate_treasure", "error": "bad"}


# ------------------------------------------------------------------------- npc

@pytest.mark.asyncio
async def test_npc_scene_token_count_failure_defaults_to_first_slot():
    fc = MagicMock()
    fc.is_connected = True
    fc.create_entity = AsyncMock(return_value={"uuid": "Actor.n"})
    fc.get_scene_tokens = AsyncMock(side_effect=RuntimeError("x"))
    fc.place_token = AsyncMock(return_value={"error": "denied"})
    res = await ga.execute_generate_npc(foundry=fc)
    assert fc.place_token.await_args.kwargs["x"] == 400
    assert res["npc"]["token_id"] == "" and res["npc"]["actor_uuid"] == "Actor.n"
