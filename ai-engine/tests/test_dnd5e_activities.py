"""Checks for the dnd5e 5.x Activities schema helpers.

Schema shape here isn't invented — it was reverse-engineered live against a
real running Foundry v14 / dnd5e 5.3.3 / midi-qol 14.0.9 world: created a
real weapon Item, inspected its auto-generated default "attack" activity,
and confirmed activity.rollAttack()/.rollDamage() both resolve correctly
against exactly these fields.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from campaign.modules._dnd5e_activities import (
    build_attack_activity,
    build_save_activity,
    default_weapon_damage,
    new_activity_id,
)


def test_new_activity_id_matches_foundry_document_id_format():
    activity_id = new_activity_id()
    assert len(activity_id) == 16
    assert activity_id.isalnum()


def test_new_activity_id_is_unique_per_call():
    assert new_activity_id() != new_activity_id()


def test_build_attack_activity_shape():
    activities = build_attack_activity(
        ability="str", attack_type="melee", classification="weapon",
        damage_parts=[{"number": 2, "denomination": 6, "bonus": "3", "types": ["slashing"]}],
        bonus="5",
    )
    assert len(activities) == 1
    activity_id, activity = next(iter(activities.items()))
    assert activity["_id"] == activity_id
    assert activity["type"] == "attack"
    assert activity["attack"] == {
        "ability": "str", "bonus": "5",
        "type": {"value": "melee", "classification": "weapon"},
    }
    assert activity["damage"]["includeBase"] is True
    assert activity["damage"]["parts"][0]["denomination"] == 6


def test_build_attack_activity_defaults_bonus_to_empty_string():
    activities = build_attack_activity(ability="dex", attack_type="ranged", classification="weapon", damage_parts=[])
    activity = next(iter(activities.values()))
    assert activity["attack"]["bonus"] == ""


def test_build_save_activity_with_damage_on_fail():
    activities = build_save_activity(
        ability="dex", dc=15,
        damage_parts=[{"number": 1, "denomination": 10, "bonus": "", "types": ["fire"]}],
    )
    activity = next(iter(activities.values()))
    assert activity["type"] == "save"
    assert activity["save"] == {"ability": ["dex"], "dc": {"calculation": "", "formula": "15"}}
    assert activity["damage"]["onSave"] == "half"
    assert activity["damage"]["parts"][0]["denomination"] == 10


def test_build_save_activity_without_damage_has_no_damage_key():
    activities = build_save_activity(ability="wis", dc=12)
    activity = next(iter(activities.values()))
    assert "damage" not in activity


def test_default_weapon_damage_scales_die_with_cr():
    assert default_weapon_damage(1, "Dagger")["denomination"] == 6
    assert default_weapon_damage(5, "Dagger")["denomination"] == 8
    assert default_weapon_damage(10, "Dagger")["denomination"] == 10
    assert default_weapon_damage(20, "Dagger")["denomination"] == 12


def test_default_weapon_damage_infers_type_from_name():
    assert default_weapon_damage(1, "Longsword")["types"] == ["slashing"]
    assert default_weapon_damage(1, "War Hammer")["types"] == ["bludgeoning"]
    assert default_weapon_damage(1, "Shortbow")["types"] == ["piercing"]  # fallback
