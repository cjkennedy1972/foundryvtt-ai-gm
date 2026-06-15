"""Random quest generation."""

import random
from typing import List, Dict
from dataclasses import dataclass

@dataclass
class GeneratedQuest:
    """A procedurally generated quest."""
    title: str
    description: str
    quest_giver: str
    objective: str
    reward: str
    complications: List[str]
    resolution_options: List[str]


class QuestGenerator:
    """Generate random quests."""

    QUEST_HOOKS = [
        "Retrieve",
        "Rescue",
        "Investigate",
        "Destroy",
        "Protect",
        "Steal",
        "Spy on",
        "Negotiate with",
    ]

    QUEST_TARGETS = [
        "a stolen artifact",
        "a kidnapped noble",
        "the source of strange disappearances",
        "a dangerous monster",
        "a village from bandits",
        "a sacred treasure",
        "secrets from a rival",
        "peace between feuding factions",
    ]

    LOCATIONS = [
        "an ancient tomb",
        "a hidden cave system",
        "a distant city",
        "a wizard's tower",
        "a dragon's lair",
        "an abandoned fort",
        "a haunted mansion",
        "an underground dungeon",
    ]

    COMPLICATIONS = [
        "The target is protected by powerful magic",
        "A rival faction also seeks the objective",
        "Local authorities forbid interference",
        "The target is not what it seems",
        "Time is running out",
        "The quest giver is hiding something",
        "Innocents could be harmed",
        "The objective is cursed",
    ]

    REWARDS = [
        "500 gold pieces",
        "a magical item",
        "a favor from a powerful ally",
        "a map to hidden treasure",
        "a mysterious artifact",
        "knowledge of a great secret",
        "land or property",
        "a title or position of power",
    ]

    def __init__(self):
        pass

    def generate(self, level: int = 5) -> GeneratedQuest:
        """Generate a random quest."""
        hook = random.choice(self.QUEST_HOOKS)
        target = random.choice(self.QUEST_TARGETS)
        location = random.choice(self.LOCATIONS)

        title = f"{hook} {target.split()[0].title()}"
        description = f"{hook} {target} from {location}"

        quest_givers = [
            "A desperate merchant",
            "The town mayor",
            "A mysterious stranger",
            "A noble lord",
            "A scholar",
            "A cleric",
            "An old sage",
            "A hooded figure",
        ]

        resolutions = [
            "Complete the objective successfully",
            "Find a peaceful compromise",
            "Uncover the true nature of the problem",
            "Expose the quest giver's deception",
            "Prevent a catastrophe",
            "Claim the objective for yourself",
            "Report to the authorities",
            "Make an alliance with enemies",
        ]

        return GeneratedQuest(
            title=title,
            description=description,
            quest_giver=random.choice(quest_givers),
            objective=f"{hook.lower()} {target} located in {location}",
            reward=random.choice(self.REWARDS),
            complications=random.sample(self.COMPLICATIONS, 2),
            resolution_options=random.sample(resolutions, 3),
        )

    def generate_campaign_arc(self, num_quests: int = 5, level_start: int = 1) -> List[GeneratedQuest]:
        """Generate a connected quest arc."""
        quests = []
        for i in range(num_quests):
            quest = self.generate(level_start + i)
            # Add connections to previous quests
            if i > 0:
                quest.complications.append(f"Connected to the previous quest")
            quests.append(quest)
        return quests
