#!/usr/bin/env python3
"""Tests for the NPC personality data reaching the LLM prompt, and for the
registry not carrying one campaign's NPCs into the next.

Two defects sit behind these. GameLoop._get_npc_context called
`NPCRegistry.get_context(actor_name)`; no such method exists, so every actor
raised AttributeError into a `logger.debug` and the whole Tier 3 personality
block produced nothing. The real method, `get_npc_context`, is keyed by
npc_id — and context/loader.py files vault NPCs under a slug while
chat_listener files generated ones under the display name, so a rename alone
would have fixed the generated NPCs and left every vault NPC silently empty.

The registry half is the same shape as the AI-speaker leak: NPCRegistry has
a `clear()` that nothing outside the test suite ever called, so campaign A's
NPCs stayed live in campaign B. register_vault_npcs skips a name that is
already present, so the stale record won.

Run:
    cd ai-engine && python -m pytest tests/test_npc_personality_injection.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import GameLoop
from npc.registry import NPCRegistry


def _loop(registry, actors):
    """A receiver with only what _get_npc_context touches."""
    foundry = SimpleNamespace(
        get_actors=AsyncMock(return_value=actors),
        get_scene_tokens=AsyncMock(return_value=[]),
        get_scene_details=AsyncMock(return_value={}),
    )
    tracker = SimpleNamespace(
        get_encounter_context=MagicMock(return_value=""),
        state=SimpleNamespace(current_scene=""),
    )
    return SimpleNamespace(
        _campaign_loader=None,
        foundry=foundry,
        _npc_registry=registry,
        state_tracker=tracker,
        _ambient_manager=None,
    )


def _context(registry, actors):
    return asyncio.run(GameLoop._get_npc_context(_loop(registry, actors)))


def test_generated_npc_personality_reaches_the_prompt():
    """An NPC filed under its display name, as chat_listener files them."""
    registry = NPCRegistry()
    registry.register_npc(
        npc_id="Mira Fenwick",
        npc_name="Mira Fenwick",
        description="A cheerful innkeeper.",
        class_name="Commoner",
        level=2,
    )
    registry.set_npc_personality("Mira Fenwick", {"social": ["cheerful", "generous"]})

    out = _context(registry, [{"name": "Mira Fenwick", "uuid": "Actor.abc", "hp": 9, "max_hp": 9}])

    assert "cheerful" in out, "personality traits never reached the prompt"


def test_vault_npc_personality_reaches_the_prompt():
    """A vault NPC is filed under a slug id, not its display name — looking it
    up by the Foundry actor name has to resolve that."""
    registry = NPCRegistry()
    registry.register_npc(
        npc_id="elara_windborne",
        npc_name="Elara Windborne",
        description="Keeper of the north gate.",
        alignment="Lawful Neutral",
    )
    registry.set_npc_personality("elara_windborne", {"manner": ["severe"]})

    out = _context(registry, [{"name": "Elara Windborne", "uuid": "Actor.def", "hp": 14, "max_hp": 14}])

    assert "severe" in out, "vault NPCs are filed under a slug id and were never found by name"


def test_actor_with_no_registry_record_still_lists():
    """A plain actor must not lose its line because the registry has nothing."""
    out = _context(NPCRegistry(), [{"name": "Town Guard", "uuid": "Actor.ghi", "hp": 11, "max_hp": 11}])

    assert "Town Guard" in out
    assert "Actor.ghi" in out


def test_registry_clear_drops_the_uuid_maps_too():
    """A half-cleared registry maps campaign A's uuid onto campaign B's ids."""
    registry = NPCRegistry()
    registry.register_npc(npc_id="a", npc_name="Alia", description="")
    registry.map_actor_to_npc("Actor.old", "a")

    registry.clear()

    assert registry.get_npc_by_actor_uuid("Actor.old") is None
    assert registry.get_actor_uuid_for_npc("a") is None
