"""Automated Animations — animation flags + weapon items for animation matching.

Must register BEFORE midi_qol: midi-qol's attack-bonus injection reaches
into the weapon items this module creates (see campaign/modules/__init__.py
for the registration order that preserves this).
"""

from campaign.modules._dnd5e_activities import build_attack_activity, default_weapon_damage
from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    npc = ctx.npc
    if npc.get("animation_type", "none") != "none":
        ctx.flags["autoanimations"] = {
            "killAnim": False,
            "animationType": npc.get("animation_type", "melee"),
        }
    for weapon_name in npc.get("weapon_items", []):
        # dnd5e 5.x resolves attacks through system.activities, not the old
        # system.damage/system.save fields — an item with neither has no
        # working attack roll at all (verified live: zero activities, zero
        # legacy damage on every weapon this deploy path created before).
        # The campaign schema doesn't carry a real damage formula for weapon
        # items yet (only a name + optional attack_bonus), so this is a
        # reasonable default from CR + weapon name, same spirit as
        # _default_monster_icon's name-based fallback elsewhere.
        damage_part = default_weapon_damage(npc.get("cr", 1), weapon_name)
        activities = build_attack_activity(
            ability="str",
            attack_type="melee",
            classification="weapon",
            damage_parts=[damage_part],
        )
        ctx.items.append({
            "name": weapon_name,
            "type": "weapon",
            "system": {
                "description": {"value": ""},
                "quantity": 1,
                "equipped": True,
                "activities": activities,
            },
        })


def on_encounter_journal(enc: dict, mods: dict):
    return {"enable_spell_animations": True, "enable_melee_animations": True}


register(ModuleIntegration(
    module_id="autoanimations",
    on_npc=on_npc,
    on_encounter_journal=on_encounter_journal,
))
