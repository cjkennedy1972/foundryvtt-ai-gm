#!/usr/bin/env python3
"""ParticleManager (immersion/particles.py): effect creation, presets, removal.

Asserts actual returned payloads and manager state (active_effects,
effect_history truncation, removal-by-location matching), not just that a
call returns without raising.

Run:
    cd ai-engine && python -m pytest tests/test_immersion_particles.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from immersion.particles import ParticleManager


# ── create_effect ────────────────────────────────────────────────────────

def test_create_effect_stores_it_and_returns_its_visual_payload():
    mgr = ParticleManager()
    result = mgr.create_effect(
        "fx-1", "Fireball", "spell", 10, 20,
        color="#ff0000", duration=500, intensity=0.9, size="large",
    )

    assert result == {
        "type": "particle_effect_created",
        "effect_id": "fx-1",
        "name": "Fireball",
        "position": (10, 20),
        "visual": {
            "color": "#ff0000",
            "size": "large",
            "intensity": 0.9,
            "duration_ms": 500,
        },
    }
    assert "fx-1" in mgr.active_effects
    assert mgr.active_effects["fx-1"].type == "spell"


def test_create_effect_uses_its_documented_defaults():
    mgr = ParticleManager()
    result = mgr.create_effect("fx-1", "Glow", "environmental", 0, 0)

    assert result["visual"] == {
        "color": "#ffffff",
        "size": "medium",
        "intensity": 0.7,
        "duration_ms": None,
    }


def test_create_effect_appends_to_history():
    mgr = ParticleManager()
    mgr.create_effect("fx-1", "Glow", "environmental", 0, 0)
    mgr.create_effect("fx-2", "Spark", "impact", 1, 1)

    assert [h["effect_id"] for h in mgr.effect_history] == ["fx-1", "fx-2"]


def test_effect_history_is_capped_at_max_history():
    mgr = ParticleManager()
    mgr.max_history = 3
    for i in range(5):
        mgr.create_effect(f"fx-{i}", "Spark", "impact", 0, 0)

    assert len(mgr.effect_history) == 3
    assert [h["effect_id"] for h in mgr.effect_history] == ["fx-2", "fx-3", "fx-4"]


# ── create_effect_from_preset ────────────────────────────────────────────

def test_create_effect_from_preset_applies_the_presets_visuals():
    mgr = ParticleManager()
    result = mgr.create_effect_from_preset("fx-1", "fireball", 5, 5)

    assert result["visual"] == {
        "color": "#ff4500",
        "size": "large",
        "intensity": 0.8,
        "duration_ms": 1000,
    }
    assert result["name"] == "fireball"
    assert mgr.active_effects["fx-1"].type == "spell"


def test_create_effect_from_preset_returns_an_error_for_an_unknown_preset():
    mgr = ParticleManager()
    result = mgr.create_effect_from_preset("fx-1", "not-a-preset", 0, 0)
    assert result == {"error": "Unknown particle preset: not-a-preset"}
    assert mgr.active_effects == {}


# ── remove_effect ────────────────────────────────────────────────────────

def test_remove_effect_deletes_it_and_returns_confirmation():
    mgr = ParticleManager()
    mgr.create_effect("fx-1", "Fireball", "spell", 0, 0)

    result = mgr.remove_effect("fx-1")

    assert result == {
        "type": "particle_effect_removed",
        "effect_id": "fx-1",
        "name": "Fireball",
    }
    assert "fx-1" not in mgr.active_effects


def test_remove_effect_returns_an_error_for_an_unknown_effect():
    mgr = ParticleManager()
    assert mgr.remove_effect("ghost") == {"error": "Effect not found: ghost"}


# ── remove_effects_at_location ───────────────────────────────────────────

def test_remove_effects_at_location_removes_only_effects_within_radius():
    mgr = ParticleManager()
    mgr.create_effect("near", "Near", "impact", 10, 10)
    mgr.create_effect("far", "Far", "impact", 100, 100)

    result = mgr.remove_effects_at_location(12, 12, radius=5)

    assert result["effects_removed"] == 1
    assert "near" not in mgr.active_effects
    assert "far" in mgr.active_effects


def test_remove_effects_at_location_reports_zero_when_nothing_is_in_range():
    mgr = ParticleManager()
    mgr.create_effect("far", "Far", "impact", 100, 100)

    result = mgr.remove_effects_at_location(0, 0, radius=5)

    assert result == {
        "type": "particle_effects_cleared",
        "location": (0, 0),
        "radius": 5,
        "effects_removed": 0,
    }
    assert "far" in mgr.active_effects


# ── get_active_effects / get_effect_count ───────────────────────────────

def test_get_active_effects_reflects_current_state_as_plain_dicts():
    mgr = ParticleManager()
    mgr.create_effect("fx-1", "Fireball", "spell", 3, 4, color="#ff0000", intensity=0.9)

    effects = mgr.get_active_effects()

    assert effects == {
        "fx-1": {
            "name": "Fireball",
            "type": "spell",
            "position": (3, 4),
            "color": "#ff0000",
            "intensity": 0.9,
            "size": "medium",
            "duration_ms": None,
        }
    }


def test_get_effect_count_tracks_additions_and_removals():
    mgr = ParticleManager()
    assert mgr.get_effect_count() == 0

    mgr.create_effect("fx-1", "Fireball", "spell", 0, 0)
    mgr.create_effect("fx-2", "Spark", "impact", 0, 0)
    assert mgr.get_effect_count() == 2

    mgr.remove_effect("fx-1")
    assert mgr.get_effect_count() == 1


# ── presets ───────────────────────────────────────────────────────────────

def test_list_presets_returns_the_full_real_preset_table():
    presets = ParticleManager().list_presets()
    assert set(presets) == {
        "fireball", "ice_shards", "healing_light", "lightning",
        "poison_cloud", "blood_spray", "smoke", "sparkles",
    }
    assert presets["lightning"] == {
        "color": "#ffff00", "size": "small", "duration": 500, "intensity": 1.0,
    }
