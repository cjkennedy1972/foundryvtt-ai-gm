#!/usr/bin/env python3
"""A campaign build quietly produced no settlements for a reasoning model.

SettlementGenerator.generate hand-rolled its own markdown-fence strip — a
fourth copy of that idiom in this codebase — and then called json.loads. It
handles a fence and nothing else. Driving the real generator:

                  bare: generated Oakhaven
         ```json fence: generated Oakhaven
      <think> preamble: FAILED — ValueError: returned invalid JSON
        prose preamble: FAILED — ValueError: returned invalid JSON
         trailing note: FAILED — ValueError: Extra data

Three of five shapes, and the two it handles are the two a reasoning model is
least likely to send.

The failure is not silent here — it raises — but the caller is
SettlementIntegration.generate_settlements_from_campaign, which logs
"Failed to generate settlement 'X'" and moves on. So a campaign build
finishes reporting success with no settlements in it.

utils/json_extract already exists for this, added in #215 when
module_discovery turned out to have the same problem six times over.

Run:
    cd ai-engine && python -m pytest tests/test_settlement_generator_json.py -v
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world.settlement_generator import SettlementGenerator

BODY = json.dumps({
    "name": "Oakhaven",
    "population": 400,
    "buildings": [{"id": "b1", "name": "The Drowned Bell", "building_type": "tavern"}],
    "npcs": [], "factions": [], "hooks": [], "secrets": [],
})


def _generate(text):
    llm = MagicMock()
    llm.generate = AsyncMock(return_value={"text": text})
    return asyncio.run(SettlementGenerator(llm).generate("Oakhaven", "gothic"))


@pytest.mark.parametrize("label,reply", [
    ("bare", BODY),
    ("json fence", f"```json\n{BODY}\n```"),
    ("unlabelled fence", f"```\n{BODY}\n```"),
    ("think block", f"<think>A small town.</think>\n{BODY}"),
    ("think block with braces", f"<think>Maybe {{400}} people.</think>\n{BODY}"),
    ("prose preamble", f"Here is Oakhaven:\n{BODY}"),
    ("trailing note", f"{BODY}\n\nLet me know if you want more detail."),
    ("fence and preamble", f"Sure!\n```json\n{BODY}\n```\nHope that helps."),
])
def test_a_settlement_survives_the_wrapping(label, reply):
    settlement = _generate(reply)

    assert settlement.name == "Oakhaven", label
    assert "b1" in settlement.buildings, f"{label}: the buildings were lost"


def test_a_reply_with_no_json_still_raises_rather_than_returning_an_empty_town():
    """A settlement that silently came back empty would deploy as an empty
    town; the caller logs the failure and skips it instead."""
    with pytest.raises(ValueError, match="invalid JSON"):
        _generate("I'm afraid I can't help with that.")


def test_a_plain_string_response_is_handled_like_a_dict_one():
    """generate() returns either {"text": ...} or a bare string."""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=f"```json\n{BODY}\n```")

    settlement = asyncio.run(SettlementGenerator(llm).generate("Oakhaven", "gothic"))

    assert settlement.name == "Oakhaven"
