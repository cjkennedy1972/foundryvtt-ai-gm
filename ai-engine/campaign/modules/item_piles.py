"""Item Piles — merchant NPC storefronts and physical loot pile actors."""

from typing import Any, Dict, Optional

from campaign.modules.registry import ModuleIntegration, NpcContext, register

# dnd5e only accepts a fixed set of Item types; currency isn't an Item.
_VALID_ITEM_TYPES = {
    "weapon", "equipment", "consumable", "tool",
    "loot", "container", "feat", "spell", "backpack",
}
_TYPE_ALIASES = {
    "wondrous_item": "equipment", "wondrous": "equipment",
    "ring": "equipment", "rod": "equipment", "wand": "consumable",
    "staff": "weapon", "scroll": "consumable", "potion": "consumable",
    "armor": "equipment", "gear": "loot", "treasure": "loot",
    "gem": "loot", "trade_good": "loot",
}


def on_npc(ctx: NpcContext) -> None:
    if ctx.npc.get("npc_type") == "merchant":
        ctx.flags["item-piles"] = {
            "data": {
                "enabled": True,
                "type": "merchant",
                "displayOne": False,
                "showItemName": True,
                "isMerchant": True,
                "canInspectItems": True,
            }
        }


async def on_loot_table(table: dict, mods: dict) -> Optional[Dict[str, Any]]:
    """Build a physical loot-container Actor document for a loot table.

    Returns None when the table opts out (deploy_as_pile: False) — caller
    (orchestrator.py) is responsible for creating the returned Actor and
    recording deployment status.
    """
    if not table.get("deploy_as_pile", True):
        return None

    pile_items = []
    for e in table.get("entries", []):
        raw_type = e.get("foundry_item_type", "loot")
        # Currency is not an Item document — fold it into the pile, skip here.
        if raw_type == "currency":
            continue
        item_type = _TYPE_ALIASES.get(raw_type, raw_type)
        if item_type not in _VALID_ITEM_TYPES:
            item_type = "loot"
        pile_items.append({
            "name": e.get("name", "Loot"),
            "type": item_type,
            "system": {
                "description": {"value": e.get("description", "")},
                "quantity": e.get("quantity", 1),
                "weight": e.get("weight_lbs", 0.1),
                "price": {
                    "value": e.get("value_gp", 0),
                    "denomination": "gp",
                },
                "rarity": e.get("rarity", "common"),
            },
        })

    return {
        "name": f"{table['name']} (Loot)",
        "type": "npc",
        "items": pile_items,
        "flags": {
            "item-piles": {
                "data": {
                    "enabled": True,
                    "type": table.get("pile_type", "pile"),
                    "displayOne": len(pile_items) == 1,
                    "showItemName": True,
                    "canInspectItems": True,
                }
            },
            "ai-gm": {"loot_table": table["name"]},
        },
    }


register(ModuleIntegration(module_id="item-piles", on_npc=on_npc, on_loot_table=on_loot_table))
