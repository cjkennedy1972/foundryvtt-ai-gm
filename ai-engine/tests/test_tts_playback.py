"""Checks for tts/playback.py — moved out of actions/executors.py (Phase 5)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tts import playback


def test_is_active_reflects_configured_engine():
    playback.configure(None, None, engine="server")
    assert playback.is_active() is False  # no service, not browser

    playback.configure(object(), None, engine="server")
    assert playback.is_active() is True  # service present

    playback.configure(None, None, engine="browser")
    assert playback.is_active() is True  # browser engine needs no service

    playback.configure(None, None, engine="server")  # reset for other tests


def test_browser_payload_maps_known_voice():
    payload = playback._browser_payload("Hello", "onyx")
    assert payload["gender"] == "male"
    assert payload["rate"] == 0.95
    assert payload["text"] == "Hello"


def test_browser_payload_falls_back_for_unknown_voice():
    payload = playback._browser_payload("Hi", "not-a-real-voice")
    assert payload["gender"] == "male"
    assert payload["rate"] == 1.0
    assert payload["pitch"] == 1.0


def test_get_npc_record_without_registry_returns_none():
    playback.configure(None, None, engine="server")
    assert playback.get_npc_record("Elara") is None
