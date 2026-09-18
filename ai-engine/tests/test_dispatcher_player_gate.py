import pytest
from unittest.mock import AsyncMock, MagicMock
from actions.dispatcher import ActionDispatcher
from actions.schemas import PLAYER_ALLOWED_ACTIONS, MIN_DAMAGE, MAX_DAMAGE
from conftest import BLOCKED_FROM_PLAYER_TURNS, minimal_action_payload
from actions.executors import ACTION_HANDLERS
from config import settings
@pytest.fixture
def mock_foundry_client():
    client = AsyncMock()
    client.is_connected = True
    client.execute_js.return_value = {"result": {"ok": True, "hit": True, "attackTotal": 20, "targetAc": 15, "damageTotal": 10}}
    return client

@pytest.fixture
def mock_app_state():
    state = MagicMock()
    state.macro_manager.resolve_macro = MagicMock(return_value={"type": "narrate", "text": "Macro narration"})
    return state

@pytest.fixture
def dispatcher(mock_foundry_client, mock_app_state, monkeypatch):
    # Manually patch all ACTION_HANDLERS to return a successful result by default
    for action_type in ACTION_HANDLERS:
        # Create a mock function that always returns a successful result
        async def mock_handler(*args, _action_type=action_type, **kwargs):
            return {"type": _action_type, "success": True, "result": {"ok": True}}

        # Replace the original function in ACTION_HANDLERS with the mock
        # This is a bit hacky but works without pytest-mock
        monkeypatch.setitem(ACTION_HANDLERS, action_type, mock_handler)

    return ActionDispatcher(mock_foundry_client, mock_app_state)



@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", sorted(PLAYER_ALLOWED_ACTIONS))
async def test_player_allowed_actions_pass(dispatcher: ActionDispatcher, action_type):
    """Every allowlisted type must survive a player turn.

    Payloads come from conftest.minimal_action_payload instead of the hand-written
    if/elif chain this used to carry: that chain covered only the 13 original
    entries, so it silently failed the moment the allowlist grew.
    """
    action = minimal_action_payload(action_type)
    action["source"] = "player_turn"

    result = await dispatcher.execute(action)

    assert result["success"] is True, f"allowed action {action_type!r} failed: {result.get('error')}"


@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", sorted(BLOCKED_FROM_PLAYER_TURNS))
async def test_player_blocked_actions_rejected(dispatcher: ActionDispatcher, action_type):
    """Every world-destructive type must be refused on a player turn."""
    action = minimal_action_payload(action_type)
    action["source"] = "player_turn"

    result = await dispatcher.execute(action)

    assert result["success"] is False, f"blocked action {action_type!r} was not rejected"
    error = result["error"].lower()
    assert (
        "not allowed from player turns" in error
        or "arbitrary javascript execution is disabled" in error
        or "execute_js is disabled" in error
    ), f"blocked action {action_type!r} had unexpected error: {result.get('error')}"


@pytest.mark.asyncio
async def test_gm_and_untagged_actions_pass(dispatcher: ActionDispatcher):
    # GM action (no source)
    gm_action = {"type": "narrate", "text": "GM narrates something"}
    result = await dispatcher.execute(gm_action)
    assert result["success"] is True, f"GM action failed: {result.get('error')}"

    # Untagged action (defaults to GM trusted)
    untagged_action = {"type": "generate_encounter", "party_level": 1, "party_size": 4}
    result = await dispatcher.execute(untagged_action)
    assert result["success"] is True, f"Untagged action failed: {result.get('error')}"

@pytest.mark.asyncio
async def test_execute_js_gate_unaffected(dispatcher: ActionDispatcher):
    # execute_js should still be blocked if settings.allow_execute_js is False (default)
    action = {"type": "execute_js", "code": "console.log('hello')", "source": "player_turn"}
    result = await dispatcher.execute(action)
    assert result["success"] is False
    assert "not allowed from player turns" in result["error"].lower()

    # Temporarily enable execute_js
    old_allow_execute_js = getattr(settings, "allow_execute_js", False)
    settings.allow_execute_js = True
    action = {"type": "execute_js", "code": "console.log('hello')", "source": None} # GM-initiated
    result = await dispatcher.execute(action)
    assert result["success"] is True # This should now pass for GM-initiated calls

    # Restore setting
    settings.allow_execute_js = old_allow_execute_js

@pytest.mark.asyncio
async def test_damage_clamping_unaffected(dispatcher: ActionDispatcher):
    # Damage above MAX_DAMAGE
    action = {"type": "update_hp", "actor_uuid": "some_actor", "damage": MAX_DAMAGE + 10, "source": "player_turn"}
    result = await dispatcher.execute(action)
    assert result["success"] is False
    assert "input should be less than or equal to 500" in result["error"].lower()

    # Damage below MIN_DAMAGE (healing)
    action = {"type": "update_hp", "actor_uuid": "some_actor", "damage": MIN_DAMAGE - 10, "source": "player_turn"}
    result = await dispatcher.execute(action)
    assert result["success"] is False
    assert "input should be greater than or equal to -200" in result["error"].lower()

@pytest.mark.asyncio
async def test_macro_provenance_propagation(dispatcher: ActionDispatcher, mock_app_state):
    # A player-triggered macro resolving to a blocked action should be rejected
    mock_app_state.macro_manager.resolve_macro.return_value = {
        "type": "cast_spell",
        "actor_uuid": "some_actor",
        "spell_name": "fireball",
        "spell_level": 3,
    }
    macro_action_player = {"type": "execute_macro", "macro_id": "player_cast_spell", "source": "player_turn"}
    result = await dispatcher.execute(macro_action_player)
    assert result["success"] is False
    assert "not allowed from player turns" in result["error"]

    # A GM-triggered macro resolving to a blocked action should pass
    mock_app_state.macro_manager.resolve_macro.return_value = {
        "type": "cast_spell",
        "actor_uuid": "some_actor",
        "spell_name": "fireball",
        "spell_level": 3,
    }
    macro_action_gm = {"type": "execute_macro", "macro_id": "gm_cast_spell"} # No source, defaults to GM
    result = await dispatcher.execute(macro_action_gm)
    assert result["success"] is True # Should pass as GM-triggered, no player restriction

    # A player-triggered macro resolving to an allowed action should pass
    mock_app_state.macro_manager.resolve_macro.return_value = {
        "type": "narrate",
        "text": "Macro narration from player"
    }
    macro_action_allowed_player = {"type": "execute_macro", "macro_id": "player_narrate", "source": "player_turn"}
    result = await dispatcher.execute(macro_action_allowed_player)
    assert result["success"] is False
    assert "not allowed from player turns" in result["error"]
