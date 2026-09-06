#!/usr/bin/env python3
"""Tests for the procedural generate_* GM actions.

Each of these executors called a method ProceduralGenerator does not have
(gen.generate_npc / gen.generate_quest / self.generate_settlement) and then
read the result with dict .get() against a dataclass. Every call raised
AttributeError straight into the executor's own `except Exception`, which
turns it into an `error` key on an otherwise well-formed result — so the
action reported failure the same way a real Foundry outage would and nobody
noticed the action had never worked at all. execute_generate_treasure carried
the identical bug and was fixed earlier; these are the rest of the family.

The assertions therefore check for real generated content, not just absence
of an exception: `"error" not in result` is the load-bearing one.

Run:
    cd ai-engine && python -m pytest tests/test_procedural_executors.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.executors import execute_generate_npc, execute_generate_quest
from procedural.generator import ProceduralGenerator


def test_generate_npc_action_returns_an_npc():
    result = asyncio.run(execute_generate_npc(foundry=None))

    assert "error" not in result, result.get("error")
    npc = result["npc"]
    assert npc["name"]
    assert npc["race"]
    assert npc["class"]
    assert npc["level"] >= 1
    assert npc["description"]
    # GeneratedNPC has no alignment of its own; the neutral default stands.
    assert npc["alignment"] == "Neutral"


def test_generate_quest_action_returns_a_quest():
    result = asyncio.run(execute_generate_quest(foundry=None))

    assert "error" not in result, result.get("error")
    quest = result["quest"]
    assert quest["title"]
    assert quest["objective"]
    assert quest["reward"]
    assert quest["objectives"], "resolution options should reach the caller"
    assert quest["difficulty"] == "medium"


def test_generate_quest_keeps_the_requested_difficulty():
    """The generator produces no difficulty, so the caller's must survive."""
    result = asyncio.run(execute_generate_quest(difficulty="deadly", foundry=None))

    assert result["quest"]["difficulty"] == "deadly"


def test_session_can_include_a_settlement():
    session = ProceduralGenerator().generate_session(3, include_settlement=True)

    settlement = session["settlement"]
    assert settlement.name
    assert settlement.size == "village"
    assert settlement.buildings
