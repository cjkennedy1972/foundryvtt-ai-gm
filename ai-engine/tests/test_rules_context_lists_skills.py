#!/usr/bin/env python3
"""get_dnd_rules_context computed the skill list and never interpolated it.

ruff flagged `skills_list` as assigned-but-unused. The prompt says "15 skills
exist, each tied to an ability (e.g., Perception = WIS, Stealth = DEX)" and
then names two of them, so the model had to guess the rest.

That matters because FoundryClient.request_skill_check maps a skill name to
the three-letter code the relay wants via _SKILL_ABBR, and an unrecognised
name is passed through verbatim — so an invented skill reaches the relay as
an invented skill.

Run:
    cd ai-engine && python -m pytest tests/test_rules_context_lists_skills.py -v
"""

import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient
from llm.system_prompts import get_dnd_rules_context
from rules.database import SKILL_ABILITIES


def _normalise(skill: str) -> str:
    """The same normalisation request_skill_check applies before lookup."""
    return skill.strip().lower().replace("_", " ").replace("-", " ")


def test_the_rules_context_names_every_skill():
    context = _normalise(get_dnd_rules_context())

    missing = sorted(s for s in SKILL_ABILITIES if _normalise(s) not in context)
    assert missing == [], f"the model is never told these exist: {missing}"


def test_the_context_says_which_ability_each_skill_uses():
    context = get_dnd_rules_context()

    assert "Stealth (DEX)" in context
    assert "Religion (INT)" in context


def test_every_skill_the_prompt_names_is_one_the_relay_can_roll():
    """rules/database.py spells two-word skills with underscores and
    _SKILL_ABBR with spaces. An unmapped name is passed to the relay
    verbatim, so the two spellings had to be reconciled."""
    unmappable = sorted(
        s for s in SKILL_ABILITIES if _normalise(s) not in FoundryClient._SKILL_ABBR
    )

    assert unmappable == [], f"prompt lists skills the client cannot map: {unmappable}"


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling,code", [
    ("animal_handling", "ani"),
    ("Animal Handling", "ani"),
    ("sleight_of_hand", "slt"),
    ("sleight-of-hand", "slt"),
    ("Perception", "prc"),
])
async def test_the_client_maps_every_spelling_to_the_relays_code(spelling, code):
    """Checked through request_skill_check, not through the test's own copy of
    the normalisation: an unmapped name is passed to the relay verbatim, so
    "animal_handling" reached it as a skill that does not exist."""
    client = FoundryClient()
    client._send = AsyncMock(return_value={})

    await client.request_skill_check("Actor.x", spelling, dc=15)

    assert client._send.await_args.kwargs["skill"] == code


def test_all_eighteen_skills_are_present():
    """Religion was missing; 5e has 18."""
    assert len(SKILL_ABILITIES) == 18
    assert SKILL_ABILITIES["religion"] == "intelligence"


def test_the_conditions_and_dcs_are_still_there():
    """They were already interpolated; the fix must not displace them."""
    from rules.database import CONDITIONS

    context = get_dnd_rules_context()
    assert all(c in context.lower() for c in CONDITIONS)
    assert "Typical DCs" in context
