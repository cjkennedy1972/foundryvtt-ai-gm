"""Regression test: the campaign loader must surface a scene's authored
atmosphere so per-turn narration matches the displayed map.

Root cause this guards against: the GM (a text model that can't see the map)
was given only a scene NAME per turn, so it narrated off the campaign title
and drifted — e.g. a "library of flesh" on an authored wilderness-cave map.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from context.loader import CampaignLoader


# Mirrors the real vault file "Story/Scene - The Whispering Caves Entrance.md".
_SCENE_FILE = """# The Whispering Caves Entrance

tags: [scene, act-1]

## Overview

A jagged cave mouth leading to the subterranean depths, echoing with faint whispers.

## Details

- **Type:** wilderness
- **Lighting:** cool moonlight, faint blue moss glow
- **Atmosphere:** eerie, cold, echoing whispers

## Map

Map style: aerial view cave entrance, glowing blue moss, moonlight through cracks
Map file: `maps/TBD`

## Description

A jagged cave mouth leading to the subterranean depths.
"""


def _loader_with_scene():
    loader = CampaignLoader.__new__(CampaignLoader)
    loader._data = {"Story/Scene - The Whispering Caves Entrance": _SCENE_FILE}
    return loader


def test_briefing_returns_authored_atmosphere():
    loader = _loader_with_scene()

    briefing = loader.get_scene_briefing("The Whispering Caves Entrance")

    # The atmosphere that anchors correct narration must be present...
    assert "cool moonlight" in briefing
    assert "wilderness" in briefing
    assert "echoing whispers" in briefing


def test_briefing_strips_the_map_image_prompt():
    """The '## Map' block is an image-gen prompt, not narration — it must not
    leak into what the GM reads aloud."""
    loader = _loader_with_scene()

    briefing = loader.get_scene_briefing("The Whispering Caves Entrance")

    assert "Map style" not in briefing
    assert "maps/TBD" not in briefing
    assert "tags:" not in briefing


def test_briefing_empty_for_unknown_scene():
    loader = _loader_with_scene()
    assert loader.get_scene_briefing("Some Improvised Room") == ""
