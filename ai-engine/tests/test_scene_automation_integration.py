"""Integration tests: scene automation actions accessible to LLM and dispatcher.

Verifies that fog of war (update_vision), hazards (environmental_save),
ambient sounds (place_sounds), and GM macros (execute_macro) are:
1. In the system prompt (LLM can propose them)
2. In the action dispatcher (can be executed)
3. Actually execute correctly

Run:
    cd ai-engine && python -m pytest tests/test_scene_automation_integration.py -v
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from actions.dispatcher import ActionDispatcher
from llm.system_prompts import ACTION_FORMAT_INSTRUCTIONS


def test_scene_automation_actions_in_system_prompt():
    """Fog of war, hazards, sounds, and macros are documented as available actions."""
    assert "update_vision" in ACTION_FORMAT_INSTRUCTIONS, "update_vision not in prompt"
    assert "environmental_save" in ACTION_FORMAT_INSTRUCTIONS, "environmental_save not in prompt"
    assert "place_sounds" in ACTION_FORMAT_INSTRUCTIONS, "place_sounds not in prompt"
    assert "execute_macro" in ACTION_FORMAT_INSTRUCTIONS, "execute_macro not in prompt (needs Task 4b)"


def test_scene_automation_actions_in_dispatcher():
    """Fog of war, hazards, sounds, and macros can be dispatched."""
    dispatcher = ActionDispatcher(foundry_client=MagicMock(), app_state=MagicMock())
    available = dispatcher.available_actions
    assert "update_vision" in available, "update_vision not in dispatcher"
    assert "environmental_save" in available, "environmental_save not in dispatcher"
    assert "place_sounds" in available, "place_sounds not in dispatcher"
    assert "execute_macro" in available, "execute_macro not in dispatcher (needs Task 4b)"


def test_execute_macro_calls_effects_manager():
    """execute_macro action calls the effects manager to run the macro."""
    async def run():
        app_state = MagicMock()
        effects_mgr = MagicMock()
        effects_mgr.execute_macro = MagicMock(return_value={"lights": "dimmed", "music": "started"})
        app_state.effects_manager = effects_mgr

        dispatcher = ActionDispatcher(foundry_client=MagicMock(), app_state=app_state)
        result = await dispatcher.execute({
            "type": "execute_macro",
            "macro_id": "dramatic_lighting",
            "overrides": {"intensity": 0.8}
        })

        assert result["type"] == "execute_macro"
        assert result["success"] is True
        effects_mgr.execute_macro.assert_called_once_with(
            "dramatic_lighting", overrides={"intensity": 0.8}
        )

    asyncio.run(run())


def test_execute_macro_fails_safely_without_effects_manager():
    """execute_macro fails gracefully if effects manager is not available."""
    async def run():
        app_state = MagicMock()
        app_state.effects_manager = None  # No effects manager

        dispatcher = ActionDispatcher(foundry_client=MagicMock(), app_state=app_state)
        result = await dispatcher.execute({
            "type": "execute_macro",
            "macro_id": "nonexistent",
        })

        assert result["type"] == "execute_macro"
        assert result["success"] is False
        assert "Effects manager not available" in result.get("error", "")

    asyncio.run(run())
