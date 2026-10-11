"""Branch coverage for the small campaign/modules/* integrations."""

import asyncio

from campaign.modules import (
    aura_effects, betterroofs, dynamic_soundscapes, fog_weaver, inactive_tokens,
    item_piles, levels, light_switch, midi_qol, portal, quartermaster, smalltime,
)
from campaign.modules import patrol
from campaign.modules.registry import NpcContext


def _ctx(npc):
    return NpcContext(npc=npc, mods={})


def test_aura_effects_flags_only_npcs_with_auras():
    c = _ctx({"auras": [{"r": 10}]})
    aura_effects.on_npc(c)
    assert c.prototype_token["flags"]["aura-effects"] == {"auras": [{"r": 10}]}
    c2 = _ctx({})
    aura_effects.on_npc(c2)
    assert c2.prototype_token == {}


def test_inactive_tokens_marks_hidden_and_ambushers():
    for npc in ({"hidden": True}, {"is_ambusher": True}):
        c = _ctx(npc)
        inactive_tokens.on_npc(c)
        assert c.prototype_token["flags"]["inactive-tokens"] == {"inactive": True}
    c = _ctx({"hidden": False})
    inactive_tokens.on_npc(c)
    assert c.prototype_token == {}


def test_portal_marks_summons_and_reinforcements():
    for npc in ({"is_summon": True}, {"reinforcement_type": "wave"}):
        c = _ctx(npc)
        portal.on_npc(c)
        assert c.prototype_token["flags"]["portal"] == {
            "placementEffect": True, "sourceType": "teleport"}
    c = _ctx({})
    portal.on_npc(c)
    assert c.prototype_token == {}


def test_patrol_route_and_non_guard():
    c = _ctx({"npc_type": "guard", "patrol_route": [[1, 2]]})
    patrol.on_npc(c)
    cfg = c.prototype_token["flags"]["patrol"]
    assert cfg == {"active": True, "speed": 1, "pause": 3000, "route": [[1, 2]]}
    c = _ctx({"npc_type": "guard"})
    patrol.on_npc(c)
    assert "route" not in c.prototype_token["flags"]["patrol"]
    c = _ctx({"npc_type": "merchant"})
    patrol.on_npc(c)
    assert c.prototype_token == {}


def test_scene_modules_prefer_explicit_module_flags():
    cfg = {"custom": 1}
    for mod, key in (
        (betterroofs, "betterroofs"), (dynamic_soundscapes, "dynamic-soundscapes"),
        (fog_weaver, "fog-weaver"), (levels, "levels"), (smalltime, "smalltime"),
    ):
        assert mod.on_scene({"module_flags": {key: cfg}}, {}) is cfg


def test_scene_modules_defaults_and_none():
    assert betterroofs.on_scene({"has_roof": True}, {}) == {"roofEnabled": True}
    assert betterroofs.on_scene({}, {}) is None
    assert dynamic_soundscapes.on_scene({}, {}) is None
    assert dynamic_soundscapes.on_scene({"soundscape": "cave"}, {}) == {
        "ambient": True, "preset": "cave", "volume": 0.6}
    assert dynamic_soundscapes.on_playlist({}, {}) == {"ambient": True}
    assert fog_weaver.on_scene({}, {}) is None
    assert fog_weaver.on_scene({"fog_type": "mist", "fog_density": 0.5}, {}) == {
        "fogType": "mist", "fogDensity": 0.5, "enabled": True}
    assert levels.on_scene({}, {}) is None
    assert levels.on_scene({"has_multiple_floors": True, "floors": [1, 2]}, {}) == {
        "sceneLevels": [1, 2]}
    assert levels.on_scene({"has_multiple_floors": True}, {}) is None
    assert smalltime.on_scene({}, {}) is None
    assert smalltime.on_scene({"time_of_day": 6}, {}) == {
        "timeOfDay": 6, "timePeriod": "afternoon"}


def test_light_switch_requires_lights():
    assert light_switch.on_scene({"scene_setup": {}}, {}) is None
    out = light_switch.on_scene({"scene_setup": {"lights": [{"x": 1}]}}, {})
    assert out == {"lights": [{"x": 1}], "interactive": True}


def test_item_piles_merchant_flag():
    c = _ctx({"npc_type": "merchant"})
    item_piles.on_npc(c)
    assert c.flags["item-piles"]["data"]["isMerchant"] is True
    c = _ctx({"npc_type": "guard"})
    item_piles.on_npc(c)
    assert c.flags == {}


