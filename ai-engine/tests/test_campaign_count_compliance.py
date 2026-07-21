"""Tests for campaign count-compliance: skeleton example, shortfall detection,
and the refill-prompt builder. These guard the fix for small-model undershooting
(anchoring on the worked example's cardinality)."""
import json
import re

import campaign.generator as g


def _example_array_lengths():
    m = re.search(r"```json\n(\{.*?\n\})\n```", g.CAMPAIGN_GENERATOR_PROMPT, re.DOTALL)
    assert m, "worked-example JSON block not found in CAMPAIGN_GENERATOR_PROMPT"
    # strip // markers we add for the model's benefit before JSON-parsing
    data = json.loads(re.sub(r"//.*", "", m.group(1)))
    return {k: len(v) for k, v in data.items() if isinstance(v, list)}


def test_worked_example_is_skeletonized_to_one_each():
    """The example must show exactly ONE item per array so the model has no
    concrete count (like '2 scenes') to anchor on."""
    lengths = _example_array_lengths()
    for key, n in lengths.items():
        assert n == 1, f"example array '{key}' has {n} items; must be 1 (shape template only)"


def test_shortfall_detects_undershoot():
    data = {
        "scenes": [{"name": "A"}],
        "npcs": [],
        "locations": [],
        "quest_logs": [{"title": "Q"}],
        "encounters": [],
    }
    sf = g.campaign_count_shortfall(data, level_range="7-14")
    # 7-14 targets: scenes 5-8, npcs 5-8, locations 12-16, quests 3-5, encounters 4-6
    assert sf["scenes"] == {"got": 1, "target_min": 5, "target_range": "5-8"}
    assert sf["npcs"]["target_min"] == 5 and sf["npcs"]["got"] == 0
    assert sf["quest_logs"]["target_min"] == 3
    assert "encounters" in sf and "locations" in sf


def test_shortfall_empty_when_counts_met():
    data = {
        "scenes": [{} for _ in range(6)],
        "npcs": [{} for _ in range(6)],
        "locations": [{} for _ in range(14)],
        "quest_logs": [{} for _ in range(4)],
        "encounters": [{} for _ in range(5)],
        "loot_tables": [{} for _ in range(3)],
        "factions": [{} for _ in range(2)],
        "artifacts": [{} for _ in range(2)],
    }
    assert g.campaign_count_shortfall(data, level_range="7-14") == {}


def test_shortfall_reads_quests_alias():
    """quest_logs count must be read from either 'quest_logs' or 'quests'."""
    data = {"quests": [{} for _ in range(4)], "scenes": [{} for _ in range(8)],
            "npcs": [{} for _ in range(8)], "locations": [{} for _ in range(6)],
            "encounters": [{} for _ in range(6)]}
    sf = g.campaign_count_shortfall(data, level_range="7-14")
    assert "quest_logs" not in sf  # 4 quests meets the 3-5 target via the alias


def test_refill_prompt_lists_deficit_and_existing_names():
    data = {"campaign": {"name": "Falling Firmament", "theme": "cosmic horror"},
            "scenes": [{"name": "The Crater"}]}
    sf = g.campaign_count_shortfall(data, level_range="7-14")
    prompt = g.generate_refill_prompt(data, sf, level_range="7-14")
    assert "Falling Firmament" in prompt
    assert "Generate 4 MORE" in prompt  # scenes: need 5, have 1
    assert "The Crater" in prompt  # existing name surfaced to avoid duplication
    # encounters short + a scene exists -> linked_scene hint present
    assert "linked_scene" in prompt and "The Crater" in prompt


def test_checklist_names_all_arrays_and_range():
    cl = g.campaign_count_checklist("7-14")
    for token in ("scenes=5-8", "npcs=5-8", "locations=12-16", "quest_logs=3-5", "encounters=4-6",
                  "loot_tables=2-3", "factions=1-2", "artifacts=1-2"):
        assert token in cl, f"checklist missing {token}"


def test_locations_populate_world_beyond_scenes():
    """A generated world should feel populated: every tier must ask for more
    locations than scenes so the map includes sites the plot never visits, and
    the checklist must tell the model world-only locations are allowed."""
    for lr in ("1-5", "1-10", "1-15", "1-20"):
        sc = g._level_scaling(lr)
        loc_min = int(sc["locations"].split("-")[0])
        scene_min = int(sc["scenes"].split("-")[0])
        assert loc_min > scene_min, f"{lr}: locations {loc_min} !> scenes {scene_min}"
    cl = g.campaign_count_checklist("1-10").lower()
    assert "act:null" in cl and "scenes:[]" in cl


