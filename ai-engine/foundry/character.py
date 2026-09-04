"""Constrained level-one player-character data used by the Foundry adapter.

The dnd5e system remains authoritative for derived values.  This module only
turns a prose concept into a safe, small set of choices; Foundry creates the
document and its embedded items from those choices.
"""

import re
from typing import Any, Dict


_CLASS_KEYWORDS = {
    "wizard": "wizard", "mage": "wizard", "sorcer": "sorcerer",
    "warlock": "warlock", "cleric": "cleric", "priest": "cleric",
    "druid": "druid", "ranger": "ranger", "rogue": "rogue",
    "thief": "rogue", "bard": "bard", "paladin": "paladin",
    "monk": "monk", "barbarian": "barbarian", "fighter": "fighter",
    "warrior": "fighter", "artificer": "artificer",
}


def character_from_concept(concept: str, name: str = "Adventurer") -> Dict[str, Any]:
    """Return a deterministic, level-one character request from prose.

    No LLM output is trusted for rules data.  The dnd5e sheet applies its own
    defaults and derives HP, AC, saves, and spell slots after creation.
    """
    text = (concept or "").strip()
    lowered = text.lower()
    class_name = next((value for key, value in _CLASS_KEYWORDS.items() if key in lowered), "fighter")
    match = re.search(r"(?:named?|called)\s+([A-Za-z][A-Za-z '-]{1,40})", text, re.I)
    if not match:
        match = re.match(r"([A-Za-z][A-Za-z '-]{1,40}),\s+", text)
    character_name = (match.group(1).strip(" '-") if match else name.strip()) or "Adventurer"
    return {
        "name": character_name[:60],
        "class": class_name,
        "level": 1,
        "race": "human",
        "background": "soldier" if class_name in {"fighter", "paladin", "barbarian"} else "acolyte" if class_name == "cleric" else "sage" if class_name in {"wizard", "artificer"} else "folk-hero",
        "concept": text[:1000],
    }