def test_item_piles_loot_table_opt_out_and_item_normalisation():
    assert asyncio.run(item_piles.on_loot_table(
        {"name": "T", "deploy_as_pile": False}, {})) is None
    table = {"name": "T", "pile_type": "chest", "entries": [
        {"name": "Coins", "foundry_item_type": "currency"},
        {"name": "Ring", "foundry_item_type": "ring"},
        {"name": "Odd", "foundry_item_type": "nonsense"},
    ]}
    actor = asyncio.run(item_piles.on_loot_table(table, {}))
    types = {i["name"]: i["type"] for i in actor["items"]}
    assert types == {"Ring": "equipment", "Odd": "loot"}   # currency skipped
    assert actor["flags"]["item-piles"]["data"]["type"] == "container"
    assert actor["flags"]["item-piles"]["data"]["displayOne"] is False
    assert actor["name"] == "T (Loot)"


def test_item_piles_unknown_pile_type_falls_back_to_pile():
    actor = asyncio.run(item_piles.on_loot_table(
        {"name": "T", "pile_type": "bogus", "entries": [{"name": "x"}]}, {}))
    assert actor["flags"]["item-piles"]["data"]["type"] == "pile"
    assert actor["flags"]["item-piles"]["data"]["displayOne"] is True
    assert actor["img"] == item_piles._PILE_IMAGES["pile"]


def test_quartermaster_only_party_loot():
    assert asyncio.run(quartermaster.on_loot_table({"name": "x"}, {})) is None
    table = {"name": "Haul", "loot_type": "party", "entries": [
        {"name": "Gold", "foundry_item_type": "currency"},
        {"name": "Gem", "value_gp": 50}]}
    actor = asyncio.run(quartermaster.on_loot_table(table, {}))
    assert [i["name"] for i in actor["items"]] == ["Gem"]
    assert actor["flags"]["quartermaster"] == {"stash": True, "quest_reward": False}
    q = asyncio.run(quartermaster.on_loot_table(
        {"name": "Q", "is_quest_reward": True, "entries": []}, {}))
    assert q["flags"]["quartermaster"]["quest_reward"] is True


def test_midi_attack_bonus_only_touches_weapon_attack_activities():
    weapon = {"type": "weapon", "system": {"activities": {
        "a": {"type": "attack", "attack": {}, "damage": {"parts": [{"bonus": ""}]}},
        "b": {"type": "save"}}}}
    gear = {"type": "equipment", "system": {"activities": {
        "c": {"type": "attack", "attack": {}}}}}
    c = _ctx({"attack_bonus": 5})
    c.items.extend([gear, weapon])
    midi_qol.on_npc(c)
    acts = weapon["system"]["activities"]
    assert acts["a"]["attack"]["bonus"] == "5"
    assert acts["a"]["damage"]["parts"][0]["bonus"] == "5"
    assert "bonus" not in acts["b"]
    assert "bonus" not in gear["system"]["activities"]["c"]["attack"]


def test_midi_spells_save_attack_aoe_and_skips():
    c = _ctx({"spells": [
        "not a dict", {"level": 1},
        {"name": "Fireball", "save": "dex", "damage": "8d6", "damage_type": "fire",
         "aoe": {"type": "sphere", "size": 20}, "concentration": True},
        {"name": "Fire Bolt", "damage": "1d10", "range": 120},
        {"name": "Shocking Grasp", "damage": "1d8", "range": 5},
        {"name": "Light"},
    ]})
    midi_qol.on_npc(c)
    items = {i["name"]: i for i in c.items}
    assert set(items) == {"Fireball", "Fire Bolt", "Shocking Grasp", "Light"}
    fb = items["Fireball"]["system"]
    assert fb["target"]["template"] == {"type": "sphere", "size": 20, "units": "ft"}
    assert fb["properties"] == ["concentration"]
    assert [a["type"] for a in fb["activities"].values()] == ["save"]
    assert [a["type"] for a in items["Fire Bolt"]["system"]["activities"].values()] == ["attack"]
    assert items["Light"]["system"]["activities"] == {}
    assert items["Light"]["system"]["target"]["template"] == {"units": "ft"}
    bolt = next(iter(items["Fire Bolt"]["system"]["activities"].values()))
    grasp = next(iter(items["Shocking Grasp"]["system"]["activities"].values()))
    assert bolt["attack"]["type"]["value"] == "ranged"
    assert grasp["attack"]["type"]["value"] == "melee"


def test_midi_save_spell_without_damage_has_no_damage_parts():
    c = _ctx({"spells": [{"name": "Hold", "save": "wis"}]})
    midi_qol.on_npc(c)
    act = next(iter(c.items[0]["system"]["activities"].values()))
    assert act["type"] == "save"
    assert not act.get("damage", {}).get("parts")


def test_midi_encounter_journal_defaults():
    assert midi_qol.on_encounter_journal({}, {}) == {
        "use_midi_rolls": True, "auto_apply_damage": True, "concentration_penalty": True}
    assert midi_qol.on_encounter_journal(
        {"midi_qol": {"auto_damage": False}}, {})["auto_apply_damage"] is False
