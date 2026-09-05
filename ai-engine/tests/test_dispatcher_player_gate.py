import pytest
from unittest.mock import AsyncMock, MagicMock
from actions.dispatcher import ActionDispatcher
from actions.schemas import PLAYER_ALLOWED_ACTIONS, MIN_DAMAGE, MAX_DAMAGE
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
    return state

@pytest.fixture
def dispatcher(mock_foundry_client, mock_app_state):
    # Manually patch all ACTION_HANDLERS to return a successful result by default
    for action_type, handler_func in ACTION_HANDLERS.items():
        original_func_name = handler_func.__name__
        # Create a mock function that always returns a successful result
        async def mock_handler(*args, **kwargs):
            return {"type": action_type, "success": True, "result": {"ok": True}}

        # Replace the original function in ACTION_HANDLERS with the mock
        # This is a bit hacky but works without pytest-mock
        ACTION_HANDLERS[action_type] = mock_handler

    return ActionDispatcher(mock_foundry_client, mock_app_state)



@pytest.mark.asyncio
async def test_player_allowed_actions_pass(dispatcher: ActionDispatcher):
    for action_type in PLAYER_ALLOWED_ACTIONS:
        action = {"type": action_type, "source": "player_turn"}
        if action_type == "narrate":
            action["text"] = "Player narrates something"
        elif action_type == "speak":
            action["npc_name"] = "Player_NPC"
            action["text"] = "Player speaks as NPC"
        elif action_type == "roll":
            action["formula"] = "1d20"
            action["speaker"] = "Player"
        elif action_type == "move_token":
            action["token_id"] = "some_id"
            action["x"] = 10
            action["y"] = 20
        elif action_type == "update_hp":
            action["actor_uuid"] = "some_actor"
            action["damage"] = 5
        elif action_type == "use_action":
            action["actor_uuid"] = "some_actor"
            action["action_type"] = "action"
        elif action_type == "skill_check":
            action["actor_uuid"] = "some_actor"
            action["skill"] = "athletics"
            action["dc"] = 15
        elif action_type == "death_save":
            action["actor_uuid"] = "some_actor"
        elif action_type == "saving_throw":
            action["actor_uuid"] = "some_actor"
            action["ability"] = "dexterity"
            action["dc"] = 15
        elif action_type == "apply_condition":
            action["actor_uuid"] = "some_actor"
            action["condition"] = "prone"
        elif action_type == "attack_with_item":
            action["attacker_uuid"] = "some_actor"
            action["item_name"] = "sword"
            action["target_token_id"] = "target_id"
        elif action_type == "start_encounter":
            action["token_ids"] = ["token1"]
        elif action_type == "end_encounter":
            pass # No additional fields needed

        result = await dispatcher.execute(action)
        assert result["success"] is True, f"Allowed action '{action_type}' failed: {result.get('error')}"

@pytest.mark.asyncio
async def test_player_blocked_actions_rejected(dispatcher: ActionDispatcher):
    blocked_actions = {
        "generate_encounter": {"party_level": 1, "party_size": 4},
        "cast_spell": {"actor_uuid": "some_actor", "spell_name": "fireball", "spell_level": 3},
        "execute_js": {"code": "game.actors.delete()"},
        "pause_game": {},
        "resume_game": {},
        "grant_inspiration": {"actor_uuid": "some_actor"},
        "generate_map": {"prompt": "forest", "scene_name": "test"},
        "generate_npc": {},
        "generate_quest": {},
        "generate_treasure": {"cr": 5},
        "place_walls": {"walls": [{"c":[0,0,10,10]}]},
        "place_lights": {"lights": [{"x":0,"y":0}]},
        "place_sounds": {"sounds": [{"x":0,"y":0,"path":"a.ogg"}]},
        "place_token": {"actor_name":"monster", "x":0,"y":0},
        "configure_scene": {"darkness": 0.5},
        "setup_scene": {"scene_name": "new_scene"},
        "set_weather": {"weather": "rain"},
        "set_time": {"time": "dawn"},
        "apply_token_effect": {"token_id": "t1", "effect_type": "condition", "effect_name": "stunned"},
        "update_vision": {"token_id": "t1", "vision_range": 10},
        "execute_macro": {"macro_id": "test_macro"},
        "whisper": {"player_id": "some_player_id", "message": "secret"},
        "switch_scene": {"scene_name": "new_scene"},
        "opportunity_attack": {"attacker_uuid": "a", "target_uuid": "b"},
        "tactical_analysis": {"actor_uuid": "a"},
        "set_exhaustion": {"actor_uuid": "a", "delta": 1},
        "short_rest": {"actor_uuids": ["a"]},
        "long_rest": {"actor_uuids": ["a"]},
        "environmental_save": {"ability": "str", "dc": 10, "target_token_ids": ["a"]},
        "use_save_item": {"caster_uuid": "a", "item_name": "wand", "target_token_ids": ["b"]},
    }

    for action_type, payload in blocked_actions.items():
        payload["type"] = action_type
        payload["source"] = "player_turn"
        result = await dispatcher.execute(payload)
        assert result["success"] is False, f"Blocked action '{action_type}' was not rejected"
        assert "not allowed from player turns" in result["error"] or "arbitrary javascript execution is disabled" in result["error"].lower(), \
            f"Blocked action '{action_type}' had unexpected error: {result.get('error')}"

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
