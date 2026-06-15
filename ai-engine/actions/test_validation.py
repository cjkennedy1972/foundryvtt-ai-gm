#!/usr/bin/env python3
"""
Tests for action schema validation and dispatcher hardening.

These cover the remediation for GitHub issue #35:
- Extra/misnamed LLM fields are rejected
- Numeric fields are bounded (damage clamping)
- Handler non-dict returns are handled
- Pydantic validation is in place before dispatch
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from actions.schemas import (
    ACTION_SCHEMAS,
    NarrateAction,
    SpeakAction,
    RollAction,
    MoveTokenAction,
    UpdateHpAction,
    PlaySoundAction,
    SwitchSceneAction,
    StartEncounterAction,
    EndEncounterAction,
    PromptPlayerAction,
    MIN_DAMAGE,
    MAX_DAMAGE,
)
from actions.dispatcher import _validate_action, _clamp_damage


# ======================================================================
# Schema validation tests
# ======================================================================

def test_extra_field_rejected():
    """Extra keys from LLM should cause ValidationError, not slip through."""
    errors = 0
    for name, schema_cls in ACTION_SCHEMAS.items():
        try:
            # Build minimal valid payload, then add a junk key
            kwargs = {f: "test" for f in schema_cls.model_fields}
            kwargs["gibberish"] = "should be rejected"
            schema_cls(**kwargs)
            print(f"  FAIL: {name} accepted extra field 'gibberish'")
            errors += 1
        except Exception:
            pass  # Expected — ValidationError or similar
    return errors


def test_missing_required_field():
    """Omitted required fields should cause ValidationError.

    Schemas with no required fields (e.g. EndEncounterAction) are
    intentionally allowed to accept an empty input.
    """
    # Schemas that are designed to work with no input.
    empty_ok = {"end_encounter"}
    errors = 0
    for name, schema_cls in ACTION_SCHEMAS.items():
        if name in empty_ok:
            print(f"  ✓ {name}: empty input OK (no required fields)")
            continue
        try:
            schema_cls()
            print(f"  FAIL: {name} accepted empty input")
            errors += 1
        except Exception:
            pass  # Expected
    return errors


def test_valid_payload():
    """Valid payloads should pass validation."""
    errors = 0
    valid_examples = {
        "narrate": NarrateAction(text="Hello, world!"),
        "speak": SpeakAction(npc_name="Goblin", text="I will fight you!"),
        "roll": RollAction(formula="2d6+3", speaker="Hero"),
        "move_token": MoveTokenAction(token_id="abc", x=100.0, y=200.0),
        "update_hp": UpdateHpAction(actor_uuid="abc", damage=10),
        "play_sound": PlaySoundAction(sound_name="sword_clash"),
        "switch_scene": SwitchSceneAction(scene_name="battlefield"),
        "start_encounter": StartEncounterAction(token_ids=["a", "b"]),
        "end_encounter": EndEncounterAction(),
        "prompt_player": PromptPlayerAction(player_id="user123", question="Do you attack?"),
    }
    for name, instance in valid_examples.items():
        print(f"  ✓ {name}: {instance}")
    return errors


# ======================================================================
# Damage clamping tests
# ======================================================================

def test_damage_clamping():
    """Damage values outside [MIN_DAMAGE, MAX_DAMAGE] should be clamped."""
    errors = 0

    # Above max
    val, reason = _clamp_damage(99999)
    if val != MAX_DAMAGE:
        print(f"  FAIL: damage=99999 -> {val}, expected {MAX_DAMAGE}")
        errors += 1
    else:
        print(f"  ✓ damage=99999 clamped to {MAX_DAMAGE}: {reason}")

    # Below min
    val, reason = _clamp_damage(-99999)
    if val != MIN_DAMAGE:
        print(f"  FAIL: damage=-99999 -> {val}, expected {MIN_DAMAGE}")
        errors += 1
    else:
        print(f"  ✓ damage=-99999 clamped to {MIN_DAMAGE}: {reason}")

    # Within bounds — no clamp
    val, reason = _clamp_damage(15)
    if val != 15:
        print(f"  FAIL: damage=15 -> {val}, expected 15")
        errors += 1
    elif reason is not None:
        print(f"  FAIL: damage=15 was clamped: {reason}")
        errors += 1
    else:
        print(f"  ✓ damage=15 passed through unchanged")

    # Boundary values
    val, _ = _clamp_damage(MIN_DAMAGE)
    if val != MIN_DAMAGE:
        print(f"  FAIL: damage={MIN_DAMAGE} changed to {val}")
        errors += 1
    else:
        print(f"  ✓ damage={MIN_DAMAGE} at lower boundary — unchanged")

    val, _ = _clamp_damage(MAX_DAMAGE)
    if val != MAX_DAMAGE:
        print(f"  FAIL: damage={MAX_DAMAGE} changed to {val}")
        errors += 1
    else:
        print(f"  ✓ damage={MAX_DAMAGE} at upper boundary — unchanged")

    return errors


# ======================================================================
# Dispatcher _validate_action tests
# ======================================================================

def test_validate_action_rejects_unknown_type():
    """Unknown action types should fail validation."""
    kwargs, err = _validate_action("foobar", {"x": 1})
    if err is None:
        print("  FAIL: unknown type 'foobar' was not rejected")
        return 1
    print(f"  ✓ unknown type rejected: {err}")
    return 0


def test_validate_action_accepts_valid():
    """A well-formed action dict should validate."""
    kwargs, err = _validate_action("narrate", {"text": "Hello"})
    if err:
        print(f"  FAIL: valid action rejected: {err}")
        return 1
    if kwargs.get("text") != "Hello":
        print(f"  FAIL: validated text != 'Hello': {kwargs}")
        return 1
    print(f"  ✓ valid action accepted: {kwargs}")
    return 0


def test_validate_action_rejects_extra_fields():
    """Extra LLM fields should be rejected."""
    kwargs, err = _validate_action("narrate", {"text": "Hello", "fake_field": 42})
    if err is None:
        print("  FAIL: extra field 'fake_field' was not rejected")
        return 1
    print(f"  ✓ extra field rejected: {err}")
    return 0


# ======================================================================
# HP path sanitization test
# ======================================================================

def test_hp_path_sanitization():
    """HP paths with brackets or special chars should be rejected."""
    errors = 0

    # Valid path
    try:
        UpdateHpAction(actor_uuid="x", damage=5, hp_path="data.attributes.hp.value")
        print("  ✓ valid hp_path accepted")
    except Exception as e:
        print(f"  FAIL: valid hp_path rejected: {e}")
        errors += 1

    # Invalid path with brackets
    try:
        UpdateHpAction(actor_uuid="x", damage=5, hp_path="data[0].hp.value")
        print("  FAIL: hp_path with brackets was accepted")
        errors += 1
    except Exception:
        print("  ✓ hp_path with brackets rejected")

    # Invalid path with dots-and-spaces
    try:
        UpdateHpAction(actor_uuid="x", damage=5, hp_path="my .hp")
        print("  FAIL: hp_path with spaces was accepted")
        errors += 1
    except Exception:
        print("  ✓ hp_path with spaces rejected")

    return errors


# ======================================================================
# Summary
# ======================================================================

def run_all():
    tests = [
        ("Extra field rejection", test_extra_field_rejected),
        ("Missing required field", test_missing_required_field),
        ("Valid payload acceptance", test_valid_payload),
        ("Damage clamping", test_damage_clamping),
        ("Unknown type rejection", test_validate_action_rejects_unknown_type),
        ("Valid action acceptance", test_validate_action_accepts_valid),
        ("Extra fields in action", test_validate_action_rejects_extra_fields),
        ("HP path sanitization", test_hp_path_sanitization),
    ]

    print("=" * 60)
    print("ACTION VALIDATION TESTS (GitHub #35 remediation)")
    print("=" * 60)

    total_errors = 0
    for name, fn in tests:
        print(f"\n--- {name} ---")
        errors = fn()
        total_errors += errors
        status = "FAIL" if errors else "PASS"
        print(f"  [{status}] {errors} error(s)")

    print("\n" + "=" * 60)
    if total_errors == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"TOTAL: {total_errors} error(s)")
    print("=" * 60)

    return total_errors


if __name__ == "__main__":
    sys.exit(run_all())
