"""campaign.generator: prompt scaling, JSON recovery, encounter reconciliation, validation, markdown builders."""

import json

import pytest

import campaign.generator as g


# ── level scaling / prompts ──────────────────────────────────────────────

@pytest.mark.parametrize("rng,scenes", [("1-5", "3-5"), ("1-10", "5-8"), ("3-17", "8-12"), ("1-20", "10-15"),
                                        ("5", "3-5"), ("5–10", "3-5"), ("garbage", "3-5"), ("", "3-5"), ("10-1", "3-5")])
def test_level_scaling_tiers_and_bad_input(rng, scenes):
    assert g._level_scaling(rng)["scenes"] == scenes


def test_campaign_prompt_embeds_modules_with_hints_and_fallback_titles():
    p = g.generate_campaign_prompt("A pirate tale", {"midi-qol": {"version": "11"}, "custom-mod": {"title": "My Mod", "version": "2"}}, "1-10")
    assert "A pirate tale" in p
    assert "- **midi-qol** (11): Midi QOL — REQUIRED" in p
    assert "- **custom-mod** (2): My Mod" in p
    assert p.index("custom-mod") < p.index("midi-qol")           # sorted
    assert "5-8" in p                                           # scaled scene count for a 1-10 range
    assert "Active FoundryVTT Modules" not in g.generate_campaign_prompt("x")


def test_arc_extension_prompt_modules_and_tier_range():
    data = {"campaign": {"name": "Oak", "description": "d", "theme": "t", "level_range": "1-20"},
            "scenes": [{"name": "Old Scene"}], "npcs": [{"name": "Old Npc"}], "quest_logs": [{"name": "Old Q"}], "story_arcs": [{"name": "Arc1"}]}
    p = g.generate_arc_extension_prompt(data, 5, 2, active_modules={"fxmaster": {"title": "FX", "version": "1"}})
    assert "Old Scene" in p and "Old Npc" in p and "- **fxmaster** (1): FX" in p
    assert "Active FoundryVTT Modules" not in g.generate_arc_extension_prompt(data, 5, 2)



# ── JSON parsing ─────────────────────────────────────────────────────────

def test_balanced_extraction_ignores_braces_in_strings_and_escapes():
    t = 'noise {"a": "}{", "b": {"c": "q\\"}"}} trailing {x}'
    assert g._extract_balanced_json(t) == '{"a": "}{", "b": {"c": "q\\"}"}}'
    assert g._extract_balanced_json("no braces") is None
    assert g._extract_balanced_json('{"unterminated": 1') is None


def test_recovery_requires_campaign_key_and_returns_enclosing_object():
    bad = 'prefix {"campaign": {"name": "X"}, "npcs": [{"name": "n"}]} garbage,,'
    assert g._try_recovery_json(bad) == {"campaign": {"name": "X"}, "npcs": [{"name": "n"}]}
    assert g._try_recovery_json('{"npcs": []}') is None
    assert g._try_recovery_json('"campaign" only') is None
    assert g._try_recovery_json('{"campaign": {"name": "X"') is None        # unbalanced
    assert g._try_recovery_json('{"campaign": nope}') is None               # balanced but not JSON


def test_parse_strips_think_blocks_and_fences():
    payload = {"campaign": {"name": "X"}, "scenes": []}
    raw = f"<think>hmm {{not json}}</think>\n```json\n{json.dumps(payload)}\n```\nthanks"
    assert g.parse_campaign_response(raw) == payload
    assert g.parse_campaign_response(f"```\n{json.dumps(payload)}\n```") == payload


def test_parse_repairs_small_model_slips():
    raw = '{"campaign": {"name": "X"}, "npcs": [{"name": "A", "hp: 136", "cr": 1/4,}]}'
    # the misquoted pair is an invalid-JSON slip the repair step is for
    raw = '{"campaign": {"name": "X"}, "npcs": [{"name": "A", "hp: 136", "cr": 1/4, "ac": 12,}],}'
    out = g.parse_campaign_response(raw)
    assert out["npcs"][0] == {"name": "A", "hp": 136, "cr": 0.25, "ac": 12}


