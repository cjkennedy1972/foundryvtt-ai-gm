#!/usr/bin/env python3
"""Tests for GameLoop._handle_generated_npcs — the Tier 5 hook that files a
procedurally generated NPC into the personality registry.

These deliberately drive the REAL NPCRegistry and PersonalityEngine rather
than mocks. The bug they exist to catch was pure signature drift: the hook
called a `PersonalityEngine.extract_traits` that does not exist and passed
`register_npc(name=..., npc_class=...)` for parameters named `npc_name` and
`class_name`. Every generated NPC therefore hit the handler's own
`except Exception` and was silently dropped — which mocked collaborators
would have happily accepted.

Run:
    cd ai-engine && python -m pytest tests/test_generated_npc_registration.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import GameLoop
from npc.personality import PersonalityEngine
from npc.registry import NPCRegistry

GENERATED = {
    "type": "generate_npc",
    "npc": {
        "name": "Mira Fenwick",
        "description": "A cheerful, generous innkeeper who is always curious about travellers.",
        "class": "Commoner",
        "race": "Halfling",
        "level": 2,
        "alignment": "Neutral Good",
    },
}


def _loop():
    return SimpleNamespace(
        _npc_registry=NPCRegistry(),
        _personality_engine=PersonalityEngine(),
    )


def test_generated_npc_lands_in_the_registry():
    loop = _loop()

    asyncio.run(GameLoop._handle_generated_npcs(loop, [GENERATED]))

    record = loop._npc_registry.get_npc("Mira Fenwick")
    assert record is not None, "generated NPC was dropped instead of registered"
    assert record.npc_name == "Mira Fenwick"
    assert record.class_name == "Commoner"
    assert record.level == 2
    assert record.alignment == "Neutral Good"


def test_generated_npc_gets_its_personality_parsed():
    """Registering without traits is the half-done case the handler's name
    promises it does not leave behind."""
    loop = _loop()

    asyncio.run(GameLoop._handle_generated_npcs(loop, [GENERATED]))

    record = loop._npc_registry.get_npc("Mira Fenwick")
    assert record.personality, "personality traits were extracted and thrown away"


def test_non_npc_results_are_ignored():
    loop = _loop()

    asyncio.run(GameLoop._handle_generated_npcs(loop, [
        "not a dict",
        {"type": "generate_treasure", "treasure": {"total_value_gp": 40}},
        {"type": "generate_npc"},  # no npc payload
    ]))

    assert loop._npc_registry.list_npcs() == []
