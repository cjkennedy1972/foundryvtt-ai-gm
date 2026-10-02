"""Walls: the field is `sight`, and the legacy `sense` the model used to be taught is mapped to it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from actions.executors import _legacy_sense_to_sight, execute_place_walls
from llm.system_prompts import build_system_prompt


def test_legacy_sense_becomes_sight_and_an_explicit_sight_wins():
    assert _legacy_sense_to_sight({"c": [0, 0, 1, 1], "sense": 0, "move": 20}) == {"c": [0, 0, 1, 1], "move": 20, "sight": 0}
    assert _legacy_sense_to_sight({"c": [0, 0, 1, 1], "sense": 0, "sight": 20})["sight"] == 20
    untouched = {"c": [0, 0, 1, 1], "sight": 10}
    assert _legacy_sense_to_sight(untouched) is untouched          # nothing to map: same object, no copy
    assert _legacy_sense_to_sight("not a dict") == "not a dict"


@pytest.mark.asyncio
async def test_the_relay_receives_sight_never_sense():
    foundry = MagicMock()
    foundry.canvas_create = AsyncMock(return_value={"ok": True})
    await execute_place_walls([{"c": [0, 0, 64, 0], "sense": 0}], foundry=foundry)
    foundry.canvas_create.assert_awaited_once_with("walls", [{"c": [0, 0, 64, 0], "sight": 0}])


def test_the_prompt_teaches_sight_not_sense_for_walls():
    prompt = build_system_prompt()
    assert '"sight"' in prompt
    assert '"sense"' not in prompt and "`sense`" not in prompt


def test_the_prompts_wall_legend_matches_foundrys_constants():
    """CONST.WALL_MOVEMENT_TYPES is {NONE 0, NORMAL 20}; CONST.WALL_SENSE_TYPES is {NONE 0, LIMITED 10,
    NORMAL 20, PROXIMITY 30, DISTANCE 40} (Foundry API docs, v13). The prompt once said 30=ethereal and
    40=impassable/sight-only, which steered walls to the wrong sense type."""
    prompt = build_system_prompt()
    assert "ethereal" not in prompt and "sight-only" not in prompt
    assert "**30**=proximity" in prompt and "**40**=distance" in prompt
    assert "`move`: **0**=none (walkable), **20**=blocks movement" in prompt


@pytest.mark.asyncio
async def test_setup_scene_also_maps_legacy_sense_and_no_wall_text_still_teaches_it():
    from actions.executors import execute_setup_scene
    foundry = MagicMock()
    foundry.canvas_create = AsyncMock(return_value={"ok": True})
    foundry.clear_canvas_layer = AsyncMock()
    foundry.get_scene_details = AsyncMock(return_value={})
    await execute_setup_scene(walls=[{"c": [0, 0, 64, 0], "sense": 0}], foundry=foundry)
    foundry.canvas_create.assert_any_await("walls", [{"c": [0, 0, 64, 0], "sight": 0}])

    import pathlib
    for path in ("actions/schemas.py", "actions/executors.py", "llm/system_prompts.py"):
        text = pathlib.Path(path).read_text()
        assert "ethereal" not in text and "move/sense" not in text, path
