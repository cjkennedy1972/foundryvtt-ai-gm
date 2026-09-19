#!/usr/bin/env python3
"""update_vision said it updated vision and fog of war, and updated a dict.

VisionManager, like the rest of immersion/, never talks to Foundry: it
records a range against a token id and returns it. The executor's docstring
says "Update vision and fog of war", the action is advertised to the model,
and the result carried no error — so the GM narrated a torch being lit and
the map stayed dark.

#185 wired apply_token_effect's condition path to "add-effect" but left this
one, because the obvious route (update-canvas-document via
FoundryClient.canvas_update) has never had a caller and the relay's declared
parameters for that type do not match what canvas_update sends. Rather than
ship an unverified relay path, this writes the Token document through
execute-js, which is what move_token, spend_spell_slot, grant_inspiration
and adjust_exhaustion already do for exactly this reason.

Run:
    cd ai-engine && python -m pytest tests/test_update_vision_reaches_foundry.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.executors import execute_update_vision
from foundry import scripts
from immersion.vision import VisionManager


def _app_state():
    state = MagicMock()
    state.vision_manager = VisionManager()
    return state


def _foundry(ok=True):
    foundry = AsyncMock()
    foundry.execute_js = AsyncMock(return_value={
        "result": {"ok": ok, "id": "tok1", "name": "Thorin"} if ok
        else {"ok": False, "error": "token not found"}
    })
    return foundry


# ── the script ────────────────────────────────────────────────────────────

def test_the_script_writes_the_sight_range_foundry_reads():
    js = scripts.set_token_vision("tok1", 60.0)

    assert "sight" in js and "range" in js
    assert "60" in js


def test_a_light_radius_sets_both_dim_and_bright():
    """5e light sources give bright light to half their radius."""
    js = scripts.set_token_vision("tok1", 60.0, light_radius=40.0)

    assert "light" in js
    assert "40" in js and "20" in js


def test_no_light_radius_leaves_the_light_alone():
    """Setting vision must not extinguish a torch the token already carries."""
    js = scripts.set_token_vision("tok1", 60.0)

    assert "light" not in js.split("const upd")[1].split(";")[0]


def test_the_token_is_resolved_inside_foundry():
    """Same reason move_token does: the model passes ids, names and uuids."""
    js = scripts.set_token_vision("Thorin", 30.0)

    assert "Thorin" in js
    assert "tokens.find" in js or "tokens.get" in js


# ── the executor ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_updating_vision_actually_reaches_foundry():
    foundry = _foundry()

    result = await execute_update_vision(
        "tok1", 60.0, app_state=_app_state(), foundry=foundry
    )

    foundry.execute_js.assert_awaited_once()
    assert result.get("rendered_in_foundry") is True


@pytest.mark.asyncio
async def test_a_light_source_is_applied_too():
    foundry = _foundry()

    await execute_update_vision(
        "tok1", 60.0, has_light=True, light_radius=40.0,
        app_state=_app_state(), foundry=foundry
    )

    assert "40" in foundry.execute_js.await_args.args[0]


@pytest.mark.asyncio
async def test_a_token_foundry_cannot_find_is_reported():
    foundry = _foundry(ok=False)

    result = await execute_update_vision(
        "ghost", 60.0, app_state=_app_state(), foundry=foundry
    )

    assert result.get("rendered_in_foundry") is False
    assert result.get("error")


@pytest.mark.asyncio
async def test_the_in_memory_record_is_still_kept():
    """The immersion endpoints read it back."""
    state = _app_state()

    await execute_update_vision("tok1", 60.0, app_state=state, foundry=_foundry())

    assert state.vision_manager.vision_ranges.get("tok1")


@pytest.mark.asyncio
async def test_a_light_source_is_recorded_as_well_as_applied():
    state = _app_state()

    result = await execute_update_vision(
        "tok1", 60.0, has_light=True, light_radius=40.0,
        app_state=state, foundry=_foundry()
    )

    assert state.vision_manager.light_sources.get("tok1", {}).get("radius") == 40.0
    assert result["result"].get("light")


@pytest.mark.asyncio
async def test_a_relay_failure_does_not_take_the_turn_down():
    foundry = _foundry()
    foundry.execute_js = AsyncMock(side_effect=RuntimeError("relay down"))

    result = await execute_update_vision(
        "tok1", 60.0, app_state=_app_state(), foundry=foundry
    )

    assert result.get("rendered_in_foundry") is False
    assert result.get("error")
