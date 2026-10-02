#!/usr/bin/env python3
"""ItemManager (immersion/items.py): loot pools, distribution and inventory.

Asserts the actual returned payloads and state mutations (pool contents,
distributed-item lists, computed totals), not just that a call returns a dict.

Run:
    cd ai-engine && python -m pytest tests/test_immersion_items.py -v
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from immersion.items import ItemManager, LootItem


def _item(item_id="sword-1", rarity="rare", value_gp=250.0, weight_lbs=3.0, quantity=1):
    return LootItem(
        item_id=item_id,
        name="Flametongue",
        rarity=rarity,
        value_gp=value_gp,
        weight_lbs=weight_lbs,
        description="A sword that bursts into flame on command.",
        quantity=quantity,
    )


# ── add_item_to_pool ─────────────────────────────────────────────────────

def test_add_item_to_pool_registers_the_item_and_pool():
    mgr = ItemManager()
    result = mgr.add_item_to_pool("goblin-camp", _item())

    assert result == {
        "type": "item_added_to_pool",
        "pool_name": "goblin-camp",
        "item_id": "sword-1",
        "item_name": "Flametongue",
        "rarity": "rare",
    }
    assert mgr.available_items["sword-1"].value_gp == 250.0
    assert mgr.loot_pools["goblin-camp"] == ["sword-1"]


def test_add_item_to_pool_appends_to_an_existing_pool_without_duplicating_items():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item("sword-1"))
    mgr.add_item_to_pool("camp", _item("shield-1", rarity="uncommon"))

    assert mgr.loot_pools["camp"] == ["sword-1", "shield-1"]
    assert len(mgr.available_items) == 2


def test_add_item_to_pool_does_not_overwrite_an_already_registered_item():
    """Re-adding the same item_id (e.g. to a second pool) must not clobber
    the first registration's data."""
    mgr = ItemManager()
    mgr.add_item_to_pool("camp-a", _item("sword-1", value_gp=250.0))
    mgr.add_item_to_pool("camp-b", _item("sword-1", value_gp=999.0))

    assert mgr.available_items["sword-1"].value_gp == 250.0
    assert mgr.loot_pools["camp-a"] == ["sword-1"]
    assert mgr.loot_pools["camp-b"] == ["sword-1"]


# ── distribute_item ──────────────────────────────────────────────────────

def test_distribute_item_records_it_under_the_actor_and_returns_its_color():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item(rarity="legendary"))

    result = mgr.distribute_item("sword-1", "actor-1")

    assert result == {
        "type": "item_distributed",
        "actor_id": "actor-1",
        "item_id": "sword-1",
        "item_name": "Flametongue",
        "rarity": "legendary",
        "color": "#ff8000",
    }
    assert mgr.distributed_items["actor-1"] == ["sword-1"]


def test_distribute_item_appends_for_an_actor_who_already_has_items():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item("sword-1"))
    mgr.add_item_to_pool("camp", _item("potion-1", rarity="common"))

    mgr.distribute_item("sword-1", "actor-1")
    mgr.distribute_item("potion-1", "actor-1")

    assert mgr.distributed_items["actor-1"] == ["sword-1", "potion-1"]


def test_distribute_item_falls_back_to_white_for_an_unknown_rarity():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item(rarity="mythic"))
    result = mgr.distribute_item("sword-1", "actor-1")
    assert result["color"] == "#ffffff"


def test_distribute_item_returns_an_error_for_an_unregistered_item():
    mgr = ItemManager()
    result = mgr.distribute_item("ghost-item", "actor-1")
    assert result == {"error": "Item not found: ghost-item"}
    assert "actor-1" not in mgr.distributed_items


# ── draw_from_pool ───────────────────────────────────────────────────────

def test_draw_from_pool_distributes_an_item_from_the_pool():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item("sword-1"))

    with patch("random.choice", return_value="sword-1"):
        result = mgr.draw_from_pool("camp", "actor-1")

    assert result["type"] == "item_distributed"
    assert result["item_id"] == "sword-1"
    assert mgr.distributed_items["actor-1"] == ["sword-1"]


def test_draw_from_pool_returns_an_error_for_a_missing_pool():
    mgr = ItemManager()
    assert mgr.draw_from_pool("no-such-pool", "actor-1") == {
        "error": "Pool not found or empty: no-such-pool"
    }


def test_draw_from_pool_returns_an_error_for_an_empty_pool():
    mgr = ItemManager()
    mgr.loot_pools["camp"] = []
    assert mgr.draw_from_pool("camp", "actor-1") == {
        "error": "Pool not found or empty: camp"
    }


# ── get_actor_inventory ──────────────────────────────────────────────────

def test_get_actor_inventory_computes_item_count_and_total_value():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item("sword-1", value_gp=250.0))
    mgr.add_item_to_pool("camp", _item("potion-1", rarity="common", value_gp=50.0))
    mgr.distribute_item("sword-1", "actor-1")
    mgr.distribute_item("potion-1", "actor-1")

    inventory = mgr.get_actor_inventory("actor-1")

    assert inventory["item_count"] == 2
    assert inventory["total_value_gp"] == 300.0
    assert {i["id"] for i in inventory["items"]} == {"sword-1", "potion-1"}


def test_get_actor_inventory_is_empty_for_an_unknown_actor():
    mgr = ItemManager()
    inventory = mgr.get_actor_inventory("nobody")
    assert inventory == {
        "actor_id": "nobody",
        "items": [],
        "item_count": 0,
        "total_value_gp": 0,
    }


# ── create_loot_pool_from_cr ─────────────────────────────────────────────

def test_create_loot_pool_from_cr_scales_distribution_with_cr():
    mgr = ItemManager()
    result = mgr.create_loot_pool_from_cr("boss-room", cr=15)

    assert result["cr"] == 15
    assert result["suggested_distribution"] == {
        "mundane_items": 7,
        "common_items": 5,
        "uncommon_items": 3,
        "rare_items": 1,
    }


def test_create_loot_pool_from_cr_gives_low_cr_encounters_no_rare_items():
    mgr = ItemManager()
    result = mgr.create_loot_pool_from_cr("weak-camp", cr=1)
    assert result["suggested_distribution"] == {
        "mundane_items": 1,
        "common_items": 0,
        "uncommon_items": 0,
        "rare_items": 0,
    }


# ── listing ──────────────────────────────────────────────────────────────

def test_list_available_items_includes_quantity_and_value():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item(quantity=3))
    items = mgr.list_available_items()
    assert items == [
        {
            "id": "sword-1",
            "name": "Flametongue",
            "rarity": "rare",
            "value_gp": 250.0,
            "quantity": 3,
        }
    ]


def test_list_loot_pools_reports_item_counts_per_pool():
    mgr = ItemManager()
    mgr.add_item_to_pool("camp", _item("sword-1"))
    mgr.add_item_to_pool("camp", _item("shield-1"))
    mgr.add_item_to_pool("vault", _item("gem-1"))

    assert mgr.list_loot_pools() == {"camp": 2, "vault": 1}
