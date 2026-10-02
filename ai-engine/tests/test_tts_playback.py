"""Checks for tts/playback.py — moved out of actions/executors.py (Phase 5)."""

import asyncio
import sys
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tts import playback


def _write_wav(path: Path, *, framerate: int = 16000, nframes: int = 8000) -> None:
    """Write a minimal valid WAV file with a known, computable duration."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * nframes)


@pytest.fixture(autouse=True)
def _reset_playback_state():
    """Every test starts from a clean module-level state and leaves it clean."""
    playback.configure(None, None, engine="server")
    playback._active_playback_task = None
    playback._chat_listener = None
    yield
    playback.configure(None, None, engine="server")
    playback._active_playback_task = None
    playback._chat_listener = None


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


def test_get_npc_record_delegates_to_registry():
    registry = MagicMock()
    registry.get_npc_by_name.return_value = {"name": "Elara", "voice": "nova"}
    playback.configure(None, registry, engine="server")

    record = playback.get_npc_record("Elara")

    registry.get_npc_by_name.assert_called_once_with("Elara")
    assert record == {"name": "Elara", "voice": "nova"}


# ---------------------------------------------------------------------------
# _split_sentences
# ---------------------------------------------------------------------------

def test_split_sentences_empty_text_returns_empty_list():
    assert playback._split_sentences("") == []


def test_split_sentences_splits_on_terminal_punctuation():
    text = "The door creaks open. A cold wind blows! Who goes there?"
    assert playback._split_sentences(text) == [
        "The door creaks open.",
        "A cold wind blows!",
        "Who goes there?",
    ]


def test_split_sentences_single_sentence_has_no_trailing_period_split():
    assert playback._split_sentences("Just one sentence here") == [
        "Just one sentence here"
    ]


def test_split_sentences_does_not_split_on_lowercase_continuation():
    # Heuristic only splits [.!?] followed by whitespace + a *capital* letter,
    # so "e.g." style abbreviations mid-sentence don't fragment the text.
    text = "Bring potions, e.g. healing draughts, before you go."
    assert playback._split_sentences(text) == [
        "Bring potions, e.g. healing draughts, before you go."
    ]


# ---------------------------------------------------------------------------
# _wav_duration / _duration_from_url
# ---------------------------------------------------------------------------

def test_wav_duration_computes_from_frames_and_framerate(tmp_path):
    wav_path = tmp_path / "clip.wav"
    _write_wav(wav_path, framerate=16000, nframes=8000)

    assert playback._wav_duration(wav_path) == pytest.approx(0.5)


def test_wav_duration_returns_fallback_for_missing_file(tmp_path):
    assert playback._wav_duration(tmp_path / "does-not-exist.wav") == 3.0


def test_duration_from_url_without_service_returns_fallback():
    playback.configure(None, None, engine="server")
    assert playback._duration_from_url("http://host/audio/clip.wav") == 3.0


def test_duration_from_url_resolves_filename_against_audio_dir(tmp_path):
    wav_path = tmp_path / "clip.wav"
    _write_wav(wav_path, framerate=8000, nframes=4000)
    service = MagicMock()
    service.audio_dir = str(tmp_path)
    playback.configure(service, None, engine="server")

    duration = playback._duration_from_url("http://host/audio/clip.wav")

    assert duration == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# stop_playback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_playback_cancels_active_task_and_clears_it():
    async def never_ending():
        await asyncio.sleep(100)

    task = asyncio.create_task(never_ending())
    await asyncio.sleep(0)
    playback._active_playback_task = task

    await playback.stop_playback(None)

    assert task.cancelled()
    assert playback._active_playback_task is None


@pytest.mark.asyncio
async def test_stop_playback_broadcasts_stop_via_foundry():
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": {"ok": True}})

    await playback.stop_playback(foundry)

    foundry.execute_js.assert_awaited_once()
    js_sent = foundry.execute_js.await_args.args[0]
    assert "stopAll" in js_sent


@pytest.mark.asyncio
async def test_stop_playback_logs_when_module_reports_not_ok(caplog):
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(
        return_value={"result": {"ok": False, "error": "aigm-tts module not active"}}
    )

    with caplog.at_level("WARNING"):
        await playback.stop_playback(foundry)

    assert "aigm-tts module not active" in caplog.text


@pytest.mark.asyncio
async def test_stop_playback_survives_foundry_exception():
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(side_effect=RuntimeError("socket closed"))

    # Must not raise — broadcast failures are logged, not propagated.
    await playback.stop_playback(foundry)

    foundry.execute_js.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_playback_without_foundry_client_does_not_raise():
    playback._active_playback_task = asyncio.current_task()

    await playback.stop_playback(None)

    # Still clears the active-task bookkeeping even with no foundry client.
    assert playback._active_playback_task is None


# ---------------------------------------------------------------------------
# _play_browser / _play_tts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_play_browser_sends_speakall_payload_for_text_and_voice():
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": {"ok": True}})
    playback._tts_volume = 0.5

    await playback._play_browser("Hello there", "nova", foundry)

    js_sent = foundry.execute_js.await_args.args[0]
    assert "speakAll" in js_sent
    assert "Hello there" in js_sent
    assert '"gender": "female"' in js_sent


@pytest.mark.asyncio
async def test_play_browser_logs_but_does_not_raise_when_module_missing(caplog):
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(
        return_value={"result": {"ok": False, "error": "aigm-tts module not active"}}
    )

    with caplog.at_level("WARNING"):
        await playback._play_browser("Hi", "echo", foundry)

    assert "aigm-tts module not active" in caplog.text


@pytest.mark.asyncio
async def test_play_browser_survives_execute_js_exception():
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(side_effect=RuntimeError("boom"))

    await playback._play_browser("Hi", "echo", foundry)  # must not raise

    foundry.execute_js.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_tts_sends_audiohelper_play_call():
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": {"ok": True}})

    await playback._play_tts("http://host/audio/clip.wav", foundry)

    js_sent = foundry.execute_js.await_args.args[0]
    assert "AH.play" in js_sent
    assert "http://host/audio/clip.wav" in js_sent


@pytest.mark.asyncio
async def test_play_tts_survives_execute_js_exception():
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(side_effect=RuntimeError("boom"))

    await playback._play_tts("http://host/audio/clip.wav", foundry)  # must not raise

    foundry.execute_js.assert_awaited_once()


# ---------------------------------------------------------------------------
# narrate() — server engine
# ---------------------------------------------------------------------------

@pytest.fixture
def instant_sleep(monkeypatch):
    """Replace asyncio.sleep with a near-instant yield so timing-driven
    playback loops run at test speed instead of real wall-clock seconds."""
    real_sleep = asyncio.sleep

    async def fake(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake)
    return fake


@pytest.mark.asyncio
async def test_narrate_server_plays_each_sentence_in_order(instant_sleep, monkeypatch):
    service = MagicMock()
    service.narrate = AsyncMock(side_effect=["http://host/a.wav", "http://host/b.wav"])
    playback.configure(service, None, engine="server")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": {"ok": True}})
    monkeypatch.setattr(playback, "_duration_from_url", lambda url: 0.0)

    await playback.narrate("First sentence. Second sentence.", foundry)

    assert [call.args[0] for call in service.narrate.await_args_list] == [
        "First sentence.",
        "Second sentence.",
    ]
    played_urls = [call.args[0] for call in foundry.execute_js.await_args_list]
    assert any("a.wav" in js for js in played_urls)
    assert any("b.wav" in js for js in played_urls)


@pytest.mark.asyncio
async def test_narrate_server_skips_playback_when_sentence_has_no_audio_url(instant_sleep):
    service = MagicMock()
    service.narrate = AsyncMock(return_value=None)
    playback.configure(service, None, engine="server")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": {"ok": True}})

    await playback.narrate("Only sentence here.", foundry)

    foundry.execute_js.assert_not_awaited()


@pytest.mark.asyncio
async def test_narrate_server_continues_after_one_sentence_raises(instant_sleep, monkeypatch):
    service = MagicMock()
    service.narrate = AsyncMock(side_effect=[RuntimeError("tts down"), "http://host/b.wav"])
    playback.configure(service, None, engine="server")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": {"ok": True}})
    monkeypatch.setattr(playback, "_duration_from_url", lambda url: 0.0)

    await playback.narrate("Bad sentence. Good sentence.", foundry)

    # First sentence failed but the second still got synthesized and played.
    assert service.narrate.await_count == 2
    foundry.execute_js.assert_awaited_once()


@pytest.mark.asyncio
async def test_narrate_empty_text_skips_synthesis_entirely(instant_sleep):
    service = MagicMock()
    service.narrate = AsyncMock()
    playback.configure(service, None, engine="server")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock()

    await playback.narrate("", foundry)

    service.narrate.assert_not_awaited()


@pytest.mark.asyncio
async def test_narrate_resets_idle_timer_after_playback_completes(instant_sleep):
    service = MagicMock()
    service.narrate = AsyncMock(return_value=None)
    playback.configure(service, None, engine="server")
    listener = MagicMock()
    playback.set_chat_listener(listener)
    foundry = MagicMock()
    foundry.execute_js = AsyncMock()

    await playback.narrate("Hello there.", foundry)

    listener._reset_idle_timer.assert_called_once_with(_escalate=True)


@pytest.mark.asyncio
async def test_narrate_cancels_previous_in_flight_narration():
    service = MagicMock()
    release_first = asyncio.Event()
    first_cancelled = asyncio.Event()

    async def slow_narrate(_sentence):
        release_first.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            first_cancelled.set()
            raise
        return None

    service.narrate = AsyncMock(side_effect=slow_narrate)
    playback.configure(service, None, engine="server")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock()

    first_task = asyncio.create_task(playback.narrate("First one.", foundry))
    await release_first.wait()

    second_service = MagicMock()
    second_service.narrate = AsyncMock(return_value=None)
    playback.configure(second_service, None, engine="server")

    await playback.narrate("Second one.", foundry)
    await first_task

    assert first_cancelled.is_set()
    second_service.narrate.assert_awaited_once()


# ---------------------------------------------------------------------------
# narrate() — browser engine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_browser_speaks_each_sentence_with_narrator_voice(instant_sleep, monkeypatch):
    from config import settings

    playback.configure(None, None, engine="browser")
    play_browser = AsyncMock()
    monkeypatch.setattr(playback, "_play_browser", play_browser)
    foundry = MagicMock()

    await playback.narrate("The quest begins. Good luck.", foundry)

    assert [call.args[0] for call in play_browser.await_args_list] == [
        "The quest begins.",
        "Good luck.",
    ]
    assert all(call.args[1] == settings.tts_narrator_voice for call in play_browser.await_args_list)


@pytest.mark.asyncio
async def test_narrate_browser_empty_text_does_not_call_foundry(instant_sleep):
    playback.configure(None, None, engine="browser")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock()

    await playback.narrate("", foundry)

    foundry.execute_js.assert_not_awaited()


# ---------------------------------------------------------------------------
# speak() — server + browser engine, NPC voice assignment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_speak_server_uses_npc_name_and_record_for_synthesis(instant_sleep):
    service = MagicMock()
    service.speak = AsyncMock(return_value=None)
    playback.configure(service, None, engine="server")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock()
    npc_record = {"name": "Elara", "voice": "nova"}

    await playback.speak("Welcome, traveler.", "Elara", npc_record, foundry)

    service.speak.assert_awaited_once_with("Welcome, traveler.", "Elara", npc_record)


@pytest.mark.asyncio
async def test_speak_empty_text_skips_synthesis(instant_sleep):
    service = MagicMock()
    service.speak = AsyncMock()
    playback.configure(service, None, engine="server")
    foundry = MagicMock()

    await playback.speak("", "Elara", None, foundry)

    service.speak.assert_not_awaited()


@pytest.mark.asyncio
async def test_speak_browser_assigns_voice_via_voice_assigner(instant_sleep, monkeypatch):
    assigner = MagicMock()
    assigner.get_voice.return_value = "shimmer"
    playback.configure(None, None, engine="browser")
    playback._voice_assigner = assigner
    play_browser = AsyncMock()
    monkeypatch.setattr(playback, "_play_browser", play_browser)
    foundry = MagicMock()

    await playback.speak("Hee hee, caught you!", "Pip", {"name": "Pip"}, foundry)

    assigner.get_voice.assert_called_once_with("Pip", {"name": "Pip"})
    assert play_browser.await_args_list[0].args == ("Hee hee, caught you!", "shimmer", foundry)


@pytest.mark.asyncio
async def test_speak_browser_falls_back_to_echo_without_voice_assigner(instant_sleep, monkeypatch):
    playback.configure(None, None, engine="browser")
    playback._voice_assigner = None
    play_browser = AsyncMock()
    monkeypatch.setattr(playback, "_play_browser", play_browser)
    foundry = MagicMock()

    await playback.speak("Hello.", "Guard", None, foundry)

    assert play_browser.await_args_list[0].args == ("Hello.", "echo", foundry)


@pytest.mark.asyncio
async def test_speak_continues_after_one_sentence_raises(instant_sleep, monkeypatch):
    service = MagicMock()
    service.speak = AsyncMock(side_effect=[RuntimeError("tts down"), "http://host/b.wav"])
    playback.configure(service, None, engine="server")
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": {"ok": True}})
    monkeypatch.setattr(playback, "_duration_from_url", lambda url: 0.0)

    await playback.speak("Bad line. Good line.", "NPC", None, foundry)

    assert service.speak.await_count == 2
    foundry.execute_js.assert_awaited_once()


@pytest.mark.asyncio
async def test_speak_resets_idle_timer_after_completion(instant_sleep):
    service = MagicMock()
    service.speak = AsyncMock(return_value=None)
    playback.configure(service, None, engine="server")
    listener = MagicMock()
    playback.set_chat_listener(listener)
    foundry = MagicMock()

    await playback.speak("Hello.", "NPC", None, foundry)

    listener._reset_idle_timer.assert_called_once_with(_escalate=True)


@pytest.mark.asyncio
async def test_speak_cancels_previous_in_flight_speech():
    release_first = asyncio.Event()
    first_cancelled = asyncio.Event()

    async def slow_speak(_sentence, _name, _record):
        release_first.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            first_cancelled.set()
            raise
        return None

    service = MagicMock()
    service.speak = AsyncMock(side_effect=slow_speak)
    playback.configure(service, None, engine="server")
    foundry = MagicMock()

    first_task = asyncio.create_task(playback.speak("First one.", "NPC", None, foundry))
    await release_first.wait()

    second_service = MagicMock()
    second_service.speak = AsyncMock(return_value=None)
    playback.configure(second_service, None, engine="server")

    await playback.speak("Second one.", "NPC", None, foundry)
    await first_task

    assert first_cancelled.is_set()
    second_service.speak.assert_awaited_once()


# ---------------------------------------------------------------------------
# Remaining edges: barge-in cancellation during playback.py's own wait,
# dispatch-level exception handling, and a few short-circuit branches.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_play_tts_logs_when_module_reports_not_ok(caplog):
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(
        return_value={"result": {"ok": False, "error": "no AudioHelper"}}
    )

    with caplog.at_level("WARNING"):
        await playback._play_tts("http://host/audio/clip.wav", foundry)

    assert "no AudioHelper" in caplog.text


@pytest.mark.asyncio
async def test_narrate_dispatch_logs_on_unexpected_exception(monkeypatch, caplog):
    playback.configure(MagicMock(), None, engine="server")

    async def boom(_text, _foundry):
        raise ValueError("synthesis engine exploded")

    monkeypatch.setattr(playback, "_narrate_server", boom)

    with caplog.at_level("WARNING"):
        await playback.narrate("Hello.", MagicMock())

    assert "synthesis engine exploded" in caplog.text
    assert playback._active_playback_task is None


@pytest.mark.asyncio
async def test_speak_dispatch_logs_on_unexpected_exception(monkeypatch, caplog):
    playback.configure(MagicMock(), None, engine="server")

    async def boom(_text, _name, _record, _foundry):
        raise ValueError("npc voice engine exploded")

    monkeypatch.setattr(playback, "_speak_server", boom)

    with caplog.at_level("WARNING"):
        await playback.speak("Hello.", "NPC", None, MagicMock())

    assert "npc voice engine exploded" in caplog.text
    assert playback._active_playback_task is None


@pytest.mark.asyncio
async def test_narrate_browser_resets_idle_timer_after_playback(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))
    playback.configure(None, None, engine="browser")
    listener = MagicMock()
    playback.set_chat_listener(listener)
    monkeypatch.setattr(playback, "_play_browser", AsyncMock())

    await playback.narrate("Hello there.", MagicMock())

    listener._reset_idle_timer.assert_called_once_with(_escalate=True)


@pytest.mark.asyncio
async def test_speak_browser_resets_idle_timer_after_playback(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))
    playback.configure(None, None, engine="browser")
    playback._voice_assigner = None
    listener = MagicMock()
    playback.set_chat_listener(listener)
    monkeypatch.setattr(playback, "_play_browser", AsyncMock())

    await playback.speak("Hello there.", "NPC", None, MagicMock())

    listener._reset_idle_timer.assert_called_once_with(_escalate=True)


@pytest.mark.asyncio
async def test_speak_browser_empty_text_returns_without_playing():
    playback.configure(None, None, engine="browser")
    playback._voice_assigner = None
    play_browser = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(playback, "_play_browser", play_browser)
        await playback.speak("", "NPC", None, MagicMock())

    play_browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_speak_server_logs_barge_in_measurement_on_first_sentence(instant_sleep, monkeypatch, caplog):
    service = MagicMock()
    service.speak = AsyncMock(return_value="http://host/a.wav")
    playback.configure(service, None, engine="server")
    monkeypatch.setattr(playback, "_duration_from_url", lambda url: 0.0)
    monkeypatch.setattr(playback, "_play_tts", AsyncMock())

    with caplog.at_level("INFO"):
        await playback.speak("Only one sentence here.", "Elara", None, MagicMock())

    assert "Audio began on first sentence for 'Elara'" in caplog.text


@pytest.mark.asyncio
async def test_narrate_server_cancelled_mid_sentence_wait_via_stop_playback(monkeypatch, caplog):
    """Covers the CancelledError re-raise inside the sentence sleep itself
    (not inside the mocked TTS-service call) — the barge-in path triggered by
    a real stop_playback() during the post-playback wait."""
    service = MagicMock()
    service.speak = AsyncMock()
    service.narrate = AsyncMock(return_value="http://host/a.wav")
    playback.configure(service, None, engine="server")
    monkeypatch.setattr(playback, "_duration_from_url", lambda url: 10.0)
    monkeypatch.setattr(playback, "_play_tts", AsyncMock())

    task = asyncio.create_task(playback.narrate("A long sentence plays here.", MagicMock()))
    # Let the task run until it's parked inside the post-playback asyncio.sleep.
    for _ in range(50):
        await asyncio.sleep(0)
        if playback._active_playback_task is task:
            break

    with caplog.at_level("INFO"):
        await playback.stop_playback(None)
        await task

    assert "Narration interrupted at sentence 1" in caplog.text
    assert playback._active_playback_task is None


@pytest.mark.asyncio
async def test_speak_server_cancelled_mid_sentence_wait_via_stop_playback(monkeypatch, caplog):
    service = MagicMock()
    service.speak = AsyncMock(return_value="http://host/a.wav")
    playback.configure(service, None, engine="server")
    monkeypatch.setattr(playback, "_duration_from_url", lambda url: 10.0)
    monkeypatch.setattr(playback, "_play_tts", AsyncMock())

    task = asyncio.create_task(playback.speak("A long line plays here.", "Elara", None, MagicMock()))
    for _ in range(50):
        await asyncio.sleep(0)
        if playback._active_playback_task is task:
            break

    with caplog.at_level("INFO"):
        await playback.stop_playback(None)
        await task

    assert "NPC speech for 'Elara' interrupted at sentence 1" in caplog.text
    assert playback._active_playback_task is None


@pytest.mark.asyncio
async def test_narrate_browser_cancelled_mid_sentence_wait_via_stop_playback(monkeypatch):
    """Covers the CancelledError re-raise inside _narrate_browser's own sleep."""
    playback.configure(None, None, engine="browser")
    monkeypatch.setattr(playback, "_play_browser", AsyncMock())

    task = asyncio.create_task(
        playback.narrate("A reasonably long sentence to keep the sleep alive here.", MagicMock())
    )
    for _ in range(50):
        await asyncio.sleep(0)
        if playback._active_playback_task is task:
            break

    await playback.stop_playback(None)
    await task

    assert task.done()
    assert playback._active_playback_task is None


@pytest.mark.asyncio
async def test_speak_browser_cancelled_mid_sentence_wait_via_stop_playback(monkeypatch):
    """Covers the CancelledError re-raise inside _speak_browser's own sleep."""
    playback.configure(None, None, engine="browser")
    playback._voice_assigner = None
    monkeypatch.setattr(playback, "_play_browser", AsyncMock())

    task = asyncio.create_task(
        playback.speak(
            "A reasonably long sentence to keep the sleep alive here.",
            "NPC",
            None,
            MagicMock(),
        )
    )
    for _ in range(50):
        await asyncio.sleep(0)
        if playback._active_playback_task is task:
            break

    await playback.stop_playback(None)
    await task

    assert task.done()
    assert playback._active_playback_task is None
