"""Automated Animations — animation flags + weapon items for animation matching.

Must register BEFORE midi_qol: midi-qol's attack-bonus injection reaches
into the weapon items this module creates (see campaign/modules/__init__.py
for the registration order that preserves this).
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    npc = ctx.npc
    if npc.get("animation_type", "none") != "none":
        ctx.flags["autoanimations"] = {
            "killAnim": False,
            "animationType": npc.get("animation_type", "melee"),
        }
    for weapon_name in npc.get("weapon_items", []):
        ctx.items.append({
            "name": weapon_name,
            "type": "weapon",
            "system": {
                "description": {"value": ""},
                "quantity": 1,
                "equipped": True,
            },
        })


def on_encounter_journal(enc: dict, mods: dict):
    return {"enable_spell_animations": True, "enable_melee_animations": True}


register(ModuleIntegration(
    module_id="autoanimations",
    on_npc=on_npc,
    on_encounter_journal=on_encounter_journal,
))
