"""Random NPC generation."""

import random
from typing import List, Dict
from dataclasses import dataclass

@dataclass
class GeneratedNPC:
    """A procedurally generated NPC."""
    name: str
    race: str
    class_name: str
    level: int
    personality_traits: List[str]
    ideals: List[str]
    bonds: List[str]
    flaws: List[str]
    appearance: str
    background: str


class NPCGenerator:
    """Generate random NPCs."""

    RACES = ["Human", "Elf", "Dwarf", "Halfling", "Dragonborn", "Gnome", "Half-Orc", "Tiefling"]
    CLASSES = ["Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"]

    NAMES = {
        "human": ["Aldrin", "Bessie", "Cedric", "Daisy", "Ethan", "Fiona"],
        "elf": ["Aelindor", "Belladine", "Caranthan", "Daewen", "Elowen", "Finwe"],
        "dwarf": ["Bordin", "Gimli", "Thorin", "Beyla", "Disa", "Marta"],
        "halfling": ["Bilbo", "Pippin", "Rosie", "Tilly", "Merry", "Daisy"],
        "dragonborn": ["Draxos", "Emberclaw", "Flameheart", "Goldscale", "Ironhorn"],
        "gnome": ["Tinkertop", "Sparklebright", "Jinglebell", "Nimble", "Widget"],
        "half_orc": ["Durotan", "Gronn", "Thrall", "Garona", "Rulkah"],
        "tiefling": ["Zariel", "Mephistopheles", "Infernus", "Inferna", "Blazara"],
    }

    TRAITS = [
        "Cheerful", "Cynical", "Fearless", "Nervous", "Ambitious", "Lazy",
        "Honest", "Deceptive", "Brave", "Cowardly", "Kind", "Cruel",
        "Witty", "Dull", "Mysterious", "Open-minded", "Suspicious", "Trusting",
    ]

    IDEALS = [
        "Honor and duty",
        "Freedom and independence",
        "Knowledge and truth",
        "Wealth and power",
        "Redemption and forgiveness",
        "Chaos and unpredictability",
        "Order and law",
        "Community and cooperation",
    ]

    BONDS = [
        "I owe a debt of loyalty to",
        "I seek revenge against",
        "I am trying to impress",
        "I protect the innocent from",
        "I desire the approval of",
        "I am searching for",
        "I have sworn to defend",
    ]

    FLAWS = [
        "I am too trusting of others",
        "I am prone to rage",
        "I have a forbidden love",
        "I am secretly corrupt",
        "I am haunted by a past mistake",
        "I have a terrible secret",
        "I am addicted to something",
    ]

    APPEARANCES = [
        "tall and muscular with a scarred face",
        "short and stocky with bright eyes",
        "elegant and graceful with flowing hair",
        "weathered and worn from years of travel",
        "young and energetic with an infectious smile",
        "mysterious with hidden tattoos",
        "dressed in fine clothing with jewelry",
        "dressed in rags and tattered cloth",
    ]

    BACKGROUNDS = [
        "A former adventurer settling down",
        "A merchant traveling between cities",
        "A scholar seeking ancient knowledge",
        "A fugitive hiding from the law",
        "A noble fallen from grace",
        "A peasant with dreams of grandeur",
        "A soldier looking for redemption",
        "A craftsperson perfecting their trade",
    ]

    def __init__(self):
        pass

    def generate(self) -> GeneratedNPC:
        """Generate a random NPC."""
        race = random.choice(self.RACES).lower()
        race_key = race.replace(" ", "_")

        names = self.NAMES.get(race_key, self.NAMES["human"])
        name = random.choice(names)

        return GeneratedNPC(
            name=name,
            race=race,
            class_name=random.choice(self.CLASSES),
            level=random.randint(1, 5),
            personality_traits=random.sample(self.TRAITS, 2),
            ideals=random.sample(self.IDEALS, 1),
            bonds=[f"{random.choice(self.BONDS)} a mysterious figure"],
            flaws=random.sample(self.FLAWS, 1),
            appearance=random.choice(self.APPEARANCES),
            background=random.choice(self.BACKGROUNDS),
        )

    def generate_party(self, size: int = 4, level: int = 5) -> List[GeneratedNPC]:
        """Generate a full party of NPCs."""
        party = []
        classes_used = []

        # Ensure party has diversity
        required_classes = ["Fighter", "Rogue", "Cleric", "Wizard"]

        for i in range(size):
            if i < len(required_classes) and required_classes[i] not in classes_used:
                npc = self.generate()
                npc.class_name = required_classes[i]
                npc.level = level
                party.append(npc)
                classes_used.append(npc.class_name)
            else:
                npc = self.generate()
                npc.level = level
                party.append(npc)

        return party
