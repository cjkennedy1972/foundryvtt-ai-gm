#!/usr/bin/env python3
"""apply_token_effect told the model it had applied a visual effect and put
it in a Python dict.

The immersion managers are in-memory bookkeeping: none of
immersion/{ambient,effects,vision,items,particles}.py mentions foundry,
execute_js or the relay at all. That is fine for AmbientManager, whose
atmosphere string is read back into the prompt by chat_listener, but
apply_token_effect is an action the model calls expecting something to
appear on a token — and it returned a result with no error, so the GM went
on to narrate a marker nobody could see.

apply_condition already applies real Foundry status icons through
"add-effect", which works in play. A condition effect now goes down that
same path.

Auras and update_vision are still records only; see the PR. Foundry's token
vision lives on the Token document and the relay's update-canvas-document is
the route to it, but FoundryClient.canvas_update has never had a caller and
the relay's declared parameters for that type do not match what it sends, so
wiring it here would be shipping an unverified path.

Run:
    cd ai-engine && python -m pytest tests/test_token_effects_reach_foundry.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.executors import execute_apply_token_effect
from immersion.effects import EffectsManager


def _app_state():
    state = MagicMock()
    state.effects_manager = EffectsManager()
    return state


def _foundry(actor_uuid="Actor.goblin"):
    foundry = AsyncMock()
    foundry.get_scene_tokens = AsyncMock(return_value=[
        {"id": "tok1", "name": "Goblin", "actorUuid": actor_uuid},
    ])
    foundry.add_effect = AsyncMock(return_value={"ok": True})
    return foundry


@pytest.mark.asyncio
async def test_a_condition_effect_reaches_the_token_in_foundry():
    foundry = _foundry()

    result = await execute_apply_token_effect(
        "tok1", "condition", "poisoned", app_state=_app_state(), foundry=foundry
    )

    foundry.add_effect.assert_awaited_once()
    assert foundry.add_effect.await_args.args[0] == "Actor.goblin"
    assert result.get("rendered_in_foundry") is True


@pytest.mark.asyncio
async def test_the_condition_name_is_passed_as_the_status_id():
    foundry = _foundry()

    await execute_apply_token_effect(
        "tok1", "condition", "Poisoned", app_state=_app_state(), foundry=foundry
    )

    assert foundry.add_effect.await_args.args[1] == "poisoned"


@pytest.mark.asyncio
async def test_the_in_memory_record_is_still_kept():
    """chat_listener and the immersion endpoints read it back."""
    state = _app_state()

    await execute_apply_token_effect(
        "tok1", "condition", "poisoned", app_state=state, foundry=_foundry()
    )

    assert state.effects_manager.active_effects.get("tok1")


@pytest.mark.asyncio
async def test_a_token_with_no_actor_says_so_rather_than_claiming_success():
    foundry = _foundry()
    foundry.get_scene_tokens = AsyncMock(return_value=[])

    result = await execute_apply_token_effect(
        "tok1", "condition", "poisoned", app_state=_app_state(), foundry=foundry
    )

    assert result.get("rendered_in_foundry") is False
    foundry.add_effect.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_relay_failure_is_reported_not_swallowed():
    foundry = _foundry()
    foundry.add_effect = AsyncMock(side_effect=RuntimeError("relay down"))

    result = await execute_apply_token_effect(
        "tok1", "condition", "poisoned", app_state=_app_state(), foundry=foundry
    )

    assert result.get("rendered_in_foundry") is False
    assert result.get("error")


@pytest.mark.asyncio
async def test_an_aura_is_marked_as_narration_only():
    """No relay path exists for auras, so the result must not imply one."""
    foundry = _foundry()

    result = await execute_apply_token_effect(
        "tok1", "aura", "protection", app_state=_app_state(), foundry=foundry
    )

    assert result.get("rendered_in_foundry") is False
    foundry.add_effect.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_effect_type_is_still_rejected():
    result = await execute_apply_token_effect(
        "tok1", "sparkles", "x", app_state=_app_state(), foundry=_foundry()
    )

    assert result.get("error")
