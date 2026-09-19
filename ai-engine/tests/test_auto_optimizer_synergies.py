#!/usr/bin/env python3
"""The auto-optimizer reported zero module synergies however many it found.

CampaignOptimizer.optimize_campaign compiles its result with a `synergies`
block that holds COUNTS plus the lists under a `details` key:

    "synergies": {
        "scene_synergies": len(module_synergies.get("scene_enhancements", [])),
        "encounter_synergies": ...,
        "npc_synergies": ...,
        "immersion_gap_fills": ...,
        "details": module_synergies,
    },

AutoOptimizer read `result["synergies"]["scene_enhancements"]`, which is not a
key at that level — the lists live one deeper, under "details". So the .get
default fired every time:

    scene synergies    : []  (2 were found)
    encounter synergies: []  (1 was found)
    quest enhancements : {'hooks': ['a cold wind']}

Two of the three paths. The quest one works because "enhancements" really is
a top-level key.

Each is exposed as an API route (/optimize-scene, /optimize-encounter,
/optimize-quest), which returns the result verbatim, so the endpoints reported
an empty list and the log line said "optimized with 0 module synergies".

Run:
    cd ai-engine && python -m pytest tests/test_auto_optimizer_synergies.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.auto_optimizer import AutoOptimizer

SCENE_SYNERGIES = [
    {"scene": "The Sunken Chapel", "module": "fxmaster", "enhancement": "rain"},
    {"scene": "The Sunken Chapel", "module": "sequencer", "enhancement": "bell toll"},
]
ENCOUNTER_SYNERGIES = [{"encounter": "Ambush", "module": "midi-qol", "enhancement": "auto-damage"}]


def _optimizer(**overrides):
    """An AutoOptimizer over a CampaignOptimizer result of the real shape."""
    result = {
        "status": "complete",
        "campaign_name": "Oakhaven",
        "modules": {"total_installed": 3, "enabled": 2},
        "synergies": {
            "scene_synergies": len(SCENE_SYNERGIES),
            "encounter_synergies": len(ENCOUNTER_SYNERGIES),
            "npc_synergies": 0,
            "immersion_gap_fills": 0,
            "details": {
                "scene_enhancements": SCENE_SYNERGIES,
                "encounter_enhancements": ENCOUNTER_SYNERGIES,
                "npc_enhancements": [],
            },
        },
        "enhancements": {"hooks": ["a cold wind moves through the nave"]},
        "recommendations": [{"priority": "high", "category": "Immersion Gaps"}],
    }
    result.update(overrides)
    opt = AutoOptimizer(llm_manager=MagicMock(), foundry_client=MagicMock())
    opt.optimizer.optimize_campaign = AsyncMock(return_value=result)
    return opt


# ── the synergies reach the caller ────────────────────────────────────────

def test_scene_synergies_are_reported():
    out = asyncio.run(_optimizer().optimize_new_scene({"name": "The Sunken Chapel"}, {"name": "Oakhaven"}))

    assert out["synergies"] == SCENE_SYNERGIES


def test_encounter_synergies_are_reported():
    out = asyncio.run(_optimizer().optimize_new_encounter({"name": "Ambush"}, {"name": "Oakhaven"}))

    assert out["synergies"] == ENCOUNTER_SYNERGIES


def test_a_scene_with_no_synergies_reports_an_empty_list_not_an_error():
    opt = _optimizer(synergies={"scene_synergies": 0, "details": {"scene_enhancements": []}})

    out = asyncio.run(opt.optimize_new_scene({"name": "Empty Room"}, {"name": "Oakhaven"}))

    assert out["synergies"] == []
    assert "error" not in out


def test_a_result_with_no_details_block_does_not_raise():
    opt = _optimizer(synergies={"scene_synergies": 0})

    out = asyncio.run(opt.optimize_new_scene({"name": "Empty Room"}, {"name": "Oakhaven"}))

    assert out["synergies"] == []


# ── what already worked ───────────────────────────────────────────────────

def test_quest_enhancements_still_come_from_the_top_level_key():
    out = asyncio.run(_optimizer().optimize_new_quest({"title": "The Bone Key"}, {"name": "Oakhaven"}))

    assert out["narrative_enhancements"] == {"hooks": ["a cold wind moves through the nave"]}


def test_modules_and_recommendations_still_come_through():
    out = asyncio.run(_optimizer().optimize_new_scene({"name": "The Sunken Chapel"}, {"name": "Oakhaven"}))

    assert out["modules"] == {"total_installed": 3, "enabled": 2}
    assert out["recommendations"] == [{"priority": "high", "category": "Immersion Gaps"}]


def test_a_failed_optimization_is_passed_back_as_an_error():
    opt = _optimizer()
    opt.optimizer.optimize_campaign = AsyncMock(
        return_value={"status": "error", "error": "module discovery timed out"}
    )

    out = asyncio.run(opt.optimize_new_scene({"name": "The Sunken Chapel"}, {"name": "Oakhaven"}))

    assert out == {"error": "module discovery timed out"}


def test_a_batch_optimizes_each_element():
    opt = _optimizer()

    out = asyncio.run(opt.optimize_element_batch(
        [{"name": "A"}, {"name": "B"}], "scene", {"name": "Oakhaven"}
    ))

    assert len(out) == 2
    assert all(o["synergies"] == SCENE_SYNERGIES for o in out)
