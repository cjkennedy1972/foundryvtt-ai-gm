"""Random treasure and loot generation."""

import random
from typing import List, Dict
from dataclasses import dataclass

@dataclass
class GeneratedTreasure:
    """A procedurally generated treasure."""
    gold: int
    gems: List[Dict]
    items: List[Dict]
    magical_items: List[Dict]
    total_value: int


class TreasureGenerator:
    """Generate random treasure and loot."""

    GEM_TYPES = {
        "10gp": ["Azurite", "Banded agate", "Blue quartz", "Eye agate"],
        "50gp": ["Bloodstone", "Carnelian", "Chalcedony", "Jasper"],
        "100gp": ["Alexandrite", "Amethyst", "Aquamarine", "Garnet"],
        "500gp": ["Black opal", "Blue sapphire", "Emerald", "Fire opal"],
        "1000gp": ["Black sapphire", "Diamond", "Jacinth", "Ruby"],
    }

    MAGICAL_ITEMS = {
        "common": [
            {"name": "Potion of Healing", "value": "50gp", "rarity": "common"},
            {"name": "Scroll of Light", "value": "25gp", "rarity": "common"},
            {"name": "Dust of Sneezing and Choking", "value": "50gp", "rarity": "common"},
        ],
        "uncommon": [
            {"name": "Boots of Speed", "value": "500gp", "rarity": "uncommon"},
            {"name": "Cloak of Billowing", "value": "100gp", "rarity": "uncommon"},
            {"name": "Rope of Entanglement", "value": "500gp", "rarity": "uncommon"},
        ],
        "rare": [
            {"name": "Bag of Holding", "value": "2500gp", "rarity": "rare"},
            {"name": "Wand of Fireball", "value": "5000gp", "rarity": "rare"},
            {"name": "Cloak of Invisibility", "value": "7500gp", "rarity": "rare"},
        ],
        "very_rare": [
            {"name": "Ring of Wishes (1 charge)", "value": "10000gp", "rarity": "very_rare"},
            {"name": "Artifact (minor)", "value": "25000gp", "rarity": "very_rare"},
        ],
    }

    MUNDANE_ITEMS = [
        {"name": "Silk tapestry", "value": "100-500gp"},
        {"name": "Jade statue", "value": "500-1000gp"},
        {"name": "Pearl necklace", "value": "250-750gp"},
        {"name": "Gold chalice", "value": "150-400gp"},
        {"name": "Silver mirror", "value": "50-200gp"},
        {"name": "Copper bowl", "value": "10-50gp"},
    ]

    def __init__(self):
        pass

    def generate(self, treasure_cr: float, level: int = 5) -> GeneratedTreasure:
        """Generate treasure based on monster CR."""
        gold = self._generate_gold(treasure_cr, level)
        gems = self._generate_gems(treasure_cr)
        items = self._generate_mundane_items(treasure_cr)
        magical = self._generate_magical_items(treasure_cr, level)

        total_value = gold + sum(self._estimate_value(g.get("value", "10gp")) for g in gems)
        total_value += sum(self._estimate_value(i.get("value", "100gp")) for i in items)
        total_value += sum(self._estimate_value(m.get("value", "500gp")) for m in magical)

        return GeneratedTreasure(
            gold=gold,
            gems=gems,
            items=items,
            magical_items=magical,
            total_value=total_value,
        )

    def _generate_gold(self, treasure_cr: float, level: int) -> int:
        """Generate gold coins."""
        base_gold = int(treasure_cr * 100)
        variance = random.randint(-20, 20)
        return max(10, base_gold + (level * 10) + (variance // 10))

    def _generate_gems(self, treasure_cr: float) -> List[Dict]:
        """Generate gems."""
        gems = []
        gem_count = random.randint(0, int(treasure_cr) + 1)

        for _ in range(gem_count):
            # Higher CR = higher value gems
            if treasure_cr >= 5:
                gem_value = random.choice(["1000gp", "500gp"])
            elif treasure_cr >= 3:
                gem_value = random.choice(["500gp", "100gp"])
            else:
                gem_value = random.choice(["100gp", "50gp", "10gp"])

            gem_name = random.choice(self.GEM_TYPES[gem_value])
            gems.append({"name": gem_name, "value": gem_value})

        return gems

    def _generate_mundane_items(self, treasure_cr: float) -> List[Dict]:
        """Generate mundane items."""
        items = []
        item_count = random.randint(0, 3)

        for _ in range(item_count):
            items.append(random.choice(self.MUNDANE_ITEMS))

        return items

    def _generate_magical_items(self, treasure_cr: float, level: int) -> List[Dict]:
        """Generate magical items."""
        magical = []

        # Higher CR and level = better magical items
        if treasure_cr >= 5 or level >= 10:
            rarity = "very_rare" if random.random() < 0.3 else "rare"
        elif treasure_cr >= 3 or level >= 7:
            rarity = "rare" if random.random() < 0.4 else "uncommon"
        elif treasure_cr >= 1:
            rarity = "uncommon" if random.random() < 0.6 else "common"
        else:
            rarity = "common"

        if random.random() < 0.5:  # 50% chance of magical item
            magical.append(random.choice(self.MAGICAL_ITEMS[rarity]))

        return magical

    def _estimate_value(self, value_str: str) -> int:
        """Estimate gold value from string like '100gp' or '100-500gp'."""
        if "-" in value_str:
            parts = value_str.replace("gp", "").split("-")
            return int(int(parts[0]) + int(parts[1])) // 2
        else:
            return int(value_str.replace("gp", ""))