def test_parse_hoists_nested_sections_and_recovers_from_trailing_garbage():
    out = g.parse_campaign_response('{"campaign": {"name": "X", "npcs": [{"name": "n"}]}}')
    assert out["npcs"] == [{"name": "n"}]
    out = g.parse_campaign_response('{"campaign": {"name": "X"}, "scenes": [{"name": "s"}]} and then I said {oops')
    assert out["scenes"] == [{"name": "s"}]
    with pytest.raises(json.JSONDecodeError):
        g.parse_campaign_response("definitely not json")


def test_parse_tolerates_raw_control_characters():
    out = g.parse_campaign_response('{"campaign": {"name": "X", "description": "line1\nline2"}}')
    assert out["campaign"]["description"] == "line1\nline2"


# ── scene_setup defaults ─────────────────────────────────────────────────

@pytest.mark.parametrize("t,size", [("tavern", (16, 12)), ("Castle", (24, 18)), ("tower", (16, 20)), ("mystery", (20, 15))])
def test_default_scene_setup_sizes(t, size):
    s = g._generate_default_scene_setup(t)
    assert (s["grid_width"], s["grid_height"]) == size and s["grid_size_px"] == 64
    assert s["walls"] == [] and s["global_illumination"] is True


def test_validate_survives_scene_with_null_type():
    data = {"campaign": {"name": "n", "description": "d"}, "scenes": [{"name": "S", "type": None}]}
    g.validate_campaign(data)
    assert data["scenes"][0]["scene_setup"]["grid_width"] == 20            # defaulted like a missing type


# ── validate_campaign ────────────────────────────────────────────────────

def _full_campaign(**over):
    base = {"campaign": {"name": "n", "description": "d"}, "scenes": [{"name": f"s{i}", "scene_setup": {"grid_width": 16, "grid_height": 12}} for i in range(5)],
            "npcs": [{}] * 5, "locations": [{"name": f"l{i}", "act": 1} for i in range(12)], "quest_logs": [{}] * 3,
            "loot_tables": [{}] * 2, "factions": [{}] * 2, "artifacts": [{}]}
    base.update(over)
    return base


def test_validate_clean_campaign_has_no_warnings():
    assert g.validate_campaign(_full_campaign()) == []


def test_validate_reports_counts_and_missing_fields():
    w = g.validate_campaign({"campaign": {}})
    assert "Campaign missing 'name' field" in w and "Campaign missing 'description' field" in w
    assert any(x.startswith("Only 0 NPCs") for x in w) and any("recommended: 3-5" in x for x in w if "scenes" in x)
    for kind in ("quests", "loot tables", "factions", "artifacts", "locations"):
        assert any(f"Only 0 {kind}" in x for x in w), kind


def test_validate_scene_setup_grid_checks():
    data = _full_campaign(scenes=[
        {"name": "odd", "scene_setup": {"grid_width": 17, "grid_height": 12, "grid_size_px": 50}},
        {"name": "missing"}, {"name": "ok", "scene_setup": {"grid_width": 24, "grid_height": 18}}] + [{"name": f"x{i}", "scene_setup": {"grid_width": 16, "grid_height": 12}} for i in range(3)])
    w = g.validate_campaign(data)
    assert "Scene 'odd': corrected grid_size_px to 64" in w and data["scenes"][0]["scene_setup"]["grid_size_px"] == 64
    assert any("Scene 'odd': grid 17×12 is non-standard" in x for x in w)
    assert data["scenes"][1]["scene_setup"]["grid_size_px"] == 64              # auto-generated
    assert not any("Scene 'ok'" in x or "Scene 'missing'" in x for x in w)


