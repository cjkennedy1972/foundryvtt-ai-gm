"""Sound and music playback.

Split out of actions/executors.py, which had grown to 2,445 lines. The
functions are moved verbatim; executors.py still owns the ACTION_HANDLERS
dispatch table and re-exports these names, so existing imports keep working.
"""

import asyncio
import html
import logging
from pathlib import Path
from typing import Any, Optional

from actions.executors_shared import ExecutionError, _extract_token_id, _require
from config import settings
from foundry.client import FoundryClient
from tts import playback as tts_playback
from utils.tasks import spawn

logger = logging.getLogger(__name__)


# Owned here because _resolve_sound_src is its only reader. executors.py's
# reset_action_caches() calls reset_sound_cache() on a scene or world change,
# since playlists differ between worlds.
_sound_src_cache: dict = {}
_sound_src_cache_at: float = 0.0


def reset_sound_cache() -> None:
    """Clear the resolved-sound cache. Called on scene/world change."""
    global _sound_src_cache, _sound_src_cache_at
    _sound_src_cache = {}
    _sound_src_cache_at = 0.0


async def _resolve_sound_src(sound_name: str, foundry: FoundryClient) -> Optional[str]:
    """Map a semantic sound name to a real Foundry asset path via the world's
    playlist sounds. Matches exact-name first, then substring, so the LLM's
    approximate names ("low_growl") still find a close sound if one exists."""
    global _sound_src_cache, _sound_src_cache_at
    import time as _t
    import re
    now = _t.monotonic()
    if not _sound_src_cache or now - _sound_src_cache_at > 60:
        catalog: dict = {}
        for pl in await foundry.get_playlists():
            for s in pl.get("sounds", []) or []:
                name = (s.get("name") or "").strip().lower()
                path = s.get("path") or s.get("src")
                if name and path:
                    catalog.setdefault(name, path)
        _sound_src_cache = catalog
        _sound_src_cache_at = now
    want = sound_name.strip().lower()
    if want in _sound_src_cache:
        return _sound_src_cache[want]
    # Fall back to a loose match: the LLM invents names like "creaking_wood"
    # while a playlist sound might be "Creaking Door" — match on any shared word.
    want_words = set(re.split(r"[^a-z0-9]+", want)) - {""}
    for name, path in _sound_src_cache.items():
        if want_words & (set(re.split(r"[^a-z0-9]+", name)) - {""}):
            return path
    return None


async def execute_play_sound(
    sound_name: str, volume: float = 0.5, foundry: FoundryClient = None, source: Optional[str] = None
) -> dict:
    """Play a sound effect in Foundry.

    The LLM emits a semantic name ("low_growl"); the relay needs a real audio
    `src` path. Resolve the name against the world's playlist sounds and play
    by path. If nothing matches (no SFX library deployed for that sound), skip
    quietly with success — a missing ambient cue must NOT read as a failed
    action, or it triggers a wasted corrective LLM retry every beat.
    """
    src = await _resolve_sound_src(sound_name, foundry)
    if not src:
        logger.info(f"[Sound] no matching sound for '{sound_name}' — skipped")
        return {"type": "play_sound", "sound_name": sound_name, "skipped": True, "success": True}
    result = await foundry.play_sound(src, volume=volume)
    logger.info(f"[Sound] {sound_name} -> {src} (volume {volume})")
    return {"type": "play_sound", "sound_name": sound_name, "src": src, "volume": volume, "result": result}


async def execute_play_music(
    playlist_name: str, volume: float = 0.5, foundry: FoundryClient = None, source: Optional[str] = None
) -> dict:
    """Play background music from a Foundry playlist.

    The playlist_name is the name of a Foundry playlist that contains tracks.
    Volume is 0-1, with 0.5 as default (50% volume).
    """
    result = await foundry.play_playlist(playlist_name, volume)
    logger.info(f"[Music] Playing playlist '{playlist_name}' at {int(volume*100)}% volume")
    return {"type": "play_music", "playlist": playlist_name, "volume": volume, "result": result}
