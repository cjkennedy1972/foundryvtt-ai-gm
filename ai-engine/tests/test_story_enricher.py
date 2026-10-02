"""Story enricher: scene/encounter/NPC enhancement generation and world dynamics."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.analyzer import NarrativeElement
from campaign.story_enricher import StoryEnricher


def _scene(name="Ambush", description="A quiet road", drama_level=5):
    return {"name": name, "description": description, "drama_level": drama_level}


# ─── scene hooks ───────────────────────────────────────────────────────────


def test_scene_hook_is_generated_only_when_synergy_and_llm_both_present():
    enricher = StoryEnricher()
    scenes = [_scene()]
    synergies = [{"scene": "Ambush", "synergies": [{"module": "dynamic-lighting"}]}]
    llm = AsyncMock()
    llm.generate_text = AsyncMock(return_value="Shadows flicker across the road.")

    hooks = asyncio.run(enricher._generate_scene_hooks(scenes, synergies, llm))

    assert hooks == [{
        "scene": "Ambush",
        "hooks": [{"hook": "Shadows flicker across the road.", "module_used": "multiple"}],
    }]


def test_scene_without_a_matching_synergy_produces_no_hook():
    enricher = StoryEnricher()
    hooks = asyncio.run(enricher._generate_scene_hooks(
        [_scene(name="Quiet Inn")], [{"scene": "Ambush", "synergies": []}],
        AsyncMock(generate_text=AsyncMock(return_value="x"))))
    assert hooks == []


def test_scene_hook_needs_an_llm_manager_even_with_a_synergy_match():
    enricher = StoryEnricher()
    synergies = [{"scene": "Ambush", "synergies": [{"module": "dynamic-lighting"}]}]
    hooks = asyncio.run(enricher._generate_scene_hooks([_scene()], synergies, None))
    assert hooks == []


def test_a_failing_llm_call_is_swallowed_and_logged_without_a_hook(caplog):
    enricher = StoryEnricher()
    synergies = [{"scene": "Ambush", "synergies": [{"module": "dynamic-lighting"}]}]
    llm = AsyncMock()
    llm.generate_text = AsyncMock(side_effect=RuntimeError("LLM is down"))

    with caplog.at_level("WARNING"):
        hooks = asyncio.run(enricher._generate_scene_hooks([_scene()], synergies, llm))

    assert hooks == []
    assert "Ambush" in caplog.text and "LLM is down" in caplog.text


def test_scene_hooks_accept_narrative_element_objects_not_just_dicts():
    enricher = StoryEnricher()
    scene = NarrativeElement(type="scene", name="Ambush", description="A quiet road",
                              immersion_opportunities=[], required_player_engagement="combat",
                              drama_level=8)
    synergies = [{"scene": "Ambush", "synergies": [{"module": "dynamic-lighting"}]}]
    llm = AsyncMock(generate_text=AsyncMock(return_value="reply"))

    hooks = asyncio.run(enricher._generate_scene_hooks([scene], synergies, llm))

    assert hooks[0]["scene"] == "Ambush"
    llm.generate_text.assert_called_once()
    prompt = llm.generate_text.call_args[0][0]
    assert "Ambush" in prompt and "A quiet road" in prompt and "8/10" in prompt


# ─── encounter moments ─────────────────────────────────────────────────────


def _encounter(name="Goblin Raid", drama_level=6):
    return {"name": name, "drama_level": drama_level}


def test_encounter_moment_includes_drama_level_and_module_enhancements():
    enricher = StoryEnricher()
    synergies = [{"encounter": "Goblin Raid", "synergies": [{"module": "midi-qol"}]}]

    moments = asyncio.run(enricher._generate_encounter_moments([_encounter()], synergies, None))

    assert len(moments) == 1
    moment = moments[0]
    assert moment["encounter"] == "Goblin Raid"
    assert moment["drama_level"] == 6
    assert moment["module_enhancements"] == [{"module": "midi-qol"}]
    assert len(moment["dramatic_beats"]) == 4


def test_encounter_without_a_matching_synergy_produces_no_moment():
    enricher = StoryEnricher()
    moments = asyncio.run(enricher._generate_encounter_moments(
        [_encounter(name="Random Fight")], [{"encounter": "Goblin Raid", "synergies": []}], None))
    assert moments == []


def test_encounter_moments_accept_narrative_element_objects():
    enricher = StoryEnricher()
    encounter = NarrativeElement(type="encounter", name="Goblin Raid", description="",
                                  immersion_opportunities=[], required_player_engagement="combat",
                                  drama_level=9)
    synergies = [{"encounter": "Goblin Raid", "synergies": [{"module": "midi-qol"}]}]

    moments = asyncio.run(enricher._generate_encounter_moments([encounter], synergies, None))

    assert moments[0]["drama_level"] == 9


# ─── npc interactions ──────────────────────────────────────────────────────


def test_npc_interaction_lists_module_features_from_synergies():
    enricher = StoryEnricher()
    npc = {"name": "Kansaldi", "description": "A cleric"}
    synergies = [{"npc": "Kansaldi", "synergies": [{"enhancement": "voice-acting"}, {"enhancement": "portrait"}]}]

    interactions = asyncio.run(enricher._generate_npc_interactions([npc], synergies, None))

    assert len(interactions) == 1
    interaction = interactions[0]
    assert interaction["npc"] == "Kansaldi"
    assert interaction["description"] == "A cleric"
    assert interaction["module_features"] == ["voice-acting", "portrait"]
    assert len(interaction["interaction_types"]) == 4


def test_npc_without_a_matching_synergy_produces_no_interaction():
    enricher = StoryEnricher()
    interactions = asyncio.run(enricher._generate_npc_interactions(
        [{"name": "Lord Soth"}], [{"npc": "Kansaldi", "synergies": []}], None))
    assert interactions == []


def test_npc_interactions_accept_narrative_element_objects():
    enricher = StoryEnricher()
    npc = NarrativeElement(type="npc", name="Kansaldi", description="A cleric",
                            immersion_opportunities=[], required_player_engagement="dialogue",
                            drama_level=3)
    synergies = [{"npc": "Kansaldi", "synergies": [{"enhancement": "voice-acting"}]}]

    interactions = asyncio.run(enricher._generate_npc_interactions([npc], synergies, None))

    assert interactions[0]["description"] == "A cleric"


# ─── dramatic moments ──────────────────────────────────────────────────────


def test_dramatic_moments_cover_decision_points_and_high_intensity_pacing():
    enricher = StoryEnricher()
    analysis = {
        "decision_points": [{"scene": "Crossroads", "options": ["left", "right", "back"]}],
        "pacing": {"peak_intensity": 9},
    }

    moments = asyncio.run(enricher._generate_dramatic_moments(analysis, None))

    assert moments[0] == {
        "type": "branching_choice", "scene": "Crossroads", "choice_count": 3,
        "narrative_weight": "high",
        "module_support": "Use notification system to show consequences preview",
    }
    assert moments[1]["type"] == "climactic_confrontation" and moments[1]["intensity_level"] == 9


def test_dramatic_moments_skip_the_climax_below_the_intensity_threshold():
    enricher = StoryEnricher()
    moments = asyncio.run(enricher._generate_dramatic_moments(
        {"decision_points": [], "pacing": {"peak_intensity": 7}}, None))
    assert moments == []


def test_dramatic_moments_handle_a_bare_campaign_analysis():
    enricher = StoryEnricher()
    assert asyncio.run(enricher._generate_dramatic_moments({}, None)) == []


# ─── world dynamics ────────────────────────────────────────────────────────


def test_world_dynamics_flags_are_enabled_only_when_their_module_is_present():
    enricher = StoryEnricher()
    synergies = {"modules": ["simple-calendar"]}

    dynamics = asyncio.run(enricher._generate_world_dynamics({"decision_points": [1, 2]}, synergies, None))

    assert dynamics["time_progression"]["enabled"] is True
    assert dynamics["consequence_tracking"]["enabled"] is False
    assert dynamics["atmospheric_evolution"]["enabled"] is False
    assert dynamics["player_agency"]["enabled"] is True
    assert "2 key decision points" in dynamics["player_agency"]["opportunities"][0]


def test_world_dynamics_with_no_relevant_modules_disables_each_tracked_flag():
    enricher = StoryEnricher()
    dynamics = asyncio.run(enricher._generate_world_dynamics({}, {"modules": []}, None))

    assert dynamics["time_progression"]["enabled"] is False
    assert dynamics["consequence_tracking"]["enabled"] is False
    assert dynamics["atmospheric_evolution"]["enabled"] is False
    assert "0 key decision points" in dynamics["player_agency"]["opportunities"][0]


# ─── full generate_enhancements wiring ─────────────────────────────────────


def test_generate_enhancements_wires_all_sections_from_campaign_analysis():
    enricher = StoryEnricher()
    campaign_analysis = {
        "scenes": [_scene()],
        "encounters": [_encounter()],
        "npcs": [{"name": "Kansaldi", "description": "A cleric"}],
        "decision_points": [{"scene": "Crossroads", "options": ["a", "b"]}],
        "pacing": {"peak_intensity": 2},
    }
    module_synergies = {
        "scene_enhancements": [{"scene": "Ambush", "synergies": [{"module": "dynamic-lighting"}]}],
        "encounter_enhancements": [{"encounter": "Goblin Raid", "synergies": [{"module": "midi-qol"}]}],
        "npc_enhancements": [{"npc": "Kansaldi", "synergies": [{"enhancement": "voice-acting"}]}],
    }

    result = asyncio.run(enricher.generate_enhancements(campaign_analysis, module_synergies, llm_manager=None))

    assert set(result.keys()) == {
        "scene_hooks", "encounter_moments", "npc_interactions", "dramatic_moments", "world_dynamics",
    }
    assert result["scene_hooks"] == []                       # no llm_manager, so no hook text generated
    assert result["encounter_moments"][0]["encounter"] == "Goblin Raid"
    assert result["npc_interactions"][0]["npc"] == "Kansaldi"
    assert result["dramatic_moments"][0]["scene"] == "Crossroads"
    assert result["world_dynamics"]["player_agency"]["enabled"] is True


def test_generate_enhancements_tolerates_missing_optional_keys():
    enricher = StoryEnricher()
    result = asyncio.run(enricher.generate_enhancements({}, {}, llm_manager=None))

    assert result["scene_hooks"] == [] and result["encounter_moments"] == []
    assert result["npc_interactions"] == [] and result["dramatic_moments"] == []
    assert result["world_dynamics"]["time_progression"]["enabled"] is False