def test_validate_prologue_rules():
    def pro(**kw):
        return g.validate_campaign(_full_campaign(prologue=kw))
    assert "Prologue must be an object when present" in g.validate_campaign(_full_campaign(prologue="text"))
    w = pro()
    assert "Prologue missing required 'vessel' field" in w and any("at least 4 panels (got 0)" in x for x in w)
    assert any("got 0" in x for x in g.validate_campaign(_full_campaign(prologue={"vessel": "tome", "panels": "x"})))
    good = {"title": "t", "body": "b", "image_prompt": "i", "era": "e"}
    assert pro(vessel="tome", panels=[good] * 4) == []
    w = pro(vessel="tome", panels=["str", {"title": "t"}, good, good])
    assert "Prologue panel 1 must be an object" in w
    for missing in ("body", "image_prompt", "era"):
        assert f"Prologue panel 2 missing '{missing}'" in w
    assert not any("panel 2 missing 'title'" in x for x in w)


def test_world_location_gaps():
    data = {"locations": [
        {"name": "Orphan"}, {"name": "Cited"}, {"name": "HasRumor", "rumors": ["x"]}, {"name": "Visited", "act": 1},
        {"name": "HasScene", "scenes": ["s"]}, {"name": ""}, "junk"],
        "quest_logs": [{"location": "the cited place CITED"}], "factions": [{"goals": ["raze nothing"]}], "artifacts": [{"current_locations": None}]}
    assert g.world_location_coverage_gaps(data) == ["Orphan"]
    data["factions"] = [{"goals": ["destroy orphan keep"]}]
    assert g.world_location_coverage_gaps(data) == []


def test_count_shortfall_and_checklist():
    short = g.campaign_count_shortfall({"npcs": [{}], "quests": [{}] * 5, "scenes": [{}] * 5}, "1-5")
    assert short["npcs"] == {"got": 1, "target_min": 3, "target_range": "3-5"}
    assert "quest_logs" not in short and "scenes" not in short            # "quests" alias is honoured
    assert "scenes=3-5" in g.campaign_count_checklist("1-5")


# ── reconcile_encounter_scenes ───────────────────────────────────────────

def _recon(encs, scenes=None):
    data = {"scenes": scenes if scenes is not None else [{"name": "The Brass Vault", "act": 1}, {"name": "Sunken Chapel", "act": 2}, {"name": "Market"}],
            "encounters": encs}
    return data, g.reconcile_encounter_scenes(data)


def test_reconcile_each_resolution_tier():
    data, notes = _recon([
        {"name": "exact", "linked_scene": "Market"},
        {"name": "norm", "linked_scene": "brass vault!"},
        {"name": "fuzzy", "linked_scene": "Sunken Chapell"},
        {"name": "act", "linked_scene": "Zzzz", "act": 2},
        {"name": "first", "linked_scene": "Qqqq", "act": 9},
        {"name": "none", "act": 1},
        "not a dict",
    ])
    got = [e["linked_scene"] for e in data["encounters"][:6]]
    assert got == ["Market", "The Brass Vault", "Sunken Chapel", "Sunken Chapel", "The Brass Vault", "The Brass Vault"]
    assert len(notes) == 5 and not any("exact" in n for n in notes)
    assert "(normalized)" in notes[0] and "(fuzzy 0." in notes[1] and "act 2 fallback" in notes[2]
    assert "first-scene fallback" in notes[3] and "'none': linked_scene '(none)'" in notes[4]


def test_reconcile_noop_without_scenes():
    data, notes = _recon([{"name": "e", "linked_scene": "Anything"}], scenes=[])
    assert notes == [] and data["encounters"][0]["linked_scene"] == "Anything"


def test_norm_scene():
    assert g._norm_scene("The  Brass Vault!") == "brass vault" and g._norm_scene(None) == ""


# ── markdown ─────────────────────────────────────────────────────────────

