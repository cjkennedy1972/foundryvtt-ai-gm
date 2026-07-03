"""Midi QOL — combat automation flags, spell items, and weapon attack bonuses.

Must register AFTER autoanimations: on_npc reaches into weapon items
autoanimations already created to add an attack bonus.
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


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

    # Enrich weapon items autoanimations already built with an attack bonus.
    if npc.get("attack_bonus") is not None:
        for item in ctx.items:
            if item.get("type") == "weapon":
                item["system"]["attackBonus"] = str(npc["attack_bonus"])

    for spell in npc.get("spells", []):
        if not isinstance(spell, dict) or not spell.get("name"):
            continue
        spell_item = {
            "name": spell["name"],
            "type": "spell",
            "system": {
                "description": {"value": ""},
                "level": spell.get("level", 0),
                "school": spell.get("school", "evocation"),
                "range": {"value": spell.get("range", 0), "units": "ft"},
                "concentration": spell.get("concentration", False),
                "prepared": True,
            },
            "flags": {"midi-qol": {"onUseMacroName": ""}},
        }
        if spell.get("damage"):
            spell_item["system"]["damage"] = {
                "parts": [[spell["damage"], spell.get("damage_type", "")]],
            }
        if spell.get("save"):
            spell_item["system"]["save"] = {
                "ability": spell["save"],
                "dc": spell.get("save_dc", 13),
                "scaling": "flat",
            }
        if spell.get("aoe"):
            spell_item["system"]["target"] = {
                "type": spell["aoe"].get("type", "sphere"),
                "value": spell["aoe"].get("size", 10),
                "units": "ft",
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
