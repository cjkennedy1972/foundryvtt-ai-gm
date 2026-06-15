"""D&D 5e rules database — spells, classes, conditions, abilities."""

# Ability scores and their modifiers
ABILITY_SCORES = {
    "strength": {"abbrev": "STR"},
    "dexterity": {"abbrev": "DEX"},
    "constitution": {"abbrev": "CON"},
    "intelligence": {"abbrev": "INT"},
    "wisdom": {"abbrev": "WIS"},
    "charisma": {"abbrev": "CHA"},
}

# Common conditions in D&D 5e
CONDITIONS = {
    "blinded": "Blind creatures have disadvantage on attack rolls and gain advantage on attacks against the blinded creature.",
    "charmed": "A charmed creature has disadvantage on attack rolls against its charmer and its allies.",
    "deafened": "A deafened creature can't hear and automatically fails checks requiring hearing.",
    "frightened": "A frightened creature has disadvantage on attack rolls and ability checks while its source is in sight.",
    "grappled": "A grappled creature's speed becomes 0 and cannot benefit from bonuses to speed.",
    "incapacitated": "An incapacitated creature can't move or take actions.",
    "invisible": "An invisible creature can't be seen. Attack rolls have disadvantage if target isn't heard.",
    "paralyzed": "A paralyzed creature is incapacitated and can't move or speak.",
    "petrified": "A petrified creature is incapacitated, can't move or speak, and is unaware of surroundings.",
    "poisoned": "A poisoned creature has disadvantage on attack rolls and ability checks.",
    "prone": "A prone creature can only move by crawling. Melee attack rolls have disadvantage against prone.",
    "restrained": "A restrained creature's speed becomes 0. Attack rolls against it have advantage, its own have disadvantage.",
    "stunned": "A stunned creature is incapacitated, can't move, and can speak only falteringly.",
    "unconscious": "Unconscious creature is incapacitated, can't move or speak, and is unaware.",
    "exhaustion": "Exhaustion has 6 levels. Each level imposes disadvantage on ability checks.",
}

# Common spells by level (sample for demonstration)
SPELLS = {
    "magic missile": {
        "level": 1,
        "school": "evocation",
        "casting_time": "1 action",
        "range": "120 feet",
        "duration": "instantaneous",
        "components": ["V", "S"],
        "description": "You hurl magical force at a creature you can see. The spell creates more than one missile.",
    },
    "fireball": {
        "level": 3,
        "school": "evocation",
        "casting_time": "1 action",
        "range": "150 feet",
        "duration": "instantaneous",
        "components": ["V", "S", "M (a tiny ball of bat guano and sulfur)"],
        "description": "A bright streak flashes from your pointing finger to a point of your choice within range.",
    },
    "cure wounds": {
        "level": 1,
        "school": "evocation",
        "casting_time": "1 action",
        "range": "Touch",
        "duration": "instantaneous",
        "components": ["V", "S"],
        "description": "A creature you touch regains hit points equal to 1d8 + your spellcasting ability modifier.",
    },
    "polymorph": {
        "level": 4,
        "school": "transmutation",
        "casting_time": "1 action",
        "range": "60 feet",
        "duration": "concentration, up to 1 hour",
        "components": ["V", "S", "M (a caterpillar cocoon)"],
        "description": "This spell transforms a creature that you can see within range into a new form.",
    },
    "wish": {
        "level": 9,
        "school": "conjuration",
        "casting_time": "1 action",
        "range": "Self",
        "duration": "instantaneous",
        "components": ["V"],
        "description": "Wish is the mightiest spell a mortal creature can cast. By simply speaking aloud, you can alter the very foundations of reality.",
    },
}

# Skill to ability mapping
SKILL_ABILITIES = {
    "acrobatics": "dexterity",
    "animal_handling": "wisdom",
    "arcana": "intelligence",
    "athletics": "strength",
    "deception": "charisma",
    "history": "intelligence",
    "insight": "wisdom",
    "intimidation": "charisma",
    "investigation": "intelligence",
    "medicine": "wisdom",
    "nature": "intelligence",
    "perception": "wisdom",
    "performance": "charisma",
    "persuasion": "charisma",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "survival": "wisdom",
}

# Typical DC values for skill checks
DC_BY_DIFFICULTY = {
    "very_easy": 5,
    "easy": 10,
    "medium": 15,
    "hard": 20,
    "very_hard": 25,
    "nearly_impossible": 30,
}

# Class hit dice
CLASS_HIT_DICE = {
    "barbarian": 12,
    "bard": 8,
    "cleric": 8,
    "druid": 8,
    "fighter": 10,
    "monk": 8,
    "paladin": 10,
    "ranger": 10,
    "rogue": 8,
    "sorcerer": 6,
    "warlock": 8,
    "wizard": 6,
}

# Class proficiencies (simplified)
CLASS_PROFICIENCIES = {
    "barbarian": ["strength_saves", "constitution_saves"],
    "bard": ["charisma_saves", "dexterity_saves"],
    "cleric": ["wisdom_saves", "charisma_saves"],
    "druid": ["intelligence_saves", "wisdom_saves"],
    "fighter": ["strength_saves", "constitution_saves"],
    "monk": ["strength_saves", "dexterity_saves"],
    "paladin": ["wisdom_saves", "charisma_saves"],
    "ranger": ["strength_saves", "dexterity_saves"],
    "rogue": ["dexterity_saves", "intelligence_saves"],
    "sorcerer": ["charisma_saves", "constitution_saves"],
    "warlock": ["wisdom_saves", "charisma_saves"],
    "wizard": ["intelligence_saves", "wisdom_saves"],
}

# Spell slots per class and level (wizard example)
SPELL_SLOTS = {
    "wizard": {
        "1": {"1st": 2, "2nd": 0, "3rd": 0, "4th": 0, "5th": 0, "6th": 0, "7th": 0, "8th": 0, "9th": 0},
        "2": {"1st": 3, "2nd": 0, "3rd": 0, "4th": 0, "5th": 0, "6th": 0, "7th": 0, "8th": 0, "9th": 0},
        "3": {"1st": 4, "2nd": 2, "3rd": 0, "4th": 0, "5th": 0, "6th": 0, "7th": 0, "8th": 0, "9th": 0},
        "5": {"1st": 4, "2nd": 3, "3rd": 2, "4th": 0, "5th": 0, "6th": 0, "7th": 0, "8th": 0, "9th": 0},
        "9": {"1st": 4, "2nd": 3, "3rd": 3, "4th": 3, "5th": 1, "6th": 0, "7th": 0, "8th": 0, "9th": 0},
        "17": {"1st": 4, "2nd": 3, "3rd": 3, "4th": 3, "5th": 2, "6th": 1, "7th": 0, "8th": 0, "9th": 0},
        "20": {"1st": 4, "2nd": 3, "3rd": 3, "4th": 3, "5th": 3, "6th": 1, "7th": 1, "8th": 1, "9th": 1},
    }
}