def _camp():
    return {
        "campaign": {"name": "Oak", "description": "Desc", "theme": "Dark Fantasy", "level_range": "1-5"},
        "factions": [{"name": "Guild", "alignment": "LN", "description": "d", "goals": ["gold", "power"]}],
        "npcs": [{"name": "Mira", "role": "smith", "alignment": "NG", "description": "x" * 150}, {"name": "Bo"}],
        "locations": [{"name": "Keep", "type": "castle", "act": 1, "description": "y" * 100, "map_file": "keep.png"}, {"name": "Hut"}],
        "quest_logs": [{"title": "Find It", "act": 2, "type": "main", "description": "z"}],
        "story_arcs": [{"act": 1, "title": "Rise", "description": "ad", "milestones": [{"name": "m1", "description": "md"}],
                        "climax": "boom", "transition_to_act2": "then"}, {}],
        "artifacts": [{"name": "Orb", "type": "rare", "description": "od", "fragments": 3, "fragment_powers": ["a", "b"]}, {}],
    }


def test_campaign_markdown_sections_and_links():
    md = g.campaign_to_markdown(_camp())
    assert md.startswith("# Oak") and "tags: [campaign, dark-fantasy]" in md and "Levels: 1-5" in md
    assert "- **Guild** (LN) — d" in md and "  - Goals: gold, power" in md
    assert "[[Campaigns/Oak/NPCs/Mira|Mira]]** — smith (NG) — " + "x" * 100 + "..." in md
    assert "x" * 101 not in md
    assert "[[Campaigns/Oak/Maps/keep.png|map]]" in md and "[[Campaigns/Oak/Locations/Keep|Keep]]** — castle (Act 1)" in md
    assert "y" * 80 + "..." in md
    assert "- **[[Campaigns/Oak/Quests/Find It|Find It]]** — Act 2 [main]: z..." in md
    assert "### Act 1: Rise" in md and "- m1: md" in md and "**Climax:** boom" in md and "**→** then" in md
    assert "### Act ?: Unnamed Act" in md
    assert "### Orb [rare]" in md and "**Fragments:** 3" in md and "- Fragment 2: b" in md and "### Unnamed Artifact [common]" in md
    assert "tags: [campaign, fantasy]" in g.campaign_to_markdown({"campaign": {}})


def test_npc_markdown_lists_each_trait_on_its_own_line():
    md = g.build_npc_markdown("Oak", {"name": "Mira", "role": "smith", "personality": ["kind", "stern"],
                                      "motivations": ["gold", "revenge"], "relationships": ["a", "b"], "portrait_file": "m.png"})
    assert md.startswith("# Mira") and "![Mira](../Portraits/m.png)" in md
    for line in ("- kind", "- stern", "- gold", "- revenge", "- a", "- b"):
        assert f"\n{line}\n" in md, line
    default = g.build_npc_markdown("Oak", {"name": "Bo"})
    assert "\n- mysterious\n" in default and "![Bo]" not in default


def test_location_markdown_lists_and_map():
    md = g.build_location_markdown("Oak", {"name": "Keep", "type": "castle", "key_features": ["moat", "gate"], "connections": ["Hut", "Road"],
                                           "rumors": ["haunted", "gold"], "map_file": "k.png", "map_style": "ink"})
    for line in ("- moat", "- gate", "- Hut", "- Road", "- haunted", "- gold"):
        assert f"\n{line}\n" in md, line
    assert "Map style: ink" in md and "![Keep](../Maps/k.png)" in md
    assert "## Map" not in g.build_location_markdown("Oak", {"name": "Hut"}) and "Rumors" not in g.build_location_markdown("Oak", {"name": "Hut"})


def test_quest_markdown_title_objectives_rewards_consequences():
    q = {"title": "Find It", "type": "main", "act": 2, "status": "active", "description": "d",
         "objectives": [{"desc": "go", "check": "DC 10"}, {"desc": "return"}, "walk home"], "rewards": ["gold", "xp"],
         "consequences": {"success": "peace", "failure": "war"}}
    md = g.build_quest_markdown("Oak", q)
    assert md.startswith("# Find It\n")                                  # a heading, not a link to the campaign + loose text
    assert "1. go — Check: DC 10" in md and "2. return\n" in md and "3. walk home" in md
    assert "\n- gold\n" in md and "\n- xp\n" in md
    assert "- **Success:** peace" in md and "- **Failure:** war" in md
    assert "Status: active" in md
