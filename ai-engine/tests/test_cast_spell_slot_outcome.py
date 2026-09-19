#!/usr/bin/env python3
"""Casting with no slots left reported success and stripped concentration.

#178 fixed use_spell_slot, which had been sending a "use-spell-slot" message
type the relay has no endpoint for. It now goes through execute-js and
returns what scripts.spend_spell_slot returns: {ok, used, remaining}.

The caller was not updated. execute_cast_spell reads
result.get("success", True) — a key that shape does not have — so the default
applies and every cast counts as successful. Two consequences:

  A caster with no slots of that level loses the spell it was concentrating
  on, which is exactly what the comment above that line says it must not do.

  The action reports no error, so the GM narrates a spell that was never cast.

Before #178 the call raised, so execute_cast_spell failed outright and never
reached this line. The fix made the path reachable and the stale read
visible.

Run:
    cd ai-engine && python -m pytest tests/test_cast_spell_slot_outcome.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.executors import execute_cast_spell


def _foundry(used=True, remaining=2, concentrating_on=None):
    """concentrating_on: the spell the caster already holds, if any.

    execute_cast_spell derives that from scripts.get_concentration_conflict
    over execute_js rather than taking it as an argument.
    """
    f = AsyncMock()
    f.use_spell_slot = AsyncMock(return_value={"ok": True, "used": used, "remaining": remaining})
    f.break_concentration = AsyncMock(return_value={"ok": True})
    f.check_spell_ritual = AsyncMock(return_value={"isRitual": False})
    f.get_actors = AsyncMock(return_value=[])
    f.chat_message = AsyncMock()
    f.execute_js = AsyncMock(return_value={"result": {
        "newSpellRequiresConcentration": bool(concentrating_on),
        "alreadyConcentrating": bool(concentrating_on),
        "concentratingOn": concentrating_on,
    }})
    return f


async def _cast(foundry, **kw):
    with patch("config.settings.players_roll_own", False):
        return await execute_cast_spell(
            actor_uuid="Actor.wizard", spell_name="Fireball", spell_level=3,
            foundry=foundry, **kw,
        )


# ── a slot was consumed ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_successful_cast_is_reported_as_one():
    result = await _cast(_foundry(used=True))

    assert result.get("success") is not False
    assert result["result"]["used"] is True


@pytest.mark.asyncio
async def test_a_successful_cast_breaks_the_old_concentration():
    foundry = _foundry(used=True, concentrating_on="Bless")

    result = await _cast(foundry)

    foundry.break_concentration.assert_awaited_once()
    assert result.get("concentration_note")


# ── no slot was available ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_casting_with_no_slots_left_is_reported_as_a_failure():
    result = await _cast(_foundry(used=False, remaining=0))

    assert result.get("success") is False
    assert result.get("error")


@pytest.mark.asyncio
async def test_a_cast_that_never_happened_does_not_strip_concentration():
    """The comment above that line says exactly this must not happen."""
    foundry = _foundry(used=False, remaining=0, concentrating_on="Bless")

    result = await _cast(foundry)

    foundry.break_concentration.assert_not_awaited()
    assert not result.get("concentration_note")


# ── rituals ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_ritual_cast_consumes_no_slot_and_still_succeeds():
    foundry = _foundry()
    foundry.check_spell_ritual = AsyncMock(return_value={"isRitual": True})

    result = await _cast(foundry, ritual=True)

    assert result.get("success") is not False
    foundry.use_spell_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_ritual_still_breaks_the_old_concentration():
    """No slot is spent, but the spell is cast."""
    foundry = _foundry(concentrating_on="Bless")
    foundry.check_spell_ritual = AsyncMock(return_value={"isRitual": True})

    await _cast(foundry, ritual=True)

    foundry.break_concentration.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_ritual_reports_that_no_slot_was_spent():
    """It is cast, and it costs nothing — both have to be true in the result,
    or the only thing making a ritual count is the absence of a key."""
    foundry = _foundry()
    foundry.check_spell_ritual = AsyncMock(return_value={"isRitual": True})

    result = await _cast(foundry, ritual=True)

    assert result["result"]["used"] is False
    assert result.get("success") is not False


@pytest.mark.asyncio
async def test_an_older_result_shape_without_used_still_counts_as_cast():
    """"used" absent is not the same as used=False. A stub or an older client
    that reports only {ok: True} must not have its cast thrown away."""
    foundry = _foundry()
    foundry.use_spell_slot = AsyncMock(return_value={"ok": True})

    result = await _cast(foundry)

    assert result.get("success") is not False


@pytest.mark.asyncio
async def test_a_non_ritual_spell_cast_as_a_ritual_is_refused():
    foundry = _foundry()
    foundry.check_spell_ritual = AsyncMock(return_value={"isRitual": False})

    result = await _cast(foundry, ritual=True)

    assert result.get("success") is False
    foundry.use_spell_slot.assert_not_awaited()
