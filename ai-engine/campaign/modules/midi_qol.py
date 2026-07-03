"""Midi QOL — combat automation flags, spell items, and weapon attack bonuses.

Must register AFTER autoanimations: on_npc reaches into weapon items
autoanimations already created to add an attack bonus.
"""

import re

from campaign.modules._dnd5e_activities import build_attack_activity, build_save_activity
from campaign.modules.registry import ModuleIntegration, NpcContext, register

_DICE_RE = re.compile(r"(\d+)\s*d\s*(\d+)\s*(?:\+\s*(\d+))?")


def _parse_damage_formula(formula: str, damage_type: str) -> list:
    """"1d10" / "2d6+3" -> [{number, denomination, bonus, types}]. Falls back
    to a flat 1d6 if the LLM produced something the regex can't parse."""
    m = _DICE_RE.search(formula or "")
    number, denomination, bonus = (int(m.group(1)), int(m.group(2)), m.group(3) or "") if m else (1, 6, "")
    return [{"number": number, "denomination": denomination, "bonus": bonus, "types": [damage_type or "force"]}]


def on_npc(ctx: NpcContext) -> None:
    npc = ctx.npc
    midi_flags = {
        "concentration-automation": npc.get("concentration_caster", False),
        "critThreshold": npc.get("critical_threshold", 20),
        "allowUseMacro": npc.get("use_macros", False),
    }
    if npc.get("auto_damage_type"):
        midi_flags["autoApplyDamage"] = npc.get("auto_damage_type") in ["auto", "both"]
    if npc.get("disadvantage_attacks"):
        midi_flags["disadvantageAttacks"] = True
    ctx.flags["midi-qol"] = midi_flags

    # Enrich weapon items autoanimations already built with an attack bonus —
    # on the activity's attack.bonus (dnd5e 5.x), not the legacy
    # system.attackBonus field dnd5e no longer reads for the actual roll.
    if npc.get("attack_bonus") is not None:
        bonus_str = str(npc["attack_bonus"])
        for item in ctx.items:
            if item.get("type") != "weapon":
                continue
            activities = item.get("system", {}).get("activities") or {}
            for activity in activities.values():
                if activity.get("type") == "attack":
                    activity["attack"]["bonus"] = bonus_str
                    for part in activity.get("damage", {}).get("parts", []):
                        part["bonus"] = bonus_str

    for spell in npc.get("spells", []):
        if not isinstance(spell, dict) or not spell.get("name"):
            continue
        # dnd5e 5.x needs a real activity for the spell to be usable — build
        # a "save" activity for save spells, an "attack" activity for spells
        # that deal damage via a spell attack roll (Fire Bolt, Ray of Frost
        # style), or leave utility spells (no damage/save) without one, same
        # as the original code left them functionally inert.
        activities = {}
        if spell.get("save"):
            damage_parts = (
                _parse_damage_formula(spell["damage"], spell.get("damage_type", ""))
                if spell.get("damage") else None
            )
            activities = build_save_activity(
                ability=spell["save"], dc=spell.get("save_dc", 13), damage_parts=damage_parts,
            )
        elif spell.get("damage"):
            damage_parts = _parse_damage_formula(spell["damage"], spell.get("damage_type", ""))
            attack_type = "ranged" if (spell.get("range", 0) or 0) > 5 else "melee"
            activities = build_attack_activity(
                ability="spellcasting", attack_type=attack_type, classification="spell",
                damage_parts=damage_parts,
            )

        spell_item = {
            "name": spell["name"],
            "type": "spell",
            "system": {
                "description": {"value": ""},
                "level": spell.get("level", 0),
                "school": spell.get("school", "evocation"),
                "range": {"value": spell.get("range", 0), "units": "ft"},
                "target": {
                    "template": {"units": "ft"},
                    "affects": {},
                } if not spell.get("aoe") else {
                    "template": {
                        "type": spell["aoe"].get("type", "sphere"),
                        "size": spell["aoe"].get("size", 10),
                        "units": "ft",
                    },
                    "affects": {},
                },
                "properties": ["concentration"] if spell.get("concentration") else [],
                "activities": activities,
            },
            "flags": {"midi-qol": {"onUseMacroName": ""}},
        }
        ctx.items.append(spell_item)


def on_encounter_journal(enc: dict, mods: dict):
    return {
        "use_midi_rolls": True,
        "auto_apply_damage": enc.get("midi_qol", {}).get("auto_damage", True),
        "concentration_penalty": True,
    }


register(ModuleIntegration(
    module_id="midi-qol",
    on_npc=on_npc,
    on_encounter_journal=on_encounter_journal,
))
