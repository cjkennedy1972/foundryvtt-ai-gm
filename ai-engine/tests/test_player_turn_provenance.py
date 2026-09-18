"""Mechanical actions from a player turn must carry source="player_turn".

The dispatcher's PLAYER_ALLOWED_ACTIONS gate keyed on that field, and only
_dispatch_narration_now ever set it. That method handles narrate/speak only,
and both are on the allowlist, so the gate never rejected anything: every
world-destructive type stayed reachable from a prompt-injected player message.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from actions.schemas import ACTION_SCHEMAS, PLAYER_ALLOWED_ACTIONS
from conftest import BLOCKED_FROM_PLAYER_TURNS, minimal_action_payload

# execute_js and execute_macro have their own gates, which reject first and
# would mask whether the provenance gate fired.
_SELF_GATED = {"execute_js", "execute_macro"}
_GATE_TESTABLE = sorted(BLOCKED_FROM_PLAYER_TURNS - _SELF_GATED)


def test_destructive_types_are_not_player_reachable():
    leaked = sorted(BLOCKED_FROM_PLAYER_TURNS & PLAYER_ALLOWED_ACTIONS)
    assert leaked == [], f"world-destructive types on the player allowlist: {leaked}"


def test_allowlist_has_no_stale_entries():
    """A typo'd or removed action type would silently allow nothing."""
    unknown = sorted(PLAYER_ALLOWED_ACTIONS - set(ACTION_SCHEMAS))
    assert unknown == [], f"allowlist names with no schema: {unknown}"


def test_every_action_type_is_classified():
    """New types default to blocked; this catches ones nobody triaged."""
    unclassified = sorted(set(ACTION_SCHEMAS) - PLAYER_ALLOWED_ACTIONS - BLOCKED_FROM_PLAYER_TURNS)
    assert unclassified == [], (
        "action types in neither the allowlist nor BLOCKED_FROM_PLAYER_TURNS — decide "
        f"which and update both: {unclassified}"
    )


def test_ordinary_play_is_not_gated():
    """The gate must not touch anything the recorded corpus relies on.

    evals/scenarios/ has the model emitting these on player turns, and the E2E
    harness asserts a failed place_token retries successfully. An earlier
    version of this policy blocked all four and broke both.
    """
    used_in_real_play = {"switch_scene", "setup_scene", "place_token", "generate_treasure"}

    blocked = sorted(used_in_real_play - PLAYER_ALLOWED_ACTIONS)

    assert blocked == [], f"recorded GM behaviour would be rejected: {blocked}"


@pytest.fixture
def dispatcher(monkeypatch):
    """Real ActionDispatcher with every handler stubbed to succeed.

    Anything the gate lets through therefore reaches a handler and reports
    success, so a rejection can only have come from the gate itself.
    """
    from actions.dispatcher import ActionDispatcher
    from actions.executors import ACTION_HANDLERS

    for action_type in ACTION_HANDLERS:
        async def stub(*args, _t=action_type, **kwargs):
            return {"type": _t, "success": True, "result": {"ok": True}}
        monkeypatch.setitem(ACTION_HANDLERS, action_type, stub)

    foundry = AsyncMock()
    foundry.is_connected = True
    return ActionDispatcher(foundry, MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", _GATE_TESTABLE)
async def test_stamped_destructive_action_is_rejected(dispatcher, action_type):
    body = minimal_action_payload(action_type)
    body["source"] = "player_turn"

    result = await dispatcher.execute(body)

    assert result["success"] is False
    assert "not allowed from player turns" in result["error"].lower(), result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", _GATE_TESTABLE)
async def test_gate_stays_silent_when_gm_initiated(dispatcher, action_type):
    """Proves the rejection above came from the gate, not from validation.

    Asserts on the absence of the gate's message rather than on success: the
    synthesized payloads satisfy each schema's required fields but not every
    deeper validator, and which ones is beside the point here.
    """
    result = await dispatcher.execute(minimal_action_payload(action_type))

    assert "not allowed from player turns" not in str(result.get("error", "")).lower()
