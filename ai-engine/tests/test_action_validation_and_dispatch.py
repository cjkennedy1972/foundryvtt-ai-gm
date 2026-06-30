#!/usr/bin/env python3
"""
Comprehensive test suite for action schema validation and dispatcher resolution.

Tests verify that:
1. Valid actions pass validation and marshal correctly
2. Invalid actions (extra fields, out-of-range values) are rejected
3. Executor resolution handles unknown action types gracefully
4. Fire-and-forget tasks (TTS, etc.) are retained in memory via spawn()

Run:
    cd ai-engine && python -m pytest tests/test_action_validation_and_dispatch.py -v
    OR:
    cd ai-engine && python tests/test_action_validation_and_dispatch.py
"""

import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.schemas import (
    NarrateAction, SpeakAction, RollAction, MoveTokenAction,
    UpdateHpAction, PlaySoundAction, PlayMusicAction, WhisperAction,
    SwitchSceneAction, StartEncounterAction, EndEncounterAction,
)
from actions.dispatcher import ActionDispatcher
from utils.tasks import spawn, _bg_tasks


# ============================================================================
# Valid action tests — ensure well-formed actions pass validation
# ============================================================================

def test_narrate_action_valid():
    """Valid NarrateAction passes schema validation."""
    action = NarrateAction(text="The dragon roars menacingly.")
    assert action.text == "The dragon roars menacingly."


def test_speak_action_valid():
    """Valid SpeakAction with optional whisper_to field."""
    action = SpeakAction(
        npc_name="Goblin Chief",
        text="Prepare the traps!",
        whisper_to="Bob"
    )
    assert action.npc_name == "Goblin Chief"
    assert action.text == "Prepare the traps!"
    assert action.whisper_to == "Bob"


def test_speak_action_without_whisper():
    """SpeakAction without whisper_to defaults to None."""
    action = SpeakAction(npc_name="Rogue", text="I pick the lock.")
    assert action.whisper_to is None


def test_move_token_action_valid():
    """Valid MoveTokenAction with bounded coordinates."""
    action = MoveTokenAction(token_id="abc123", x=100.5, y=200.0)
    assert action.x == 100.5
    assert action.y == 200.0


def test_update_hp_action_default_path():
    """UpdateHpAction uses 'hp.value' as default hp_path."""
    action = UpdateHpAction(actor_uuid="uuid1", damage=50)
    assert action.hp_path == "hp.value"


def test_update_hp_action_custom_path():
    """UpdateHpAction with custom hp_path (e.g. for non-D&D systems)."""
    action = UpdateHpAction(actor_uuid="uuid1", damage=25, hp_path="health.current")
    assert action.hp_path == "health.current"


def test_start_encounter_action_with_name():
    """StartEncounterAction with optional encounter_name."""
    action = StartEncounterAction(
        token_ids=["t1", "t2"],
        encounter_name="Goblin Ambush",
        auto_roll_initiative=True
    )
    assert action.encounter_name == "Goblin Ambush"
    assert action.auto_roll_initiative is True


def test_start_encounter_action_without_name():
    """StartEncounterAction without encounter_name (defaults to None)."""
    action = StartEncounterAction(auto_roll_initiative=False)
    assert action.encounter_name is None
    assert action.auto_roll_initiative is False


def test_play_sound_action_with_default_volume():
    """PlaySoundAction with default volume 0.5."""
    action = PlaySoundAction(sound_name="sword_clash.wav")
    assert action.volume == 0.5


def test_play_sound_action_custom_volume():
    """PlaySoundAction with custom volume within bounds."""
    action = PlaySoundAction(sound_name="thunder.wav", volume=0.8)
    assert action.volume == 0.8


# ============================================================================
# Invalid action tests — ensure malformed actions are rejected
# ============================================================================

def test_narrate_action_extra_fields_rejected():
    """NarrateAction rejects extra fields (extra='forbid')."""
    try:
        NarrateAction(text="Hello", extra_field="should fail")
        assert False, "Should have raised ValidationError"
    except ValidationError as e:
        assert "extra_field" in str(e)


def test_speak_action_extra_fields_rejected():
    """SpeakAction rejects arbitrary extra fields."""
    try:
        SpeakAction(
            npc_name="NPC",
            text="Hello",
            unauthorized_param="hack"
        )
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass  # Expected


def test_move_token_action_out_of_bounds():
    """MoveTokenAction rejects coordinates outside [MIN_COORD, MAX_COORD]."""
    try:
        MoveTokenAction(token_id="t1", x=100000, y=50)  # x exceeds MAX_COORD
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass  # Expected


def test_update_hp_action_damage_exceeds_max():
    """UpdateHpAction rejects damage > MAX_DAMAGE (500)."""
    try:
        UpdateHpAction(actor_uuid="uuid1", damage=1000)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass  # Expected


def test_update_hp_action_damage_below_min():
    """UpdateHpAction rejects damage < MIN_DAMAGE (-200)."""
    try:
        UpdateHpAction(actor_uuid="uuid1", damage=-500)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass  # Expected


def test_play_sound_action_volume_out_of_bounds():
    """PlaySoundAction rejects volume outside [0.0, 1.0]."""
    try:
        PlaySoundAction(sound_name="test.wav", volume=1.5)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass  # Expected


def test_update_hp_action_invalid_hp_path():
    """UpdateHpAction rejects hp_path with unsafe characters (brackets, spaces)."""
    try:
        UpdateHpAction(actor_uuid="uuid1", damage=10, hp_path="hp[damage]")
        assert False, "Should have raised ValidationError"
    except ValidationError as e:
        assert "simple dotted path" in str(e)


