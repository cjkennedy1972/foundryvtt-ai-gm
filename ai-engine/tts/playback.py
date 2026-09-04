"""TTS playback machinery: browser (Web Speech) and server (TTSService) engines.

Moved out of actions/executors.py (Phase 5 of the modular architecture split,
docs/ARCHITECTURE_REFACTOR.md) — the dispatch-table shape of executors.py
should stay flat; this module owns everything about *how* narration/speech
audio gets to the client, not *when* an action fires it.
"""

import asyncio
import logging
import re
import wave as _wave
from pathlib import Path
from typing import Any, Optional

from foundry.client import FoundryClient

logger = logging.getLogger(__name__)

# Injected at startup by main.py; remains None when TTS is disabled.
_tts_service: Optional[Any] = None       # TTSService | None
_npc_registry: Optional[Any] = None      # NPCRegistry | None
_tts_volume: float = 0.8
_tts_engine: str = "server"              # "server" | "browser"
_voice_assigner: Optional[Any] = None    # VoiceAssigner (browser mode)

# Active playback task — can be cancelled on player input or stop control.
_active_playback_task: Optional[asyncio.Task] = None
_playback_lock = asyncio.Lock()          # Protects _active_playback_task

# Reference to the active ChatListener — set by configure() so TTS can bump
# the idle timer so pacing nudges don't fire mid-narration.
_chat_listener: Optional[Any] = None

# Map the six OpenAI/LocalAI voice names to Web Speech API parameters so the
# browser picks a comparable platform voice. (gender hint, rate, pitch)
_BROWSER_VOICE_MAP = {
    "onyx":    ("male",   0.95, 0.80),  # deep male — villains, authority
    "fable":   ("male",   0.98, 0.95),  # sage male — narrator, scholars
    "echo":    ("male",   1.00, 1.00),  # neutral male
    "nova":    ("female", 1.00, 1.00),  # warm female
    "shimmer": ("female", 1.08, 1.15),  # light female — bards, tricksters
    "alloy":   ("female", 1.00, 1.05),  # neutral female
}


def configure(tts_service, npc_registry, volume: float = 0.8, engine: str = "server"):
    """Wire TTS into this module (called once at startup)."""
    global _tts_service, _npc_registry, _tts_volume, _tts_engine, _voice_assigner
    _tts_service = tts_service
    _npc_registry = npc_registry
    _tts_volume = volume
    _tts_engine = engine
    if engine == "browser":
        from tts.voice_assigner import VoiceAssigner
        _voice_assigner = VoiceAssigner()


def set_chat_listener(listener) -> None:
    """Register the active ChatListener so TTS can bump its idle timer."""
    global _chat_listener
    _chat_listener = listener


async def stop_playback() -> None:
    """Cancel any in-progress TTS playback and broadcast stop to all clients."""
    global _active_playback_task
    task_to_cancel: Optional[asyncio.Task] = None
    async with _playback_lock:
        task_to_cancel = _active_playback_task
        _active_playback_task = None
        if (
            task_to_cancel
            and task_to_cancel is not asyncio.current_task()
            and not task_to_cancel.done()
        ):
            task_to_cancel.cancel()

    if (
        task_to_cancel
        and task_to_cancel is not asyncio.current_task()
        and not task_to_cancel.done()
    ):
        try:
            await task_to_cancel
        except asyncio.CancelledError:
            pass

    # Broadcast stop to all clients via the browser TTS module
    try:
        js = (
            f"const m=game.modules.get('aigm-tts');"
            f"if(m&&m.api){{m.api.stopAll();return{{ok:true}};}}"
            f"return{{ok:false,error:'aigm-tts module not active'}};"
        )
        # Note: we can't use foundry.execute_js here as we may not have a client context
        logger.debug("[TTS] Broadcast stop command (via browser module if active)")
    except Exception as e:
        logger.warning(f"[TTS] Failed to broadcast stop: {e}")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for sentence-by-sentence synthesis.

    Preserves sentence structure while splitting on period, exclamation, question.
    Simple heuristic: split on [.!?] followed by space and capital letter.
    """
    if not text:
        return []

    # Split on sentence boundaries: [.!?] + whitespace + capital letter
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    return [s.strip() for s in sentences if s.strip()]


def is_active() -> bool:
    """True when any TTS path is configured (server service or browser engine)."""
    return _tts_service is not None or _tts_engine == "browser"


def get_npc_record(npc_name: str):
    """Look up an NPC's registry record for voice assignment, if any."""
    return _npc_registry.get_npc_by_name(npc_name) if _npc_registry else None


