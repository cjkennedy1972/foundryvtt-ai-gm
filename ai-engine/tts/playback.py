"""TTS playback machinery: browser (Web Speech) and server (TTSService) engines.

Moved out of actions/executors.py (Phase 5 of the modular architecture split,
docs/ARCHITECTURE_REFACTOR.md) — the dispatch-table shape of executors.py
should stay flat; this module owns everything about *how* narration/speech
audio gets to the client, not *when* an action fires it.
"""

import asyncio
import logging
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

# Serialises TTS playback so narration and NPC speech never overlap.
# Acquired before calling _play_tts; held for the audio duration + a small gap.
_tts_lock = asyncio.Lock()

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
    """Play narrator TTS for a `narrate` action. Fire-and-forget (spawn this)."""
    try:
        if _tts_engine == "browser":
            from config import settings
            async with _tts_lock:
                await _play_browser(text, settings.tts_narrator_voice, foundry)
            return
        url = await _tts_service.narrate(text)
        if url:
            duration = _duration_from_url(url)
            async with _tts_lock:
                await _play_tts(url, foundry)
                await asyncio.sleep(duration + 0.4)
            # Re-arm the idle timer to the NORMAL gap now that playback has
            # finished. (Adding the duration here double-counted it — the sleep
            # above already waited out the audio — which left a 45+2*duration
            # gap of dead air between GM beats.)
            if _chat_listener is not None:
                _chat_listener._reset_idle_timer()
        else:
            logger.warning("[TTS] Narration produced no audio URL — skipping playback")
    except Exception as e:
        logger.warning(f"[TTS] Narration failed: {e}")


async def speak(text: str, npc_name: str, npc_record, foundry: FoundryClient):
    """Play NPC TTS for a `speak` action. Fire-and-forget (spawn this)."""
    try:
        if _tts_engine == "browser":
            voice = _voice_assigner.get_voice(npc_name, npc_record) if _voice_assigner else "echo"
            async with _tts_lock:
                await _play_browser(text, voice, foundry)
            return
        url = await _tts_service.speak(text, npc_name, npc_record)
        if url:
            duration = _duration_from_url(url)
            async with _tts_lock:
                await _play_tts(url, foundry)
                await asyncio.sleep(duration + 0.4)
            # Re-arm to the normal idle gap (see narrate() — adding the
            # duration here double-counted the audio that the sleep just waited).
            if _chat_listener is not None:
                _chat_listener._reset_idle_timer()
        else:
            logger.warning(f"[TTS] NPC speech for '{npc_name}' produced no audio URL — skipping playback")
    except Exception as e:
        logger.warning(f"[TTS] NPC speech failed for {npc_name}: {e}")
