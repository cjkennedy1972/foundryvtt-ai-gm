"""Random encounter generation."""

import random
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class GeneratedEncounter:
    """A procedurally generated encounter."""
    name: str
    description: str
    monsters: List[Dict]
    difficulty: str
    environment: str
    hooks: List[str]
    treasure_cr: float


class EncounterGenerator:
    """Generate random encounters."""

    # Monster types by CR
    MONSTERS_BY_CR = {
        0.25: ["Goblin", "Skeleton", "Bandit"],
        0.5: ["Orc", "Dire Wolf", "Harpy"],
        1: ["Ghoul", "Bugbear", "Wererat"],
        2: ["Ogre", "Gargoyle", "Wyvern"],
        3: ["Manticore", "Giant Spider", "Troll"],
        4: ["Fire Elemental", "Chimera", "Helmed Horror"],
        5: ["Demon", "Devil", "Beholder"],
    }

    ENVIRONMENTS = [
        "Forest clearing",
        "Mountain pass",
        "Dungeon corridor",
        "Coastal cave",
        "Underground cavern",
        "Ancient ruin",
        "Swamp marsh",
        "Tower chamber",
        "Castle courtyard",
        "Abandoned village",
    ]

    def __init__(self):
        pass

    def generate(
        self, difficulty: str, party_level: int, party_size: int = 4
    ) -> GeneratedEncounter:
        """Generate a random encounter."""
        from combat.difficulty import DynamicDifficulty, EncounterDifficulty

        difficulty_enum = EncounterDifficulty[difficulty.upper()]
        difficulty_engine = DynamicDifficulty()
        party = difficulty_engine.get_party_composition(party_size, float(party_level))

        # Generate monster distribution
        monsters = self._generate_monster_list(difficulty_enum, party_level, party_size)
        environment = random.choice(self.ENVIRONMENTS)

        # Create encounter
        encounter = GeneratedEncounter(
            name=self._generate_name(environment),
            description=self._generate_description(environment, monsters),
            monsters=monsters,
            difficulty=difficulty,
            environment=environment,
            hooks=self._generate_hooks(environment, len(monsters)),
            treasure_cr=sum(m.get("cr", 0) for m in monsters),
        )

        return encounter

    def _generate_monster_list(
        self, difficulty: str, party_level: int, party_size: int
    ) -> List[Dict]:
        """Generate a list of monsters for the encounter."""
        monsters = []

        # Create 1-3 monsters based on difficulty
        num_monsters = random.randint(1, 3)
        total_cr = 0
        target_cr = party_level * party_size * (0.5 if difficulty == "easy" else 1.0 if difficulty == "medium" else 1.5)

        for i in range(num_monsters):
            if total_cr >= target_cr:
                break

            # Pick a CR appropriate for the party
            cr = random.choice([0.25, 0.5, 1, 2, 3, 4, 5])
            if cr > party_level:
                cr = max(0.25, party_level - 1)

            monster_name = random.choice(self.MONSTERS_BY_CR.get(cr, ["Goblin"]))
            monsters.append({
                "name": monster_name,
                "cr": cr,
                "count": random.randint(1, 2),
            })
            total_cr += cr

        return monsters

    def _generate_name(self, environment: str) -> str:
        """Generate an encounter name."""
        prefixes = ["The", "Ambush at", "Battle in the", "Siege of", "Strange happenings"]
        return f"{random.choice(prefixes)} {environment}"

    def _generate_description(self, environment: str, monsters: List[Dict]) -> str:
        """Generate encounter description."""
        monster_types = ", ".join(m["name"] for m in monsters)
        descriptions = [
            f"A group of {monster_types} guard {environment}.",
            f"You stumble upon {monster_types} camping in {environment}.",
            f"A desperate battle rages as {monster_types} attack from {environment}.",
            f"The sound of combat draws you to {environment}, where {monster_types} are fighting.",
        ]
        return random.choice(descriptions)

    def _generate_hooks(self, environment: str, monster_count: int) -> List[str]:
        """Generate roleplay hooks for the encounter."""
        hooks = [
            "What brought these creatures here?",
            "Can any of them be reasoned with?",
            f"Is {environment} their home or a temporary camp?",
            "Are there clues about their leader or organization?",
            "Could they be escaped prisoners or enslaved creatures?",
        ]
        return random.sample(hooks, min(3, len(hooks)))