def _wav_duration(path: Path) -> float:
    """Return playback duration in seconds for a WAV file."""
    try:
        with _wave.open(str(path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 3.0  # safe fallback


def _duration_from_url(url: str) -> float:
    """Resolve a served audio URL back to a local path and return its duration."""
    if _tts_service is None:
        return 3.0
    filename = url.rsplit("/", 1)[-1]
    return _wav_duration(Path(_tts_service.audio_dir) / filename)


def _browser_payload(text: str, voice_name: str) -> dict:
    """Build the Web Speech API payload for the aigm-tts module."""
    gender, rate, pitch = _BROWSER_VOICE_MAP.get(voice_name, ("male", 1.0, 1.0))
    return {
        "text": text,
        "gender": gender,
        "rate": rate,
        "pitch": pitch,
        "volume": _tts_volume,
        "lang": "en-US",
    }


async def _play_browser(text: str, voice_name: str, foundry: FoundryClient):
    """Broadcast Web Speech API playback to all clients via the aigm-tts module."""
    import json as _json
    payload_js = _json.dumps(_browser_payload(text, voice_name))
    js = (
        f"const m=game.modules.get('aigm-tts');"
        f"if(m&&m.api){{m.api.speakAll({payload_js});return{{ok:true}};}}"
        f"return{{ok:false,error:'aigm-tts module not active'}};"
    )
    try:
        res = await foundry.execute_js(js)
        result = res.get("result") if isinstance(res, dict) else None
        if isinstance(result, dict) and not result.get("ok"):
            logger.warning(f"[TTS] browser playback skipped: {result.get('error')}")
    except Exception as e:
        logger.warning(f"[TTS] browser speakAll failed: {e}")


async def _play_tts(audio_url: str, foundry: FoundryClient):
    """Trigger Foundry to play a TTS audio URL for all clients."""
    # Broadcast playback to all clients via Foundry's native AudioHelper
    # (v13: foundry.audio.AudioHelper; v11-12: global). The second arg `true`
    # pushes to every connected client. The engine serves the audio with CORS
    # headers so Foundry's Web Audio decoding works cross-origin.
    js = (
        f"const url={audio_url!r}, vol={_tts_volume};"
        f"const AH=(globalThis.foundry?.audio?.AudioHelper)??(typeof AudioHelper!=='undefined'?AudioHelper:null);"
        f"if(!AH)return{{ok:false,error:'no AudioHelper'}};"
        f"AH.play({{src:url,volume:vol,loop:false}},true);return{{ok:true}};"
    )
    try:
        res = await foundry.execute_js(js)
        result = res.get("result") if isinstance(res, dict) else None
        if isinstance(result, dict) and not result.get("ok"):
            logger.warning(f"[TTS] playback skipped: {result.get('error')}")
    except Exception as e:
        logger.warning(f"[TTS] playback failed: {e}")


async def narrate(text: str, foundry: FoundryClient):
    """Play narrator TTS for a `narrate` action. Fire-and-forget (spawn this).

    Synthesizes sentence-by-sentence so audio begins on sentence one while
    sentence two is still being generated. Cancellable via stop_playback().
    """
    global _active_playback_task
    try:
        async with _playback_lock:
            if _active_playback_task and not _active_playback_task.done():
                _active_playback_task.cancel()
                try:
                    await _active_playback_task
                except asyncio.CancelledError:
                    pass
            # Create the new playback task and store it
            _active_playback_task = asyncio.current_task()

        if _tts_engine == "browser":
            await _narrate_browser(text, foundry)
            return

        await _narrate_server(text, foundry)
    except asyncio.CancelledError:
        logger.info("[TTS] Narration cancelled by player input")
    except Exception as e:
        logger.warning(f"[TTS] Narration failed: {e}")
    finally:
        async with _playback_lock:
            _active_playback_task = None


async def _narrate_browser(text: str, foundry: FoundryClient):
    """Browser-engine narration with estimated duration."""
    from config import settings
    sentences = _split_sentences(text)
    if not sentences:
        return

    # Estimate duration: ~0.15 sec per word (150 wpm)
    word_count = len(text.split())
    total_duration = max(1.0, word_count * 0.15)
    per_sentence = total_duration / len(sentences) if sentences else total_duration

    for sentence in sentences:
        await _play_browser(sentence, settings.tts_narrator_voice, foundry)
        # Let audio play (~0.15 per word, plus small gap)
        word_count_sent = len(sentence.split())
        duration = max(0.8, word_count_sent * 0.15)
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            raise

    if _chat_listener is not None:
        _chat_listener._reset_idle_timer(_escalate=True)


async def _narrate_server(text: str, foundry: FoundryClient):
    """Server-engine narration with sentence-by-sentence synthesis.

    Synthesizes and plays sentences sequentially. Logs when audio begins (first
    sentence ready), supporting barge-in measurement — the gap between narration
    start and actual audio playback. When cancelled mid-narration, logs the barge-in
    event so caller can measure interruption latency.
    """
    sentences = _split_sentences(text)
    if not sentences:
        logger.warning("[TTS] Narration produced no sentences — skipping playback")
        return

    for i, sentence in enumerate(sentences):
        try:
            url = await _tts_service.narrate(sentence)
            if url:
                duration = _duration_from_url(url)
                # Play audio as soon as first sentence is ready
                await _play_tts(url, foundry)
                if i == 0:
                    # Barge-in measurement: audio began on first sentence
                    logger.info(f"[TTS] Audio began on first sentence (sentence 1/{len(sentences)})")

                # Wait for audio to finish
                try:
                    await asyncio.sleep(duration + 0.2)
                except asyncio.CancelledError:
                    raise
            else:
                logger.warning(f"[TTS] Sentence {i+1} produced no audio URL")
        except asyncio.CancelledError:
            # Barge-in succeeded: player interrupted mid-narration
            logger.info(f"[TTS] Narration interrupted at sentence {i+1}/{len(sentences)}")
            raise
        except Exception as e:
            logger.warning(f"[TTS] Sentence {i+1} synthesis failed: {e}")

    # Re-arm idle timer now that playback finished
    if _chat_listener is not None:
        _chat_listener._reset_idle_timer(_escalate=True)


async def speak(text: str, npc_name: str, npc_record, foundry: FoundryClient):
    """Play NPC TTS for a `speak` action. Fire-and-forget (spawn this).

    Synthesizes sentence-by-sentence. Cancellable via stop_playback().
    """
    global _active_playback_task
    try:
        async with _playback_lock:
            if _active_playback_task and not _active_playback_task.done():
                _active_playback_task.cancel()
                try:
                    await _active_playback_task
                except asyncio.CancelledError:
                    pass
            _active_playback_task = asyncio.current_task()

        if _tts_engine == "browser":
            await _speak_browser(text, npc_name, npc_record, foundry)
            return

        await _speak_server(text, npc_name, npc_record, foundry)
    except asyncio.CancelledError:
        logger.info(f"[TTS] NPC speech for '{npc_name}' cancelled by player input")
    except Exception as e:
        logger.warning(f"[TTS] NPC speech failed for {npc_name}: {e}")
    finally:
        async with _playback_lock:
            _active_playback_task = None


async def _speak_browser(text: str, npc_name: str, npc_record, foundry: FoundryClient):
    """Browser-engine NPC speech with estimated duration."""
    voice = _voice_assigner.get_voice(npc_name, npc_record) if _voice_assigner else "echo"
    sentences = _split_sentences(text)
    if not sentences:
        return

    for sentence in sentences:
        await _play_browser(sentence, voice, foundry)
        word_count_sent = len(sentence.split())
        duration = max(0.8, word_count_sent * 0.15)
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            raise

    if _chat_listener is not None:
        _chat_listener._reset_idle_timer(_escalate=True)


async def _speak_server(text: str, npc_name: str, npc_record, foundry: FoundryClient):
    """Server-engine NPC speech with sentence-by-sentence synthesis.

    Synthesizes and plays NPC dialogue sequentially. Logs when audio begins and
    if interrupted mid-speech, supporting barge-in success measurement.
    """
    sentences = _split_sentences(text)
    if not sentences:
        logger.warning(f"[TTS] NPC speech for '{npc_name}' produced no sentences — skipping playback")
        return

    for i, sentence in enumerate(sentences):
        try:
            url = await _tts_service.speak(sentence, npc_name, npc_record)
            if url:
                duration = _duration_from_url(url)
                await _play_tts(url, foundry)
                if i == 0:
                    # Barge-in measurement: audio began on first sentence
                    logger.info(f"[TTS] Audio began on first sentence for '{npc_name}' (sentence 1/{len(sentences)})")

                # Wait for audio to finish
                try:
                    await asyncio.sleep(duration + 0.2)
                except asyncio.CancelledError:
                    raise
            else:
                logger.warning(f"[TTS] Sentence {i+1} for '{npc_name}' produced no audio URL")
        except asyncio.CancelledError:
            # Barge-in succeeded: player interrupted mid-speech
            logger.info(f"[TTS] NPC speech for '{npc_name}' interrupted at sentence {i+1}/{len(sentences)}")
            raise
        except Exception as e:
            logger.warning(f"[TTS] Sentence {i+1} for '{npc_name}' failed: {e}")

    if _chat_listener is not None:
        _chat_listener._reset_idle_timer(_escalate=True)
