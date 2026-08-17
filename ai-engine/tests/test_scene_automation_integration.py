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
from unittest.mock import AsyncMock, MagicMock, patch

from actions.dispatcher import ActionDispatcher
from actions.executors import ACTION_HANDLERS
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


def test_execute_macro_dispatches_the_wrapped_action():
    """execute_macro resolves the macro and dispatches the action it wraps.

    The earlier version of this test mocked effects_manager.execute_macro into
    existence — a method EffectsManager never had — so it passed while every
    real macro invocation raised AttributeError. It now runs against the real
    MacroManager and asserts the wrapped action actually reaches a handler.
    """
    async def run():
        from immersion.macros import MacroManager

        app_state = MagicMock()
        app_state.macro_manager = MacroManager()
        app_state.macro_manager.register_macro(
            "dramatic_lighting", "Dramatic Lighting", "dim the lights",
            "narrate", {"text": "The torches gutter and dim."},
        )

        dispatcher = ActionDispatcher(foundry_client=MagicMock(), app_state=app_state)
        app_state.action_dispatcher = dispatcher

        narrate = AsyncMock(return_value={"type": "narrate", "success": True})
        with patch.dict(ACTION_HANDLERS, {"narrate": narrate}):
            result = await dispatcher.execute({
                "type": "execute_macro",
                "macro_id": "dramatic_lighting",
                "overrides": {"text": "The hall falls dark."},
            })

        assert result["type"] == "execute_macro"
        assert result["success"] is True
        assert result["action_type"] == "narrate"
        # The override reached the wrapped action, not just the macro record.
        assert narrate.await_args.kwargs["text"] == "The hall falls dark."

    asyncio.run(run())


def test_execute_macro_fails_safely_without_macro_manager():
    """execute_macro fails gracefully if the macro manager is not available."""
    async def run():
        app_state = MagicMock()
        app_state.macro_manager = None

        dispatcher = ActionDispatcher(foundry_client=MagicMock(), app_state=app_state)
        result = await dispatcher.execute({
            "type": "execute_macro",
            "macro_id": "nonexistent",
        })

        assert result["type"] == "execute_macro"
        assert result["success"] is False
        assert "Macro manager not available" in result.get("error", "")

    asyncio.run(run())


def test_unknown_macro_is_reported_not_dispatched():
    """A macro id that was never registered fails without dispatching anything."""
    async def run():
        from immersion.macros import MacroManager

        app_state = MagicMock()
        app_state.macro_manager = MacroManager()
        dispatcher = ActionDispatcher(foundry_client=MagicMock(), app_state=app_state)
        app_state.action_dispatcher = dispatcher

        result = await dispatcher.execute({"type": "execute_macro", "macro_id": "nope"})

        assert result["success"] is False
        assert "Macro not found" in result.get("error", "")

    asyncio.run(run())


def test_macro_cannot_recurse_into_execute_macro():
    """A macro wrapping execute_macro is rejected instead of looping forever."""
    from immersion.macros import MacroManager

    manager = MacroManager()
    manager.register_macro("loop", "Loop", "recurses", "execute_macro", {"macro_id": "loop"})
    assert "cannot invoke execute_macro" in manager.resolve_macro("loop").get("error", "")
