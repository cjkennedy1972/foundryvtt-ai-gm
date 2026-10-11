"""Quartermaster — credit quest rewards and shared loot to the party stash.

Flags quest rewards and shared party loot for Quartermaster to handle
stashing instead of dropping an Item Piles pile on the map.
"""

from typing import Any, Dict, Optional

from campaign.modules.item_piles import _PILE_IMAGES
from campaign.modules.registry import ModuleIntegration, register


async def on_loot_table(table: dict, mods: dict) -> Optional[Dict[str, Any]]:
    """Flag party loot and quest rewards for Quartermaster stashing.

    Returns a minimal Actor with Quartermaster flags when the table is party
    loot or quest rewards. Returns None to skip (let Item Piles handle it).
    """
    if not table.get("deploy_as_pile", True):
        return None  # same opt-out as item_piles: the table asked for no physical actor

    is_party_loot = table.get("loot_type") == "party" or table.get("is_quest_reward")
    if not is_party_loot:
        return None

    items = []
    for e in table.get("entries", []):
        if e.get("foundry_item_type") == "currency":
            continue  # Currency handled separately by Quartermaster
        items.append({
            "name": e.get("name", "Loot"),
            "type": "loot",
            "system": {
                "description": {"value": e.get("description", "")},
                "quantity": e.get("quantity", 1),
                "weight": e.get("weight_lbs", 0.1),
                "price": {"value": e.get("value_gp", 0), "denomination": "gp"},
                "rarity": e.get("rarity", "common"),
            },
        })

    img = _PILE_IMAGES["container"]  # without an img dnd5e gives the token the default NPC art
    return {
        "name": f"{table.get('name', 'Loot')} (Party Stash)",
        "type": "npc",
        "img": img,
        "prototypeToken": {"texture": {"src": img}},
        "items": items,
        "flags": {
            "quartermaster": {
                "stash": True,
                "quest_reward": table.get("is_quest_reward", False),
            },
            "ai-gm": {"loot_table": table.get("name", "Loot")},
        },
    }


register(ModuleIntegration(module_id="quartermaster", on_loot_table=on_loot_table))
