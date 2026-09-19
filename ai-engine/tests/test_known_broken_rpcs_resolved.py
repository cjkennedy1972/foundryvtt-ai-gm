#!/usr/bin/env python3
"""The three RPC types left in KNOWN_BROKEN by #178, resolved.

Each sent a message type the relay answers with
{"type":"error","error":"Unknown message type"}, which _send raises on — so
each was an action that failed every time it was used. A rename would not
fix any of them, which is why they were named and waived rather than
patched.

  get-tactical-data   FoundryClient.get_tactical_data had no callers at all.
                      Dead and broken. Deleted; combat/tactics.py and
                      scripts.tactical_scene_state cover tactical state.

  track-action        use_action had no relay endpoint, no core dnd5e field
                      to write, and nothing read its result — only the
                      system prompt advertised it. Combat already tracks the
                      action economy it actually needs, in
                      _attacks_used_this_turn. Withdrawn, so the model stops
                      being offered a move that always errors.

  opportunity-attack  Reachable only for NPC attackers; a PC defers to the
                      player under players_roll_own. Now resolved through
                      the same path a normal attack takes, which already
                      maps an actor uuid to a scene token.

Run:
    cd ai-engine && python -m pytest tests/test_known_broken_rpcs_resolved.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.dispatcher import ACTION_HANDLERS
from actions.schemas import ACTION_SCHEMAS
from foundry.client import FoundryClient


# ── the waiver list is empty ──────────────────────────────────────────────

def test_nothing_is_waived_any_more():
    from tests.test_relay_protocol_contract import KNOWN_BROKEN

    assert KNOWN_BROKEN == {}, f"still shipping actions that always fail: {sorted(KNOWN_BROKEN)}"


# ── get-tactical-data ─────────────────────────────────────────────────────

def test_the_dead_tactical_data_call_is_gone():
    assert not hasattr(FoundryClient, "get_tactical_data")


def test_tactical_state_still_has_a_live_path():
    """Deleting the broken one must not remove the working one."""
    from foundry import scripts
    import combat.tactics as tactics

    assert callable(scripts.tactical_scene_state)
    assert callable(tactics.build_tactical_snapshot)


# ── track-action ──────────────────────────────────────────────────────────

def test_use_action_is_no_longer_offered_to_the_model():
    assert "use_action" not in ACTION_SCHEMAS
    assert "use_action" not in ACTION_HANDLERS


def test_the_system_prompt_no_longer_advertises_it():
    from llm.system_prompts import build_system_prompt

    assert "use_action" not in build_system_prompt()


def test_combat_still_tracks_the_economy_it_actually_uses():
    """Multiattack accounting is real and unrelated to the withdrawn action."""
    import inspect

    from combat.loop import CombatLoop

    assert "_attacks_used_this_turn" in inspect.getsource(CombatLoop._process_npc_turn)


# ── opportunity-attack ────────────────────────────────────────────────────

def _foundry(items=("Scimitar",)):
    foundry = AsyncMock()
    foundry.get_actors = AsyncMock(return_value=[])
    foundry.execute_js = AsyncMock(return_value={"result": list(items)})
    foundry.get_scene_tokens = AsyncMock(return_value=[
        {"id": "tokTARGET", "name": "Elara", "actorUuid": "Actor.elara"},
    ])
    foundry.chat_message = AsyncMock()
    return foundry


@pytest.mark.asyncio
async def test_an_npc_opportunity_attack_resolves_as_a_real_attack():
    from actions.executors import execute_opportunity_attack

    foundry = _foundry()
    with patch("config.settings.players_roll_own", False):
        result = await execute_opportunity_attack(
            "Actor.goblin", "Actor.elara", reason="left reach", foundry=foundry
        )

    assert result["type"] == "opportunity_attack"
    scripts_run = [c.args[0] for c in foundry.execute_js.await_args_list if c.args]
    assert any("resolve" in js.lower() or "rollAttack" in js for js in scripts_run), \
        "the attack was never actually resolved"


@pytest.mark.asyncio
async def test_an_npc_with_no_attack_items_reports_why():
    from actions.executors import execute_opportunity_attack

    foundry = _foundry(items=())
    with patch("config.settings.players_roll_own", False):
        result = await execute_opportunity_attack(
            "Actor.goblin", "Actor.elara", foundry=foundry
        )

    assert result.get("success") is False
    assert result.get("error")


@pytest.mark.asyncio
async def test_a_player_character_is_still_asked_to_roll_their_own():
    """The reaction belongs to the player when players_roll_own is set."""
    from actions.executors import execute_opportunity_attack

    foundry = _foundry()
    with patch("config.settings.players_roll_own", True), \
         patch("actions.executors._player_actor_name", AsyncMock(return_value="Thorin")):
        result = await execute_opportunity_attack(
            "Actor.thorin", "Actor.elara", foundry=foundry
        )

    assert result.get("deferred_to_player") is True
    foundry.chat_message.assert_awaited()
