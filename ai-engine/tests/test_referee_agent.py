#!/usr/bin/env python3
"""Tests for referee.agent.RefereeAgent — DC-band adjudication, spell-slot
legality (checked live against Foundry, not a shadow ledger), and the
approve/reject contract consumed by chat_listener._process_player_input.

Run:
    cd ai-engine && python -m pytest tests/test_referee_agent.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from referee.agent import RefereeAgent
from referee.models import Ruling


def test_standard_dc_approved_unchanged():
    ref = RefereeAgent()
    action = {"type": "skill_check", "actor_uuid": "a1", "skill": "stealth", "dc": 15}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True
    assert ruling.action["dc"] == 15
    assert ruling.reason is None


def test_dc_within_tolerance_approved_unchanged():
    """DC 18 is within 5 of the 'hard' band (20) — a deliberate in-between
    value, not clamped."""
    ref = RefereeAgent()
    action = {"type": "saving_throw", "actor_uuid": "a1", "ability": "dexterity", "dc": 18}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True
    assert ruling.action["dc"] == 18


def test_extreme_dc_clamped_to_nearest_band():
    ref = RefereeAgent()
    action = {"type": "skill_check", "actor_uuid": "a1", "skill": "arcana", "dc": 40}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True
    assert ruling.action["dc"] == 30  # nearest band: nearly_impossible
    assert "clamped" in ruling.reason
    # Original action dict must not be mutated in place.
    assert action["dc"] == 40


def test_non_dc_action_passes_through():
    ref = RefereeAgent()
    action = {"type": "narrate", "text": "The torch flickers."}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True
    assert ruling.action == action


def test_adjudicate_batch_preserves_order():
    ref = RefereeAgent()
    actions = [
        {"type": "narrate", "text": "..."},
        {"type": "skill_check", "actor_uuid": "a1", "skill": "stealth", "dc": 40},
    ]
    rulings = asyncio.run(ref.adjudicate_batch(actions))
    assert len(rulings) == 2
    assert all(isinstance(r, Ruling) for r in rulings)
    assert rulings[0].action["type"] == "narrate"
    assert rulings[1].action["dc"] == 30


def test_adjudication_error_fails_open():
    """A referee bug must never block play — it approves the action
    unchanged and records the failure in notes instead."""
    ref = RefereeAgent()
    # dc is a string, not an int -- abs(band - dc) raises TypeError inside _check_dc_band.
    action = {"type": "skill_check", "actor_uuid": "a1", "skill": "stealth", "dc": "fifteen"}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True
    assert ruling.action is action
    assert ruling.notes and "adjudication error" in ruling.notes[0]


def _foundry_with_slots(slots: dict):
    """slots matches the REAL foundry/scripts.py::get_spell_slots shape:
    {"1": {value, max}, ..., "pact": {value, max, casterLevel}} — no
    wrapper key, only levels with max > 0 present."""
    foundry = MagicMock()
    foundry.get_spell_slots = AsyncMock(return_value=slots)
    return foundry


def test_cantrip_skips_slot_check_even_with_no_foundry():
    ref = RefereeAgent()  # no foundry configured
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Fire Bolt", "spell_level": 0}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True


def test_ritual_cast_skips_slot_check():
    foundry = _foundry_with_slots({"1": {"value": 0, "max": 2}})
    ref = RefereeAgent(foundry=foundry)
    action = {
        "type": "cast_spell", "actor_uuid": "a1", "spell_name": "Detect Magic",
        "spell_level": 1, "ritual": True,
    }
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True
    foundry.get_spell_slots.assert_not_called()


def test_no_foundry_client_fails_open_on_cast_spell():
    """Without a FoundryClient (tests, NPCAgent turns without one wired),
    the Referee can't verify slots — it approves rather than blocking."""
    ref = RefereeAgent()
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Magic Missile", "spell_level": 1}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True


def test_available_slot_at_exact_level_approved():
    foundry = _foundry_with_slots({"1": {"value": 2, "max": 4}})
    ref = RefereeAgent(foundry=foundry)
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Magic Missile", "spell_level": 1}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True


def test_upcast_from_higher_slot_approved():
    """5e rule: a level-3 slot can cast a level-1 spell."""
    foundry = _foundry_with_slots({"1": {"value": 0, "max": 4}, "3": {"value": 1, "max": 2}})
    ref = RefereeAgent(foundry=foundry)
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Magic Missile", "spell_level": 1}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True


def test_no_available_slot_rejected():
    foundry = _foundry_with_slots({"1": {"value": 0, "max": 4}, "2": {"value": 0, "max": 2}})
    ref = RefereeAgent(foundry=foundry)
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Magic Missile", "spell_level": 1}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is False
    assert "level 1" in ruling.reason


def test_warlock_pact_slot_approved_via_caster_level():
    """Pact Magic slots aren't keyed by spell level — they're all castable
    up to the Warlock's casterLevel."""
    foundry = _foundry_with_slots({"pact": {"value": 1, "max": 2, "casterLevel": 3}})
    ref = RefereeAgent(foundry=foundry)
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Hex", "spell_level": 1}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True


def test_warlock_pact_slot_exhausted_rejected():
    foundry = _foundry_with_slots({"pact": {"value": 0, "max": 2, "casterLevel": 3}})
    ref = RefereeAgent(foundry=foundry)
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Hex", "spell_level": 1}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is False


def test_unreadable_sheet_fails_open():
    """Actor not found / relay hiccup -> empty slots dict -> approve rather
    than block on data we can't verify."""
    foundry = _foundry_with_slots({})
    ref = RefereeAgent(foundry=foundry)
    action = {"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Magic Missile", "spell_level": 1}
    ruling = asyncio.run(ref.adjudicate(action))
    assert ruling.approved is True
