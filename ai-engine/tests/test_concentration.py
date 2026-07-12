#!/usr/bin/env python3
"""
execute_cast_spell must not let a caster silently hold two concentration
spells at once — RAW says starting a new one ends the old one.

Run:
    cd ai-engine && python -m pytest tests/test_concentration.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex


def _foundry(conflict_info):
    f = AsyncMock()
    f.execute_js = AsyncMock(return_value={"result": conflict_info})
    f.break_concentration = AsyncMock(return_value={"ok": True})
    f.use_spell_slot = AsyncMock(return_value={"ok": True})
    return f


def test_new_concentration_spell_breaks_existing_concentration():
    f = _foundry({
        "found": True, "newSpellRequiresConcentration": True,
        "alreadyConcentrating": True, "concentratingOn": "Hex",
    })
    out = asyncio.run(ex.execute_cast_spell(
        actor_uuid="Actor.warlock", spell_name="Hold Person", spell_level=2, foundry=f
    ))
    f.break_concentration.assert_awaited_once_with("Actor.warlock")
    assert "Hex" in out["concentration_note"]
    f.use_spell_slot.assert_awaited_once()


def test_non_concentration_spell_does_not_touch_existing_concentration():
    f = _foundry({
        "found": True, "newSpellRequiresConcentration": False,
        "alreadyConcentrating": True, "concentratingOn": "Hex",
    })
    out = asyncio.run(ex.execute_cast_spell(
        actor_uuid="Actor.warlock", spell_name="Eldritch Blast", spell_level=0, foundry=f
    ))
    f.break_concentration.assert_not_called()
    assert "concentration_note" not in out


def test_first_concentration_spell_with_nothing_active_does_not_break_anything():
    f = _foundry({
        "found": True, "newSpellRequiresConcentration": True,
        "alreadyConcentrating": False, "concentratingOn": None,
    })
    out = asyncio.run(ex.execute_cast_spell(
        actor_uuid="Actor.warlock", spell_name="Hex", spell_level=1, foundry=f
    ))
    f.break_concentration.assert_not_called()
    assert "concentration_note" not in out


if __name__ == "__main__":
    for fn in [
        test_new_concentration_spell_breaks_existing_concentration,
        test_non_concentration_spell_does_not_touch_existing_concentration,
        test_first_concentration_spell_with_nothing_active_does_not_break_anything,
    ]:
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll concentration tests passed!")
