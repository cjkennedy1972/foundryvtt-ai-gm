"""Tests for campaign.analyzer — narrative/immersion analysis of campaign data.

Pure logic (async entrypoint over dicts); no external services. Covers the
public analyze_campaign aggregate plus the scoring/heuristic helpers."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.analyzer import CampaignAnalyzer, NarrativeElement


def _campaign():
    return {
        "scenes": [
            {"name": "Ambush", "description": "A deadly combat ambush with betrayal",
             "scene_setup": {"walls": [[0, 0, 1, 1]]}, "music": "battle"},
            {"name": "Parley", "description": "A tense dialogue to negotiate a choice",
             "scene_setup": {"lights": [{}], "sounds": [{}]}},
        ],
        "encounters": [{"name": "Skirmish", "description": "fight", "tokens_placed": 6}],
        "npcs": [{"name": "Villain", "description": "the big bad", "is_villain": True}],
        # Canonical key is quest_logs (what generation actually emits).
        "quest_logs": [{"title": "Main", "stages": [{"description": "find the crown"}],
                        "choices": ["burn it", "keep it"]}],
    }


def test_analyze_campaign_aggregates_all_sections():
    analysis = asyncio.run(CampaignAnalyzer().analyze_campaign(_campaign()))
    assert {s.name for s in analysis["scenes"]} == {"Ambush", "Parley"}
    assert analysis["encounters"][0].required_player_engagement == "combat"
    assert analysis["npcs"][0].type == "npc"
    assert analysis["narrative_arcs"][0]["title"] == "Main"
    assert analysis["narrative_arcs"][0]["key_moments"] == ["find the crown"]
    assert analysis["pacing"]["scene_count"] == 2
    # Parley scene mentions "choice" -> a decision point
    assert any(dp["scene"] == "Parley" for dp in analysis["decision_points"])


def test_analyze_empty_campaign_is_safe():
    analysis = asyncio.run(CampaignAnalyzer().analyze_campaign({}))
    assert analysis["scenes"] == []
    assert analysis["pacing"] == {"average_intensity": 0, "pacing_variance": 0}


def test_immersion_gaps_flag_missing_atmosphere():
    a = CampaignAnalyzer()
    gaps = asyncio.run(a._identify_immersion_gaps({
        "scenes": [{"name": "Bare", "scene_setup": {}}]}))
    joined = " ".join(gaps)
    assert "lighting" in joined and "ambient sounds" in joined and "music" in joined


def test_engagement_type_classification():
    a = CampaignAnalyzer()
    assert a._determine_engagement_type({"description": "a fierce battle"}) == "combat"
    assert a._determine_engagement_type({"description": "let us negotiate"}) == "dialogue"
    assert a._determine_engagement_type({"description": "explore the ruins"}) == "exploration"
    assert a._determine_engagement_type({"description": "solve the riddle"}) == "puzzle"
    assert a._determine_engagement_type({"description": "a quiet field"}) == "mixed"


def test_rate_drama_uses_highest_intensity_keyword():
    a = CampaignAnalyzer()
    assert a._rate_drama({"description": "a calm scene"}) == 5          # base
    assert a._rate_drama({"description": "a shocking betrayal"}) == 10  # peak
    assert a._rate_drama({"description": "some danger ahead"}) == 6


def test_rate_encounter_intensity_scales_with_tokens():
    a = CampaignAnalyzer()
    assert a._rate_encounter_intensity({}) == 5
    assert a._rate_encounter_intensity({"tokens_placed": 6}) == 8
    assert a._rate_encounter_intensity({"tokens_placed": 100}) == 10  # capped


def test_rate_npc_importance_by_role():
    a = CampaignAnalyzer()
    assert a._rate_npc_importance({}) == 5
    assert a._rate_npc_importance({"is_questgiver": True}) == 9
    assert a._rate_npc_importance({"is_companion": True}) == 8
    assert a._rate_npc_importance({"is_ally": True}) == 7


def test_scene_opportunities_react_to_setup_and_text():
    a = CampaignAnalyzer()
    opps = a._generate_scene_opportunities(
        {"description": "a fight breaks out", "scene_setup": {"walls": [[0, 0, 1, 1]]}})
    assert any("wall animations" in o for o in opps)
    assert any("combat effects" in o for o in opps)
    # No lights/sounds -> suggests adding them
    assert any("dynamic lighting" in o for o in opps)
    assert any("ambient soundscape" in o for o in opps)


def test_variance_is_standard_deviation():
    a = CampaignAnalyzer()
    assert a._calculate_variance([]) == 0
    assert a._calculate_variance([5, 5, 5]) == 0
    assert a._calculate_variance([2, 4]) == 1.0  # std dev of [2,4]


def test_narrative_arcs_read_canonical_quest_logs_key():
    a = CampaignAnalyzer()
    # quest_logs is the key generated campaigns use; the legacy "quests" alias
    # is only a fallback.
    from_logs = asyncio.run(a._identify_narrative_arcs(
        {"quest_logs": [{"title": "Canonical"}]}))
    assert [arc["title"] for arc in from_logs] == ["Canonical"]
    from_alias = asyncio.run(a._identify_narrative_arcs(
        {"quests": [{"title": "Legacy"}]}))
    assert [arc["title"] for arc in from_alias] == ["Legacy"]


def test_extract_key_moments_skips_non_dict_stages():
    a = CampaignAnalyzer()
    moments = a._extract_key_moments({"stages": ["bad", {"description": "good"}, {}]})
    assert moments == ["good"]


def test_narrative_element_dataclass_shape():
    el = NarrativeElement(type="scene", name="X", description="d",
                          immersion_opportunities=[], required_player_engagement="combat",
                          drama_level=5)
    assert el.type == "scene" and el.drama_level == 5