def test_level_scaling_has_loot_faction_artifact_keys():
    for lr in ("1-5", "1-10", "1-15", "1-20"):
        sc = g._level_scaling(lr)
        for key in ("loot_tables", "factions", "artifacts"):
            assert key in sc, f"_level_scaling({lr}) missing {key}"
            # each is a valid "N-M" or "N-N" range
            lo, hi = (int(x) for x in sc[key].split("-"))
            assert 1 <= lo <= hi


def test_shortfall_enforces_loot_factions_artifacts():
    # medium campaign (7-14): loot_tables>=2, factions>=1, artifacts>=1
    data = {
        "scenes": [{} for _ in range(8)], "npcs": [{} for _ in range(8)],
        "locations": [{} for _ in range(6)], "quest_logs": [{} for _ in range(5)],
        "encounters": [{} for _ in range(6)],
        "loot_tables": [{"name": "T1"}],  # 1 < 2 -> short
        "factions": [{"name": "F1"}],     # 1 == min -> OK
        "artifacts": [],                  # 0 < 1 -> short
    }
    sf = g.campaign_count_shortfall(data, level_range="7-14")
    assert "loot_tables" in sf and sf["loot_tables"]["target_min"] == 2
    assert "artifacts" in sf and sf["artifacts"]["target_min"] == 1
    assert "factions" not in sf  # met its minimum of 1


def test_refill_prompt_covers_new_arrays_with_names():
    data = {"campaign": {"name": "X", "theme": "Y"},
            "loot_tables": [{"name": "Old Hoard"}], "factions": [], "artifacts": []}
    sf = g.campaign_count_shortfall(data, level_range="7-14")
    prompt = g.generate_refill_prompt(data, sf, level_range="7-14")
    assert "loot_tables" in prompt and "Old Hoard" in prompt
    assert "factions" in prompt and "artifacts" in prompt


def test_validate_campaign_agrees_with_shortfall():
    """validate_campaign must not warn about an array that campaign_count_shortfall
    considers satisfied — otherwise the user sees a false 'missing' message after
    the refill loop already backfilled it to target."""
    # A campaign that meets every scaled minimum for 7-14.
    full = {
        "campaign": {"name": "Aligned", "description": "desc"},
        "scenes": [{"name": f"s{i}", "scene_setup": {"grid_width": 20, "grid_height": 15, "grid_size_px": 64}} for i in range(6)],
        "npcs": [{} for _ in range(6)],
        "locations": [{} for _ in range(14)],
        "quest_logs": [{} for _ in range(4)],
        "encounters": [{} for _ in range(5)],
        "loot_tables": [{} for _ in range(2)],
        "factions": [{} for _ in range(1)],
        "artifacts": [{} for _ in range(1)],
    }
    assert g.campaign_count_shortfall(full, level_range="7-14") == {}
    warnings = g.validate_campaign(full, level_range="7-14")
    # No count-related warning should remain when shortfall is empty.
    count_words = ("loot tables", "factions", "artifacts", "NPCs", "locations", "scenes defined", "quests defined")
    offending = [w for w in warnings if any(cw in w for cw in count_words)]
    assert offending == [], f"validate_campaign warned despite counts met: {offending}"


def test_validate_campaign_loot_uses_scaled_minimum():
    """The old hardcoded 'loot_tables < 1' check is gone: a 7-14 campaign with 1
    loot table (below its scaled min of 2) must still be flagged, and a short
    campaign (1-5, min 1) with 1 table must NOT be flagged."""
    base = {"campaign": {"name": "N", "description": "d"}, "scenes": [], "npcs": [], "locations": []}
    med = dict(base, loot_tables=[{}])   # 1 < 2 for 7-14
    assert any("loot tables" in w for w in g.validate_campaign(med, level_range="7-14"))
    short = dict(base, loot_tables=[{}])  # 1 >= 1 for 1-5
    assert not any("loot tables" in w for w in g.validate_campaign(short, level_range="1-5"))


# ── World-location coverage (locations woven into content) ───────────────────

def _world_loc(name, **extra):
    return dict({"name": name, "act": None, "scenes": []}, **extra)


