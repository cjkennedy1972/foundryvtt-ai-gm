"""Storyteller's Cinema: put the AI GM's voice on screen as subtitles and a cinematic stage.

Cinema is a Foundry module, so everything here runs in the engine's GM browser session through `execute_js`
and degrades to a no-op when the module is missing or inactive. What it uses (read from its source):

- `window.StorytellerCinema.say(name, text, {portrait, side, duration})` broadcasts a subtitle (and portrait) to every
  client through socketlib; GM only; `duration` is in MILLISECONDS. It writes `name` and `text` into the page as raw
  HTML, so both are escaped here: NPC lines are model output, and without this a player's input could end up as markup
  in every player's browser.
- `clearSubtitles()` hides the subtitle without touching settings (`clear()` also resets the cast with settings writes).
- Scene flags under `storyteller-cinema`: `active` (cinematic mode on), `viewMode` ("battlemap" | "cinematic" default),
  `cinematicBg` (a widescreen image that replaces the map), `cinematicBgDim` (0-1).
"""

from __future__ import annotations

import html
import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MODULE_ID = "storyteller-cinema"
_FLAG_KEYS = ("active", "viewMode", "cinematicBg", "cinematicBgDim")
_AVAILABLE_TTL = 30.0


def reading_time(text: str) -> float:
    """Seconds a subtitle should stay up: ~0.4 s a word, at least 3 s, at most 12 s."""
    return min(12.0, max(3.0, 0.4 * len((text or "").split())))


class CinemaDirector:
    """The engine's handle on Storyteller's Cinema. Every method returns False/None rather than raising."""

    def __init__(self, foundry):
        self.foundry = foundry
        self._available: Optional[bool] = None
        self._checked_at = 0.0

    async def _js(self, script: str) -> Any:
        try:
            res = await self.foundry.execute_js(script)
        except Exception as e:                       # relay down, JS gate closed, or not a real client
            logger.debug(f"[Cinema] script failed: {e}")
            return None
        return res.get("result") if isinstance(res, dict) else None

    async def available(self, *, force: bool = False) -> bool:
        """Is the module active in this world, with its API loaded in a GM session? (Cached for 30 s.)"""
        now = time.monotonic()
        if not force and self._available is not None and now - self._checked_at < _AVAILABLE_TTL:
            return self._available
        ok = await self._js(
            f"return !!(game.modules.get('{MODULE_ID}')?.active && game.user?.isGM "
            "&& typeof window.StorytellerCinema?.say === 'function');")
        self._available, self._checked_at = bool(ok), now
        return self._available

    async def say(self, speaker: str, text: str, *, portrait: Optional[str] = None, side: str = "left",
                  duration_s: Optional[float] = None) -> bool:
        """Show `text` as a subtitle (with `speaker`'s portrait) on every client."""
        if not text or not await self.available():
            return False
        options: Dict[str, Any] = {"side": side if side in ("left", "right") else "left"}
        if portrait:
            options["portrait"] = str(portrait)
        if duration_s:
            options["duration"] = int(duration_s * 1000)                       # Cinema wants milliseconds
        shown = await self._js(
            f"await window.StorytellerCinema.say({json.dumps(html.escape(speaker or 'GM'))}, "
            f"{json.dumps(html.escape(text))}, {json.dumps(options)}); return true;")
        return bool(shown)

    async def clear_subtitles(self) -> bool:
        if not await self.available():
            return False
        return bool(await self._js("window.StorytellerCinema.clearSubtitles(); return true;"))

    async def scene_flags(self, scene: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The scene's Cinema flags (the active scene when `scene` is None; an id or a name otherwise)."""
        found = await self._js(
            f"const sc = {_scene_js(scene)}; if (!sc) return null; "
            f"const out = {{}}; for (const k of {json.dumps(list(_FLAG_KEYS))}) out[k] = sc.getFlag('{MODULE_ID}', k) ?? null; return out;")
        return found if isinstance(found, dict) else None

    async def set_scene(self, scene: Optional[str] = None, *, active: Optional[bool] = None, view_mode: Optional[str] = None,
                        background: Optional[str] = None, dim: Optional[float] = None) -> bool:
        """Set cinematic flags on a scene. Flags replicate to every client, so no socket call is needed. A value of
        None leaves that flag alone; use restore_scene to put previously read flags back."""
        updates = {"active": active, "viewMode": view_mode, "cinematicBg": background, "cinematicBgDim": dim}
        updates = {k: v for k, v in updates.items() if v is not None}
        if not updates or not await self.available():
            return False
        return bool(await self._js(
            f"const sc = {_scene_js(scene)}; if (!sc) return false; "
            f"for (const [k, v] of Object.entries({json.dumps(updates)})) await sc.setFlag('{MODULE_ID}', k, v); return true;"))

    async def restore_scene(self, scene: Optional[str], saved: Optional[Dict[str, Any]]) -> bool:
        """Put flags read with scene_flags back, removing any that were unset before."""
        if saved is None or not await self.available():
            return False
        return bool(await self._js(
            f"const sc = {_scene_js(scene)}; if (!sc) return false; const saved = {json.dumps(saved)}; "
            f"for (const k of {json.dumps(list(_FLAG_KEYS))}) {{ if (saved[k] === null || saved[k] === undefined) "
            f"await sc.unsetFlag('{MODULE_ID}', k); else await sc.setFlag('{MODULE_ID}', k, saved[k]); }} return true;"))


def _scene_js(scene: Optional[str]) -> str:
    if scene is None:
        return "game.scenes.active"
    ref = json.dumps(scene)
    return f"(game.scenes.get({ref}) || game.scenes.getName({ref}))"