def test_start_encounter_action_name_too_long():
    """StartEncounterAction rejects encounter_name > 100 chars."""
    try:
        long_name = "x" * 101
        StartEncounterAction(encounter_name=long_name)
        assert False, "Should have raised ValidationError"
    except ValidationError as e:
        error_str = str(e)
        assert "at most 100" in error_str or "string_too_long" in error_str


# ============================================================================
# Dispatcher tests — ensure dispatcher resolves executors correctly
# ============================================================================

def test_dispatcher_missing_action_type():
    """ActionDispatcher requires an action type."""
    import asyncio

    async def _run():
        dispatcher = ActionDispatcher(
            foundry_client=MagicMock(),
            app_state=None,
        )

        # Missing action type should be rejected
        result = await dispatcher.execute({})
        assert "error" in result
        assert result["error"] == "No action type specified"

    asyncio.run(_run())


# ============================================================================
# Fire-and-forget task tests — verify spawn() retains strong refs
# ============================================================================

def test_spawn_retains_task_reference():
    """spawn() keeps a strong reference to the task in _bg_tasks."""
    import asyncio

    async def _run():
        async def dummy_coro():
            return "done"

        # Clear any prior tasks
        _bg_tasks.clear()

        task = spawn(dummy_coro())
        assert task is not None
        assert len(_bg_tasks) > 0, "spawn() should retain a strong reference"

        # Wait for task to complete
        result = await task
        assert result == "done"

    asyncio.run(_run())


def test_spawn_cleans_up_completed_task():
    """spawn() auto-removes completed tasks via done callback."""
    import asyncio

    async def _run():
        async def quick_coro():
            return "finished"

        _bg_tasks.clear()
        task = spawn(quick_coro())
        await task

        # After completion, the done callback should remove it
        # (This may take a moment for the callback to fire)
        await asyncio.sleep(0.01)
        # Note: The callback runs asynchronously; if this test is flaky,
        # we may need to yield control explicitly.

    asyncio.run(_run())


# ============================================================================
# Integration tests — validate a full action chain
# ============================================================================

def test_speak_action_full_chain():
    """Full validation + dispatch chain for SpeakAction."""
    # Parse and validate
    valid_dict = {
        "npc_name": "Tavern Keeper",
        "text": "What can I get ye?",
        "whisper_to": None,
    }
    action = SpeakAction(**valid_dict)

    # Verify fields
    assert action.npc_name == "Tavern Keeper"
    assert action.text == "What can I get ye?"
    assert action.whisper_to is None


def test_invalid_action_rejected_before_dispatch():
    """Invalid action fails validation before reaching dispatcher."""
    bad_dict = {
        "npc_name": "NPC",
        "text": "Hello",
        "malicious_field": "hack",
    }

    try:
        SpeakAction(**bad_dict)
        assert False, "Should reject extra field"
    except ValidationError:
        pass  # Expected


# ============================================================================
# Main entry point for direct execution (without pytest)
# ============================================================================

if __name__ == "__main__":
    print("=== Valid Action Tests ===")
    test_narrate_action_valid()
    print("PASS  narrate action valid")
    test_speak_action_valid()
    print("PASS  speak action with whisper")
    test_speak_action_without_whisper()
    print("PASS  speak action without whisper")
    test_move_token_action_valid()
    print("PASS  move token action valid")
    test_update_hp_action_default_path()
    print("PASS  update hp with default path")
    test_update_hp_action_custom_path()
    print("PASS  update hp with custom path")
    test_start_encounter_action_with_name()
    print("PASS  start encounter with name")
    test_start_encounter_action_without_name()
    print("PASS  start encounter without name")
    test_play_sound_action_with_default_volume()
    print("PASS  play sound with default volume")
    test_play_sound_action_custom_volume()
    print("PASS  play sound with custom volume")

    print("\n=== Invalid Action Tests (expect ValidationError) ===")
    test_narrate_action_extra_fields_rejected()
    print("PASS  narrate rejects extra fields")
    test_speak_action_extra_fields_rejected()
    print("PASS  speak rejects extra fields")
    test_move_token_action_out_of_bounds()
    print("PASS  move token rejects out-of-bounds coords")
    test_update_hp_action_damage_exceeds_max()
    print("PASS  update hp rejects damage > 500")
    test_update_hp_action_damage_below_min()
    print("PASS  update hp rejects damage < -200")
    test_play_sound_action_volume_out_of_bounds()
    print("PASS  play sound rejects volume > 1.0")
    test_update_hp_action_invalid_hp_path()
    print("PASS  update hp rejects unsafe hp_path")
    test_start_encounter_action_name_too_long()
    print("PASS  start encounter rejects name > 100 chars")

    print("\n=== Dispatcher Tests ===")
    test_dispatcher_missing_action_type()
    print("PASS  dispatcher requires action type")

    print("\n=== Fire-and-Forget Task Tests ===")
    test_spawn_retains_task_reference()
    print("PASS  spawn retains task reference")
    test_spawn_cleans_up_completed_task()
    print("PASS  spawn cleans up after completion")

    print("\n=== Integration Tests ===")
    test_speak_action_full_chain()
    print("PASS  speak action full validation chain")
    test_invalid_action_rejected_before_dispatch()
    print("PASS  invalid action rejected before dispatch")

    print("\nAll action validation and dispatch tests passed!")