def test_coverage_gap_flags_orphan_world_location():
    data = {"locations": [_world_loc("Oakhaven")]}  # referenced nowhere, no rumors
    assert g.world_location_coverage_gaps(data) == ["Oakhaven"]


def test_coverage_gap_cleared_by_each_hook_type():
    # 1) own rumor
    assert g.world_location_coverage_gaps(
        {"locations": [_world_loc("Oakhaven", rumors=["smugglers use the docks"])]}) == []
    # 2) quest sited there
    assert g.world_location_coverage_gaps(
        {"locations": [_world_loc("Oakhaven")], "quest_logs": [{"location": "Oakhaven"}]}) == []
    # 3) faction goal names it
    assert g.world_location_coverage_gaps(
        {"locations": [_world_loc("Oakhaven")], "factions": [{"goals": ["Seize the Oakhaven smelter"]}]}) == []
    # 4) artifact fragment located there
    assert g.world_location_coverage_gaps(
        {"locations": [_world_loc("Oakhaven")], "artifacts": [{"current_locations": ["Oakhaven vault"]}]}) == []
    # 5) another location connects to it
    assert g.world_location_coverage_gaps(
        {"locations": [_world_loc("Oakhaven"), _world_loc("Riverbend", connections=["Road to Oakhaven"])]}) == ["Riverbend"]


def test_coverage_ignores_campaign_sites_and_stub_entries():
    data = {"locations": [
        {"name": "Riverbend", "act": 1, "scenes": ["Tavern"]},  # campaign site, not world-only
        {},                                                      # unnamed stub — ignored
    ]}
    assert g.world_location_coverage_gaps(data) == []


def test_validate_campaign_soft_warns_on_orphan_world_location():
    data = {"campaign": {"name": "N", "description": "d"},
            "scenes": [], "npcs": [], "locations": [_world_loc("Oakhaven")]}
    warns = g.validate_campaign(data, level_range="1-5")
    assert any("world-only site" in w and "Oakhaven" in w for w in warns)


def test_build_location_markdown_renders_rumors():
    md = g.build_location_markdown("Camp", _world_loc(
        "Oakhaven", type="town", description="A trade town.",
        rumors=["Caravans stopped arriving", "The smelter changed hands"]))
    assert "## Rumors & Hooks" in md
    assert "- Caravans stopped arriving" in md
    assert "- The smelter changed hands" in md
    # No rumors -> no section
    md2 = g.build_location_markdown("Camp", _world_loc("Nowhere", description="x"))
    assert "Rumors" not in md2


# ── JSON repair (small-model slip fixups) ────────────────────────────────────

def test_repair_misquoted_key_value():
    bad = '{"name": "Golem", "hp: 136", "ac": 15}'
    fixed = g._repair_common_json_slips(bad)
    assert '"hp": 136' in fixed
    assert json.loads(fixed)["hp"] == 136


def test_repair_fraction_cr_value():
    bad = '{"cr": 1/4, "count": 6}'
    fixed = g._repair_common_json_slips(bad)
    assert json.loads(fixed)["cr"] == 0.25


def test_repair_trailing_comma():
    bad = '{"a": 1, "b": [1, 2,], "c": 3,}'
    assert json.loads(g._repair_common_json_slips(bad)) == {"a": 1, "b": [1, 2], "c": 3}


def test_repair_leaves_valid_json_unchanged():
    good = '{"hp": 136, "note": "meet at dawn", "items": [1, 2, 3]}'
    assert g._repair_common_json_slips(good) == good


def test_repair_does_not_corrupt_string_with_colon_or_fraction():
    # A real string value containing a colon or a fraction must survive intact.
    s = '{"time": "dusk: the bell tolls", "recipe": "1/2 cup flour"}'
    fixed = g._repair_common_json_slips(s)
    assert json.loads(fixed)["time"] == "dusk: the bell tolls"
    assert json.loads(fixed)["recipe"] == "1/2 cup flour"


def test_parse_campaign_response_recovers_all_three_slips_together():
    raw = '''```json
{
  "campaign": {"name": "Test", "theme": "grit"},
  "npcs": [
    {"name": "Golem", "hp: 136", "cr": 1/4, "ac": 15,}
  ]
}
```'''
    data = g.parse_campaign_response(raw)
    npc = data["npcs"][0]
    assert npc["hp"] == 136 and npc["cr"] == 0.25 and npc["ac"] == 15

