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
    # 7-14 targets: scenes 5-8, npcs 5-8, locations 4-6, quests 3-5, encounters 4-6
    assert sf["scenes"] == {"got": 1, "target_min": 5, "target_range": "5-8"}
    assert sf["npcs"]["target_min"] == 5 and sf["npcs"]["got"] == 0
    assert sf["quest_logs"]["target_min"] == 3
    assert "encounters" in sf and "locations" in sf


def test_shortfall_empty_when_counts_met():
    data = {
        "scenes": [{} for _ in range(6)],
        "npcs": [{} for _ in range(6)],
        "locations": [{} for _ in range(5)],
        "quest_logs": [{} for _ in range(4)],
        "encounters": [{} for _ in range(5)],
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
    for token in ("scenes=5-8", "npcs=5-8", "locations=4-6", "quest_logs=3-5", "encounters=4-6"):
        assert token in cl, f"checklist missing {token}"


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

