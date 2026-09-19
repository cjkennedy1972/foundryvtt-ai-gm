#!/usr/bin/env python3
"""A campaign could satisfy its content targets with copies of itself.

_refill_short_arrays tops up arrays that came in under their level-scaled
minimum. It measures the shortfall with len(), and merges the model's reply by
appending. The prompt lists the names already used and says "do not duplicate",
and nothing checks that the model listened.

Driving the real method against a model that echoes the scenes it was told not
to repeat:

    scenes after refill: ['The Sunken Chapel', 'Bell Tower', 'The Sunken
    Chapel', 'Bell Tower', 'the sunken chapel', 'Bell Tower', ...]
    distinct: 2 of 10

The shortfall clears after the first round, because len() went up. The campaign
ships, and deploy writes five copies of The Sunken Chapel into the world and
the vault.

Same shape as the legendary-action fix: the prompt states the rule, the model
is not required to follow it, and the code has to. campaign/generator.py
already carries _norm_scene for exactly this kind of tolerant name match —
reconcile_encounter_scenes uses it because "small models often invent" a scene
name that was never generated.

Run:
    cd ai-engine && python -m pytest tests/test_campaign_refill_dedup.py -v
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.orchestrator import CampaignOrchestrator


def _client(*replies):
    """An LLM that answers each refill round with the next reply."""
    responses = []
    for reply in replies:
        r = MagicMock(status_code=200)
        r.json = MagicMock(
            return_value={"choices": [{"message": {"content": json.dumps(reply)}}]}
        )
        responses.append(r)
    client = MagicMock()
    client.post = AsyncMock(side_effect=responses)
    return client


def _refill(data, client, rounds=2):
    orch = CampaignOrchestrator()
    return asyncio.run(
        orch._refill_short_arrays(data, client, "http://llm", {}, "1-5", max_rounds=rounds)
    )


def _campaign(**arrays):
    return {"campaign": {"name": "Oakhaven", "theme": "gothic"}, **arrays}


def _names(items):
    return [i.get("name") or i.get("title") for i in items]


# ── duplicates never land ─────────────────────────────────────────────────

def test_an_echoed_item_is_not_appended():
    data = _campaign(scenes=[{"name": "The Sunken Chapel"}, {"name": "Bell Tower"}])

    out = _refill(data, _client({"scenes": [{"name": "The Sunken Chapel"}, {"name": "The Crypt"}]}))

    assert _names(out["scenes"]).count("The Sunken Chapel") == 1
    assert "The Crypt" in _names(out["scenes"])


def test_a_duplicate_in_a_different_case_is_still_a_duplicate():
    data = _campaign(scenes=[{"name": "The Sunken Chapel"}])

    out = _refill(data, _client({"scenes": [{"name": "the sunken chapel"}, {"name": "The Crypt"}]}))

    assert len(out["scenes"]) == 2


def test_two_copies_inside_one_reply_land_once():
    data = _campaign(scenes=[{"name": "Bell Tower"}])

    out = _refill(data, _client({"scenes": [{"name": "The Crypt"}, {"name": "The Crypt"}]}))

    assert _names(out["scenes"]).count("The Crypt") == 1


def test_quest_logs_are_matched_on_their_title():
    data = _campaign(quest_logs=[{"title": "The Bone Key"}])

    out = _refill(data, _client({"quest_logs": [{"title": "The Bone Key"}, {"title": "A Debt Repaid"}]}))

    assert len(out["quest_logs"]) == 2


def test_an_all_duplicate_round_stops_the_loop_instead_of_paying_for_another():
    """Echoed items used to count as progress, so the loop kept buying rounds
    and kept growing the array."""
    client = _client(
        {"scenes": [{"name": "Bell Tower"}]},
        {"scenes": [{"name": "The Crypt"}]},
    )
    data = _campaign(scenes=[{"name": "Bell Tower"}])

    out = _refill(data, client, rounds=2)

    assert client.post.await_count == 1, "a round that added nothing bought another"
    assert len(out["scenes"]) == 1


# ── what must still work ──────────────────────────────────────────────────

def test_genuinely_new_items_are_added():
    data = _campaign(scenes=[{"name": "Bell Tower"}])

    out = _refill(data, _client({"scenes": [{"name": "The Crypt"}, {"name": "The Undercroft"}]}))

    assert sorted(_names(out["scenes"])) == ["Bell Tower", "The Crypt", "The Undercroft"]


def test_items_with_no_name_are_all_kept_rather_than_judged():
    """Two nameless items are not evidence of duplication, they are evidence
    of a model that left the name out. Dropping the second loses content."""
    data = _campaign(scenes=[{"name": "Bell Tower"}])

    out = _refill(data, _client({"scenes": [
        {"description": "a nameless hollow"},
        {"description": "a second nameless hollow"},
    ]}))

    assert len(out["scenes"]) == 3


def test_a_refill_that_meets_the_minimum_stops_early():
    data = _campaign(scenes=[{"name": f"Scene {i}"} for i in range(20)],
                     npcs=[{"name": f"NPC {i}"} for i in range(20)],
                     locations=[{"name": f"Loc {i}"} for i in range(20)],
                     quest_logs=[{"title": f"Q {i}"} for i in range(20)],
                     encounters=[{"name": f"E {i}"} for i in range(20)],
                     loot_tables=[{"name": f"L {i}"} for i in range(20)],
                     factions=[{"name": f"F {i}"} for i in range(20)],
                     artifacts=[{"name": f"A {i}"} for i in range(20)])
    client = _client({"scenes": [{"name": "Unwanted"}]})

    _refill(data, client)

    assert client.post.await_count == 0, "nothing was short, so nothing should be requested"
