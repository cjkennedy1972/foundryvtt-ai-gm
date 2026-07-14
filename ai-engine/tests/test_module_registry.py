"""Checks for the ModuleIntegration registry (Phase 4) — pins exact output
against every addon active at once, since deploy_to_foundry's 47 inline
"x in mods" checks were rewritten into per-module hook files.

Each assertion here mirrors what the ORIGINAL inline code (before this
refactor) would have produced for the same input, so a regression in any
one module's extracted logic fails a specific, readable assertion rather
than a vague end-to-end diff.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import campaign.modules  # noqa: F401 — populates the registry
from campaign.modules.registry import MODULE_REGISTRY, NpcContext, run_flag_hook, run_npc_hooks
from campaign.orchestrator import CampaignOrchestrator

# times-up is a modifier consumed by DAE, not a module the fixture treats as
# active by default; duration behavior is tested explicitly below.
ALL_MODS = {
    m: {"title": m, "version": "1.0"}
    for m in MODULE_REGISTRY
    if m != "times-up"
}


class StubFoundry:
    is_connected = True

    def __init__(self):
        self.created = []

    async def _send(self, action, **kw):
        if action == "create":
            self.created.append((kw.get("entityType"), kw.get("data")))
        return {"uuid": f"{kw.get('entityType', 'Doc')}.stub{len(self.created)}"}


def test_registered_modules_match_expected_ids():
    assert set(MODULE_REGISTRY.keys()) == {
        "autoanimations", "mmm", "item-piles", "lootsheet-simple", "midi-qol",
        "token-notes", "polyglot", "patrol", "vision-5e", "dae",
        "dynamic-soundscapes", "levels", "betterroofs", "fog-weaver", "smalltime",
        "foundryvtt-simple-calendar-reborn", "progress-tracker", "rpgx-quest-log",
        "bossbar", "dfreds-convenient-effects", "dice-so-nice", "times-up",
        "sequencer", "fxmaster", "monks-tokenbar",
    }


def test_bossbar_marks_only_boss_npcs():
    boss = NpcContext(npc={"name": "Lich", "boss": True}, mods=ALL_MODS, flags={}, system={})
    run_npc_hooks(boss)
    assert boss.flags["aigm"]["boss"] is True

    mook = NpcContext(npc={"name": "Goblin"}, mods=ALL_MODS, flags={}, system={})
    run_npc_hooks(mook)
    assert "aigm" not in mook.flags


def test_autoanimations_registered_before_midi_qol():
    # midi-qol's on_npc reaches into weapon items autoanimations already
    # built — order is load-bearing, not incidental.
    order = list(MODULE_REGISTRY.keys())
    assert order.index("autoanimations") < order.index("midi-qol")


# ── NPC hooks — full fixture with every module's trigger condition met ──────

def _full_npc():
    return {
        "name": "Grimjaw", "faction": "Iron Wolves", "stat_block": "Bandit Captain",
        "npc_type": "merchant", "animation_type": "melee",
        "conditions": ["poisoned"], "concentration_caster": True,
        "critical_threshold": 19, "use_macros": True,
        "auto_damage_type": "auto", "disadvantage_attacks": True,
        "gm_token_note": "Secretly a spy", "language_spoken": "Draconic",
        "weapon_items": ["Rusty Cutlass"], "attack_bonus": 5,
        "spells": [{"name": "Firebolt", "level": 0, "damage": "1d10", "damage_type": "fire",
                     "save": "dex", "aoe": {"type": "sphere", "size": 5}}],
        "senses": {"darkvision": 60, "blindsight": 10},
        "active_effects": [{"name": "Blessed", "duration": {"rounds": 3}}],
        "portrait_src": "path/to/portrait.png",
    }


def test_npc_hooks_produce_expected_flags():
    ctx = NpcContext(npc=_full_npc(), mods=ALL_MODS, flags={"ai-gm": {}}, system={"attributes": {}})
    run_npc_hooks(ctx)

    assert ctx.flags["autoanimations"] == {"killAnim": False, "animationType": "melee"}
    assert ctx.flags["mmm"] == {"track_conditions": True, "active_conditions": ["poisoned"]}
    # item-piles wins the merchant flag; lootsheet-simple explicitly defers to it
    assert ctx.flags["item-piles"]["data"]["isMerchant"] is True
    assert "lootsheet-simple" not in ctx.flags
    assert ctx.flags["midi-qol"] == {
        "concentration-automation": True, "critThreshold": 19, "allowUseMacro": True,
        "autoApplyDamage": True, "disadvantageAttacks": True,
    }


def test_npc_hooks_lootsheet_simple_fallback_when_item_piles_inactive():
    mods_without_piles = {k: v for k, v in ALL_MODS.items() if k != "item-piles"}
    ctx = NpcContext(npc=_full_npc(), mods=mods_without_piles, flags={"ai-gm": {}}, system={"attributes": {}})
    run_npc_hooks(ctx)
    assert ctx.flags["lootsheet-simple"] == {"lootsheettype": "Merchant"}
    assert "item-piles" not in ctx.flags


def test_npc_hooks_prototype_token_flags():
    ctx = NpcContext(npc=_full_npc(), mods=ALL_MODS, flags={}, system={"attributes": {}})
    run_npc_hooks(ctx)
    assert ctx.prototype_token["flags"]["token-notes"] == {"note": "Secretly a spy"}
    assert ctx.prototype_token["flags"]["polyglot"] == {"language": "Draconic"}


def test_npc_hooks_patrol_only_for_guards():
    npc = _full_npc()
    npc["npc_type"] = "guard"
    npc["patrol_route"] = [[1, 1], [2, 2]]
    ctx = NpcContext(npc=npc, mods=ALL_MODS, flags={}, system={"attributes": {}})
    run_npc_hooks(ctx)
    assert ctx.prototype_token["flags"]["patrol"] == {
        "active": True, "speed": 1, "pause": 3000, "route": [[1, 1], [2, 2]],
    }
    # merchant-only flags must not appear on a guard
    assert "item-piles" not in ctx.flags


def _only_activity(item):
    """The single activity dnd5e 5.x weapons/spells need to be usable at all."""
    activities = item["system"]["activities"]
    assert len(activities) == 1
    return next(iter(activities.values()))


def test_npc_hooks_weapon_item_gets_midi_qol_attack_bonus():
    # dnd5e 5.x resolves attacks through system.activities, not the legacy
    # system.damage/system.attackBonus fields (verified live: an item with
    # neither has zero working attack rolls). midi-qol's bonus injection
    # must land on the activity, not a dead legacy field.
    ctx = NpcContext(npc=_full_npc(), mods=ALL_MODS, flags={}, system={"attributes": {}})
    run_npc_hooks(ctx)
    weapons = [i for i in ctx.items if i["type"] == "weapon"]
    assert len(weapons) == 1
    assert weapons[0]["name"] == "Rusty Cutlass"
    activity = _only_activity(weapons[0])
    assert activity["type"] == "attack"
    assert activity["attack"]["bonus"] == "5"
    assert activity["damage"]["parts"][0]["bonus"] == "5"


def test_npc_hooks_no_attack_bonus_without_midi_qol():
    mods_without_midi = {k: v for k, v in ALL_MODS.items() if k != "midi-qol"}
    ctx = NpcContext(npc=_full_npc(), mods=mods_without_midi, flags={}, system={"attributes": {}})
    run_npc_hooks(ctx)
    weapons = [i for i in ctx.items if i["type"] == "weapon"]
    activity = _only_activity(weapons[0])
    assert activity["attack"]["bonus"] == ""  # autoanimations' default, never enriched
    # no spell items at all without midi-qol active (only midi-qol builds them)
    assert all(i["type"] != "spell" for i in ctx.items)


def test_npc_hooks_midi_qol_spell_item():
    ctx = NpcContext(npc=_full_npc(), mods=ALL_MODS, flags={}, system={"attributes": {}})
    run_npc_hooks(ctx)
    spells = [i for i in ctx.items if i["type"] == "spell"]
    assert len(spells) == 1
    assert spells[0]["name"] == "Firebolt"
    # fixture has both damage AND save -> save activity wins (matches how a
    # save spell with on-fail damage actually works in dnd5e; an unconditional
    # attack-roll activity would be wrong for a spell that also has a save)
    activity = _only_activity(spells[0])
    assert activity["type"] == "save"
    assert activity["save"]["ability"] == ["dex"]
    assert activity["save"]["dc"]["formula"] == "13"
    assert activity["damage"]["parts"][0] == {"number": 1, "denomination": 10, "bonus": "", "types": ["fire"]}
    assert spells[0]["system"]["target"]["template"]["type"] == "sphere"


def test_npc_hooks_vision_5e_writes_into_existing_attributes():
    system = {"attributes": {"hp": {"value": 10}}}  # pre-populated, as deploy_to_foundry builds it
    ctx = NpcContext(npc=_full_npc(), mods=ALL_MODS, flags={}, system=system)
    run_npc_hooks(ctx)
    assert ctx.system["attributes"]["senses"] == {
        "darkvision": 60, "blindsight": 10, "tremorsense": 0, "truesight": 0, "units": "ft",
    }
    assert ctx.system["attributes"]["hp"] == {"value": 10}  # untouched


def test_npc_hooks_dae_effects_with_times_up_duration():
    # times-up isn't a registered module (see dae.py's docstring) — it's only
    # ever checked as a raw key in ctx.mods, so it must be added explicitly.
    mods_with_times_up = {**ALL_MODS, "times-up": {"title": "times-up", "version": "1.0"}}
    ctx = NpcContext(npc=_full_npc(), mods=mods_with_times_up, flags={}, system={"attributes": {}})
    run_npc_hooks(ctx)
    assert len(ctx.effects) == 1
    assert ctx.effects[0]["name"] == "Blessed"
    assert ctx.effects[0]["duration"] == {"rounds": 3}


def test_npc_hooks_dae_effects_without_times_up_no_duration():
    # ALL_MODS never contains times-up (it's not in MODULE_REGISTRY at all).
    ctx = NpcContext(npc=_full_npc(), mods=ALL_MODS, flags={}, system={"attributes": {}})
    run_npc_hooks(ctx)
    assert "duration" not in ctx.effects[0]


def test_npc_hooks_empty_mods_produce_no_addon_flags():
    ctx = NpcContext(npc=_full_npc(), mods={}, flags={"ai-gm": {"x": 1}}, system={"attributes": {}})
    run_npc_hooks(ctx)
    assert ctx.flags == {"ai-gm": {"x": 1}}
    assert ctx.items == []
    assert ctx.prototype_token == {}
    assert ctx.effects == []


# ── Flag hooks (journal/quest/scene/calendar/playlist/encounter) ────────────

def test_journal_polyglot_flag_hook():
    flags = run_flag_hook("on_journal", {"language": "Elvish"}, ALL_MODS)
    assert flags == {"polyglot": {"language": "Elvish"}}
    assert run_flag_hook("on_journal", {}, ALL_MODS) == {}  # no language -> no flag


def test_quest_flag_hooks_progress_tracker_and_rpgx():
    quest = {"status": "in-progress", "objectives": [1, 2], "quest_giver": "Mira",
              "difficulty": "hard", "xp_reward": 500}
    flags = run_flag_hook("on_quest", quest, ALL_MODS)
    assert flags["progress-tracker"] == {
        "enabled": True, "status": "in-progress", "objectives": 2, "completed": 0,
    }
    assert flags["rpgx-quest-log"]["questGiver"] == "Mira"
    assert flags["rpgx-quest-log"]["xpReward"] == 500


def test_scene_flag_hooks_prefer_module_flags_over_fallback():
    scene = {
        "module_flags": {"dynamic-soundscapes": {"ambient": True, "preset": "custom"}},
        "soundscape": "forest",  # would be the fallback if module_flags were absent
        "has_multiple_floors": True, "floors": ["Attic", "Cellar"],
        "has_roof": True, "fog_type": "heavy", "time_of_day": 8,
    }
    flags = run_flag_hook("on_scene", scene, ALL_MODS)
    assert flags["dynamic-soundscapes"] == {"ambient": True, "preset": "custom"}  # module_flags wins
    assert flags["levels"] == {"sceneLevels": ["Attic", "Cellar"]}  # fallback used
    assert flags["betterroofs"] == {"roofEnabled": True}
    assert flags["fog-weaver"]["fogType"] == "heavy"
    assert flags["smalltime"]["timeOfDay"] == 8


def test_scene_flag_hooks_empty_when_no_relevant_scene_fields():
    flags = run_flag_hook("on_scene", {"name": "Empty Room"}, ALL_MODS)
    assert flags == {}


def test_calendar_event_flag_hook():
    event = {"year": 100, "month": 3, "day": 15, "type": "festival", "visible_to_players": False}
    flags = run_flag_hook("on_calendar_event", event, ALL_MODS)
    assert flags["foundryvtt-simple-calendar-reborn"]["noteData"] == {
        "year": 100, "month": 2, "day": 14, "allDay": True,
        "playerVisible": False, "categories": ["festival"],
    }


def test_playlist_flag_hook():
    flags = run_flag_hook("on_playlist", {"scene": "Tavern"}, ALL_MODS)
    assert flags == {"dynamic-soundscapes": {"ambient": True}}


def test_encounter_journal_flag_hooks():
    enc = {"difficulty": "deadly", "xp_award": 1000, "midi_qol": {"auto_damage": False}}
    flags = run_flag_hook("on_encounter_journal", enc, ALL_MODS)
    assert flags["midi-qol"]["auto_apply_damage"] is False
    assert flags["autoanimations"] == {"enable_spell_animations": True, "enable_melee_animations": True}
    assert flags["dae"] == {"enable_active_effects": True, "track_conditions": True}


# ── End-to-end: deploy_to_foundry with every module active ──────────────────

def test_deploy_to_foundry_with_all_modules_creates_enriched_npc():
    orch = CampaignOrchestrator()
    client = StubFoundry()
    campaign_data = {"npcs": [_full_npc()]}
    scan_result = {"active_modules": ALL_MODS}

    asyncio.run(orch.deploy_to_foundry(campaign_data, client, {"maps": [], "portraits": []}, scan_result=scan_result))

    npc_creates = [d for t, d in client.created if t == "Actor" and d["name"] == "Grimjaw"]
    assert len(npc_creates) == 1
    data = npc_creates[0]
    assert data["flags"]["midi-qol"]["critThreshold"] == 19
    assert data["img"] == "path/to/portrait.png"
    assert data["prototypeToken"]["texture"]["src"] == "path/to/portrait.png"
    assert any(i["type"] == "weapon" for i in data["items"])
    assert data["effects"][0]["name"] == "Blessed"


def test_deploy_to_foundry_no_modules_matches_bare_ai_gm_flags_only():
    orch = CampaignOrchestrator()
    client = StubFoundry()
    campaign_data = {"npcs": [_full_npc()]}

    asyncio.run(orch.deploy_to_foundry(campaign_data, client, {"maps": [], "portraits": []}, scan_result=None))

    npc_creates = [d for t, d in client.created if t == "Actor" and d["name"] == "Grimjaw"]
    assert len(npc_creates) == 1
    data = npc_creates[0]
    assert list(data["flags"].keys()) == ["ai-gm"]
    assert "items" not in data
    assert "effects" not in data
    # portrait_src always sets prototypeToken.texture regardless of modules;
    # no module-contributed flags (token-notes/polyglot/patrol) should appear.
    assert data["prototypeToken"] == {"texture": {"src": "path/to/portrait.png"}}
