"""Master procedural content generator."""

from procedural.encounters import EncounterGenerator
from procedural.treasures import TreasureGenerator
from procedural.npcs import NPCGenerator
from procedural.quests import QuestGenerator
from procedural.settlement_gen import SettlementGenerator
from procedural.settlement import Settlement, SettlementSize, Building, SettlementNPC


class ProceduralGenerator:
    """Master generator for all procedural content."""

    def __init__(self):
        self.encounter_gen = EncounterGenerator()
        self.treasure_gen = TreasureGenerator()
        self.npc_gen = NPCGenerator()
        self.quest_gen = QuestGenerator()
        self.settlement_gen = SettlementGenerator()

    def generate_session(self, party_level: int, party_size: int = 4, include_settlement: bool = False):
        """Generate a full session's worth of content."""
        session = {
            "encounters": [
                self.encounter_gen.generate("medium", party_level, party_size),
                self.encounter_gen.generate("hard", party_level, party_size),
            ],
            "quests": [
                self.quest_gen.generate(party_level),
                self.quest_gen.generate(party_level),
            ],
            "npcs": self.npc_gen.generate_party(4, party_level),
        }
        if include_settlement:
            session["settlement"] = self.generate_settlement()
        return session

    def generate_campaign_week(self, party_level: int, party_size: int = 4):
        """Generate a week's campaign content."""
        return {
            "day_1": self.generate_session(party_level, party_size),
            "day_2": self.generate_session(party_level, party_size),
            "day_3": self.generate_session(party_level, party_size),
            "day_4": self.generate_session(party_level, party_size),
            "day_5": self.generate_session(party_level, party_size),
            "quests": self.quest_gen.generate_campaign_arc(5, party_level),
        }

    def roll_all(self, category: str, **kwargs):
        """Generate content from a specific category."""
        generators = {
            "encounter": self.encounter_gen.generate,
            "treasure": self.treasure_gen.generate,
            "npc": self.npc_gen.generate,
            "quest": self.quest_gen.generate,
            "party": self.npc_gen.generate_party,
            "settlement": lambda **kw: self.settlement_gen.generate(**kw),
        }

        if category not in generators:
            return {"error": f"Unknown category: {category}"}

        if category == "settlement":
            return generators[category](**kwargs)

        return generators[category](**kwargs)
