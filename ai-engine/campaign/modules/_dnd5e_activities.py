"""dnd5e 5.x Activities schema helpers, shared by autoanimations.py and midi_qol.py.

dnd5e 4.0+ replaced the old system.damage/system.save item fields with a
system.activities collection — a dict keyed by 16-char Foundry document ids,
each entry a typed activity ("attack", "save", "damage", ...). Items created
with the old legacy fields (what this codebase generated before) have zero
activities and are NOT usable for a real attack roll in dnd5e 5.x — no
attack button, no damage, nothing functional. Confirmed live against a
running world on dnd5e 5.3.3 / midi-qol 14.0.9.

Schema shape here is minimal but verified: created a real weapon via
Item.create(), inspected the auto-generated default "attack" activity, and
confirmed activity.rollAttack()/.rollDamage() both resolve correctly against
these fields (ability/bonus/type for attack; parts/includeBase for damage).
"""

import random
import string
from typing import Any, Dict, List, Optional

_ID_CHARS = string.ascii_letters + string.digits


def new_activity_id() -> str:
    """A 16-char alphanumeric id matching Foundry's document id format."""
    return "".join(random.choices(_ID_CHARS, k=16))


def build_attack_activity(
    ability: str,
    attack_type: str,
    classification: str,
    damage_parts: List[Dict[str, Any]],
    bonus: str = "",
) -> Dict[str, Any]:
    """One "attack" activity: an attack roll + its damage, dnd5e 5.x shape.

    ability: "str"/"dex"/"spellcasting"/etc.
    attack_type: "melee" | "ranged"
    classification: "weapon" | "spell"
    damage_parts: [{"number": 2, "denomination": 6, "bonus": "3", "types": ["slashing"]}, ...]
    """
    activity_id = new_activity_id()
    return {
        activity_id: {
            "_id": activity_id,
            "type": "attack",
            "activation": {"type": "action"},
            "attack": {
                "ability": ability,
                "bonus": str(bonus) if bonus else "",
                "type": {"value": attack_type, "classification": classification},
            },
            "damage": {
                "parts": damage_parts,
                "includeBase": True,
            },
        }
    }


def build_save_activity(
    ability: str,
    dc: int,
    damage_parts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """One "save" activity: a saving throw, optionally with damage on fail."""
    activity_id = new_activity_id()
    entry: Dict[str, Any] = {
        "_id": activity_id,
        "type": "save",
        "activation": {"type": "action"},
        "save": {
            "ability": [ability],
            "dc": {"calculation": "", "formula": str(dc)},
        },
    }
    if damage_parts:
        entry["damage"] = {"onSave": "half", "parts": damage_parts}
    return {activity_id: entry}


# Rough per-CR damage-die scaling, used only when a weapon has no explicit
# damage formula (the LLM's campaign schema currently generates weapon_items
# as bare names + an optional attack_bonus, with no damage formula field).
def default_weapon_damage(cr: float, weapon_name: str) -> Dict[str, Any]:
    """A reasonable {number, denomination, types} guess from CR + name."""
    if cr >= 15:
        denomination = 12
    elif cr >= 8:
        denomination = 10
    elif cr >= 3:
        denomination = 8
    else:
        denomination = 6

    name_lower = weapon_name.lower()
    if any(k in name_lower for k in ("sword", "axe", "claw", "blade", "scimitar", "cutlass")):
        damage_type = "slashing"
    elif any(k in name_lower for k in ("mace", "hammer", "club", "staff", "fist", "greatclub")):
        damage_type = "bludgeoning"
    else:
        damage_type = "piercing"

    return {"number": 1, "denomination": denomination, "bonus": "", "types": [damage_type]}
