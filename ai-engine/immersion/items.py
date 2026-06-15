"""Item and loot management for immersive loot distribution."""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LootItem:
    """A loot item that can be distributed."""
    item_id: str
    name: str
    rarity: str  # common, uncommon, rare, very_rare, legendary
    value_gp: float
    weight_lbs: float
    description: str
    quantity: int = 1


class ItemManager:
    """Manage item distribution and loot for encounters."""

    RARITY_COLORS = {
        "common": "#ffffff",
        "uncommon": "#1eff00",
        "rare": "#0070dd",
        "very_rare": "#a335ee",
        "legendary": "#ff8000",
    }

    def __init__(self):
        self.available_items: Dict[str, LootItem] = {}
        self.distributed_items: Dict[str, List[str]] = {}  # actor_id -> item_ids
        self.loot_pools: Dict[str, List[str]] = {}  # pool_name -> item_ids

    def add_item_to_pool(self, pool_name: str, item: LootItem) -> Dict:
        """Add an item to a loot pool."""
        if item.item_id not in self.available_items:
            self.available_items[item.item_id] = item

        if pool_name not in self.loot_pools:
            self.loot_pools[pool_name] = []

        self.loot_pools[pool_name].append(item.item_id)

        logger.info(
            f"[Loot] Added {item.name} ({item.rarity}) to pool {pool_name} "
            f"(value: {item.value_gp}gp)"
        )

        return {
            "type": "item_added_to_pool",
            "pool_name": pool_name,
            "item_id": item.item_id,
            "item_name": item.name,
            "rarity": item.rarity,
        }

    def distribute_item(self, item_id: str, actor_id: str) -> Dict:
        """Distribute an item to an actor."""
        if item_id not in self.available_items:
            return {"error": f"Item not found: {item_id}"}

        item = self.available_items[item_id]

        if actor_id not in self.distributed_items:
            self.distributed_items[actor_id] = []

        self.distributed_items[actor_id].append(item_id)

        logger.info(f"[Loot] Distributed {item.name} to {actor_id} (value: {item.value_gp}gp)")

        return {
            "type": "item_distributed",
            "actor_id": actor_id,
            "item_id": item_id,
            "item_name": item.name,
            "rarity": item.rarity,
            "color": self.RARITY_COLORS.get(item.rarity, "#ffffff"),
        }

    def draw_from_pool(self, pool_name: str, actor_id: str) -> Dict:
        """Draw a random item from a loot pool and distribute it."""
        if pool_name not in self.loot_pools or not self.loot_pools[pool_name]:
            return {"error": f"Pool not found or empty: {pool_name}"}

        import random

        item_ids = self.loot_pools[pool_name]
        item_id = random.choice(item_ids)

        return self.distribute_item(item_id, actor_id)

    def get_actor_inventory(self, actor_id: str) -> Dict:
        """Get all items held by an actor."""
        item_ids = self.distributed_items.get(actor_id, [])
        items = []
        total_value = 0

        for item_id in item_ids:
            if item_id in self.available_items:
                item = self.available_items[item_id]
                items.append(
                    {
                        "id": item.item_id,
                        "name": item.name,
                        "rarity": item.rarity,
                        "value_gp": item.value_gp,
                        "weight_lbs": item.weight_lbs,
                    }
                )
                total_value += item.value_gp

        return {
            "actor_id": actor_id,
            "items": items,
            "item_count": len(items),
            "total_value_gp": total_value,
        }

    def create_loot_pool_from_cr(self, pool_name: str, cr: float) -> Dict:
        """Create a standard loot pool based on Challenge Rating."""
        # D&D 5e DMG loot distribution by CR
        loot_distribution = {
            "mundane_items": max(1, int(cr / 2)),
            "common_items": max(0, int(cr / 3)),
            "uncommon_items": max(0, int(cr / 5)),
            "rare_items": 1 if cr >= 11 else 0,
        }

        logger.info(f"[Loot] Created pool {pool_name} for CR {cr}")

        return {
            "type": "loot_pool_created",
            "pool_name": pool_name,
            "cr": cr,
            "suggested_distribution": loot_distribution,
        }

    def list_available_items(self) -> List[Dict]:
        """List all available items."""
        return [
            {
                "id": item.item_id,
                "name": item.name,
                "rarity": item.rarity,
                "value_gp": item.value_gp,
                "quantity": item.quantity,
            }
            for item in self.available_items.values()
        ]

    def list_loot_pools(self) -> Dict[str, int]:
        """List all loot pools and their item counts."""
        return {
            pool_name: len(item_ids)
            for pool_name, item_ids in self.loot_pools.items()
        }
