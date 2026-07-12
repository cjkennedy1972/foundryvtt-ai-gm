#!/usr/bin/env python3
"""
execute_generate_treasure previously called gen.generate_treasure(cr) — a
method that doesn't exist on ProceduralGenerator — so every call raised
AttributeError, silently swallowed and reported back as a generic error.
This action has never produced any treasure until this fix.

Run:
    cd ai-engine && python -m pytest tests/test_generate_treasure.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex


def _foundry(active_modules):
    f = AsyncMock()
    f.is_connected = True
    f.create_entity = AsyncMock(return_value={"uuid": "Actor.loot123"})
    f.place_token = AsyncMock(return_value={"id": "tok_loot"})
    f.execute_js = AsyncMock(return_value={"result": [{"id": m} for m in active_modules]})
    return f


def test_generate_treasure_no_longer_raises_attributeerror():
    f = _foundry(active_modules=[])
    out = asyncio.run(ex.execute_generate_treasure(cr=5.0, foundry=f))
    assert "error" not in out, f"treasure generation failed: {out.get('error')}"
    assert out["type"] == "generate_treasure"


def test_treasure_dict_has_real_fields_not_the_old_broken_ones():
    f = _foundry(active_modules=[])
    out = asyncio.run(ex.execute_generate_treasure(cr=5.0, foundry=f))
    treasure = out["treasure"]
    # Real GeneratedTreasure fields — not the old total_value_gp/gold_coins
    # keys that were called on a dict .get() against a dataclass.
    assert set(treasure.keys()) == {"gold", "gems", "items", "magical_items", "total_value_gp"}
    assert isinstance(treasure["gold"], int) and treasure["gold"] >= 10
    assert isinstance(treasure["total_value_gp"], int)


def test_journal_entry_is_created():
    f = _foundry(active_modules=[])
    out = asyncio.run(ex.execute_generate_treasure(cr=5.0, foundry=f))
    assert out["deployed_to_foundry"] is True
    assert out["journal_uuid"] == "Actor.loot123"


def test_loot_pile_created_when_item_piles_active():
    f = _foundry(active_modules=["item-piles"])
    out = asyncio.run(ex.execute_generate_treasure(cr=5.0, foundry=f))
    assert out.get("loot_pile_uuid") == "Actor.loot123"
    assert out.get("loot_pile_token_id") == "tok_loot"
    f.place_token.assert_awaited_once()


def test_no_loot_pile_when_item_piles_not_active():
    f = _foundry(active_modules=[])
    out = asyncio.run(ex.execute_generate_treasure(cr=5.0, foundry=f))
    assert "loot_pile_uuid" not in out
    f.place_token.assert_not_awaited()


if __name__ == "__main__":
    for fn in [
        test_generate_treasure_no_longer_raises_attributeerror,
        test_treasure_dict_has_real_fields_not_the_old_broken_ones,
        test_journal_entry_is_created,
        test_loot_pile_created_when_item_piles_active,
        test_no_loot_pile_when_item_piles_not_active,
    ]:
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll generate_treasure tests passed!")
