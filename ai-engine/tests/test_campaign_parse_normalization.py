#!/usr/bin/env python3
"""
Regression test: smaller local models nest the section lists (npcs, scenes,
quest_logs, ...) INSIDE the "campaign" block instead of emitting them as
siblings, as the schema asks. The parser must hoist them to the top level —
otherwise validation sees "0 NPCs, 0 scenes", the vault folders stay empty,
and deploy creates nothing (the 2026-07-01 "The Shattered Oath" failure).

Run:
    cd ai-engine && python -m pytest tests/test_campaign_parse_normalization.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.generator import parse_campaign_response


def _nested_response() -> str:
    """The failure shape: everything inside "campaign"."""
    return json.dumps({
        "campaign": {
            "name": "The Shattered Oath",
            "description": "A test campaign.",
            "npcs": [{"name": "The Oathbreaker"}],
            "scenes": [{"name": "Ruined Chapel", "type": "temple"}],
            "locations": [{"name": "Chapel"}],
            "quest_logs": [{"title": "Mend the Oath"}],
            "encounters": [{"name": "Chapel Ambush"}],
        }
    })


def test_nested_sections_are_hoisted():
    data = parse_campaign_response(_nested_response())
    assert data["npcs"] == [{"name": "The Oathbreaker"}]
    assert data["scenes"][0]["name"] == "Ruined Chapel"
    assert data["locations"] and data["quest_logs"] and data["encounters"]
    # campaign metadata is still intact
    assert data["campaign"]["name"] == "The Shattered Oath"


def test_sibling_sections_are_untouched():
    raw = json.dumps({
        "campaign": {"name": "Well Formed", "description": "x"},
        "npcs": [{"name": "A"}],
        "scenes": [{"name": "S"}],
    })
    data = parse_campaign_response(raw)
    assert data["npcs"] == [{"name": "A"}]
    assert data["scenes"] == [{"name": "S"}]


def test_top_level_wins_over_nested():
    raw = json.dumps({
        "campaign": {"name": "Both", "npcs": [{"name": "nested"}]},
        "npcs": [{"name": "top"}],
    })
    data = parse_campaign_response(raw)
    assert data["npcs"] == [{"name": "top"}]


if __name__ == "__main__":
    test_nested_sections_are_hoisted()
    print("PASS  nested sections hoisted to top level")
    test_sibling_sections_are_untouched()
    print("PASS  well-formed responses untouched")
    test_top_level_wins_over_nested()
    print("PASS  top-level sections win over nested")
    print("All campaign parse normalization tests passed.")
