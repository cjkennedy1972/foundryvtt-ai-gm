"""Mechanical actions from a player turn must carry source="player_turn".

The dispatcher's PLAYER_ALLOWED_ACTIONS gate keyed on that field, and only
_dispatch_narration_now ever set it. That method handles narrate/speak only,
and both are on the allowlist, so the gate never rejected anything: every
world-destructive type stayed reachable from a prompt-injected player message.
"""

from actions.schemas import ACTION_SCHEMAS, PLAYER_ALLOWED_ACTIONS
from conftest import BLOCKED_FROM_PLAYER_TURNS

# The gate's runtime behaviour is covered in test_dispatcher_player_gate.py,
# which drives a real ActionDispatcher over the same two sets. What is left here
# is the shape of the policy itself.


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


def test_stamp_model_origin_makes_blocked_types_undispatchable():
    from actions.schemas import stamp_model_origin

    batch = stamp_model_origin([{"type": "pause_game"}, {"type": "narrate", "text": "x"}])
    assert all(a["source"] == "model_turn" for a in batch)
    assert stamp_model_origin([]) == []


def test_update_hp_cannot_target_non_hp_attributes():
    import pytest
    from pydantic import ValidationError

    from actions.schemas import UpdateHpAction

    assert UpdateHpAction(actor_uuid="x", damage=3, hp_path="hp.temp").hp_path == "hp.temp"
    for bad in ("hp.max", "abilities.con.value", "currency.gp"):
        with pytest.raises(ValidationError):
            UpdateHpAction(actor_uuid="x", damage=3, hp_path=bad)


def test_model_turn_keeps_macros_but_blocks_code_execution_and_pausing():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from actions.dispatcher import ActionDispatcher
    from actions.executors import ACTION_HANDLERS
    from actions.schemas import MODEL_ALLOWED_ACTIONS, MODEL_BLOCKED_ACTIONS, stamp_model_origin

    assert MODEL_BLOCKED_ACTIONS == {"execute_js", "pause_game", "resume_game"}
    assert "execute_macro" in MODEL_ALLOWED_ACTIONS
    assert stamp_model_origin([{"type": "narrate"}])[0]["source"] == "model_turn"
    assert stamp_model_origin([{"type": "narrate"}], "player_turn")[0]["source"] == "player_turn"

    d = ActionDispatcher(AsyncMock(), MagicMock())
    res = asyncio.run(d.execute({"type": "pause_game", "source": "model_turn"}))
    assert res["success"] is False and "not allowed" in res["error"]
    assert "pause_game" in ACTION_HANDLERS  # it is the gate, not a missing handler, that refuses it
