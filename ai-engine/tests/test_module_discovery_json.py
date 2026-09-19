#!/usr/bin/env python3
"""Module discovery only understood a model that answered in bare JSON.

campaign/generator.py's parse_campaign_response has documented for a while
what local models actually send back: ```json fences, chain-of-thought
preambles, and <think>...</think> blocks, naming Qwen3's reasoning tokens
specifically. campaign/importer.py and context/canon.py each carried their own
byte-identical _extract_json_object for the same reason.

campaign/module_discovery.py called json.loads on the raw response six times,
each inside `except json.JSONDecodeError: pass`. Driving the real mapper:

              raw JSON: 1 synergies mapped
         ```json fence: 0 synergies mapped
      <think> preamble: 0 synergies mapped
        prose preamble: 0 synergies mapped

Silently. Module capabilities came back empty and synergy mapping returned
nothing, which is what the optimizer above it then reported.

The two identical private copies are now one shared helper, which also strips
<think> blocks — neither copy did, and a reasoning block containing a brace
defeats the outermost-{...} scan on its own.

Run:
    cd ai-engine && python -m pytest tests/test_module_discovery_json.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.module_discovery import ModuleDiscovery, ModuleSynergyMapper
from utils.json_extract import extract_json_object

MAPPING = '{"scene": "The Sunken Chapel", "synergies": [{"module": "fxmaster", "enhancement": "rain"}]}'
ANALYSIS = {
    "scenes": [{"name": "The Sunken Chapel", "drama_level": 8}],
    "encounters": [], "npcs": [], "narrative_arcs": [],
}


def _module():
    return MagicMock(id="fxmaster", name="FXMaster", capabilities=["weather"], enabled=True)


def _map(reply):
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value=reply)
    return asyncio.run(
        ModuleSynergyMapper(llm_manager=llm).map_synergies(ANALYSIS, [_module()], llm_manager=llm)
    )


# ── the shapes models actually send ───────────────────────────────────────

@pytest.mark.parametrize("label,reply", [
    ("bare", MAPPING),
    ("fenced", f"```json\n{MAPPING}\n```"),
    ("unlabelled fence", f"```\n{MAPPING}\n```"),
    ("prose preamble", f"Here is the mapping:\n{MAPPING}"),
    ("think block", f"<think>The chapel is wet.</think>\n{MAPPING}"),
    ("think block with braces", f"<think>Maybe {{a: 1}} fits.</think>\n{MAPPING}"),
    ("trailing commentary", f"{MAPPING}\n\nLet me know if you want more."),
])
def test_a_synergy_survives_the_wrapping(label, reply):
    out = _map(reply)

    assert len(out["scene_enhancements"]) == 1, f"{label}: the mapping was dropped"


def test_an_unusable_reply_yields_nothing_and_says_so(caplog):
    """It used to be silent, which is why nobody noticed six of these."""
    import logging

    with caplog.at_level(logging.WARNING):
        out = _map("I'm afraid I can't help with that.")

    assert out["scene_enhancements"] == []
    assert any("No usable JSON" in r.message for r in caplog.records)


def test_module_analysis_survives_a_fenced_reply():
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value=(
        '```json\n{"capabilities": ["weather", "particles"], '
        '"narrative_use_cases": ["storm over the chapel"]}\n```'
    ))
    discovery = ModuleDiscovery(llm_manager=llm)

    info = asyncio.run(discovery._enhance_with_llm(
        "fxmaster", {"name": "FXMaster", "enabled": True}, llm
    ))

    assert info.capabilities == ["weather", "particles"]


# ── the shared helper ─────────────────────────────────────────────────────

def test_the_extractor_returns_none_rather_than_raising():
    assert extract_json_object("") is None
    assert extract_json_object("no json here") is None
    assert extract_json_object("{not valid json") is None
    # Matching braces that still will not parse — the only input that reaches
    # the JSONDecodeError branch at all. Without it, a version that re-raised
    # passed every test here.
    assert extract_json_object("{not: valid, 'quite'}") is None
    assert extract_json_object('prose {"a": 1,} more') is None


def test_the_extractor_takes_the_outermost_object():
    assert extract_json_object('prefix {"a": {"b": 1}} suffix') == {"a": {"b": 1}}


def test_the_extractor_drops_a_reasoning_block_before_scanning():
    """Without the strip, the brace inside <think> starts the scan too early
    and the whole thing fails to parse."""
    assert extract_json_object('<think>try {"a": 2}</think>{"a": 1}') == {"a": 1}
