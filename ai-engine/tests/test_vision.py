"""Tests for immersion.vision — vision ranges, light sources, visibility, and
fog-of-war math."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from immersion.vision import VisionManager


def test_set_vision_range():
    vm = VisionManager()
    out = vm.set_vision_range("t1", 30)
    assert out["vision_range_feet"] == 30
    assert vm.vision_ranges["t1"] == 30


def test_light_source_add_and_remove():
    vm = VisionManager()
    added = vm.apply_light_source("t1", 20, color="#ff0000", intensity=0.5)
    assert added["light"]["radius_feet"] == 20
    assert "t1" in vm.light_sources
    removed = vm.remove_light_source("t1")
    assert removed["type"] == "light_source_removed"
    assert "t1" not in vm.light_sources
    # Removing a non-existent light is a no-op, not an error.
    vm.remove_light_source("ghost")


def test_visibility_within_default_vision():
    vm = VisionManager()
    out = vm.calculate_visibility("obs", "tgt", 40)  # default 60ft vision
    assert out["visible"] is True


def test_visibility_beyond_default_vision():
    vm = VisionManager()
    out = vm.calculate_visibility("obs", "tgt", 80)
    assert out["visible"] is False
    assert "Beyond vision range" in out["reason"]


def test_visibility_uses_light_radius_when_present():
    vm = VisionManager()
    vm.apply_light_source("tgt", 15)
    inside = vm.calculate_visibility("obs", "tgt", 10)
    assert inside["visible"] is True and "light source radius" in inside["reason"]
    outside = vm.calculate_visibility("obs", "tgt", 25)
    assert outside["visible"] is False


def test_visibility_darkness_without_light_blocks():
    vm = VisionManager()
    vm.set_darkness(True)
    out = vm.calculate_visibility("obs", "tgt", 5)
    assert out["visible"] is False and "Darkness" in out["reason"]


def test_fog_of_war_partitions_visible_and_hidden():
    vm = VisionManager()
    vm.set_vision_range("p1", 30)
    positions = {
        "p1": (0.0, 0.0),
        "near": (10.0, 0.0),    # within 30ft
        "far": (100.0, 0.0),    # outside
    }
    out = vm.update_fog_of_war(["p1"], positions)
    assert "near" in out["visible_tokens"]
    assert "far" in out["hidden_tokens"]
    # player token itself is neither hidden nor double-counted as an enemy
    assert "p1" not in out["hidden_tokens"]


def test_fog_of_war_skips_player_without_position():
    vm = VisionManager()
    out = vm.update_fog_of_war(["ghost"], {"other": (0.0, 0.0)})
    assert out["visible_tokens"] == []


def test_fog_of_war_light_extends_vision():
    vm = VisionManager()
    vm.set_vision_range("p1", 5)
    vm.apply_light_source("p1", 50)  # light beats the short vision range
    out = vm.update_fog_of_war(["p1"], {"p1": (0.0, 0.0), "t": (40.0, 0.0)})
    assert "t" in out["visible_tokens"]


def test_vision_status_reports_averages():
    vm = VisionManager()
    assert vm.get_vision_status()["average_vision_range"] == 0
    vm.set_vision_range("a", 30)
    vm.set_vision_range("b", 60)
    status = vm.get_vision_status()
    assert status["average_vision_range"] == 45
    assert status["tokens_with_vision"] == 2
