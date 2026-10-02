"""Which storytelling add-ons are active in the world, cached, for code that runs outside deploy (which already has
`mods`): session recaps and other journals the engine writes mid-play."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from campaign.modules.storyteller_x import book_sheet_flags

logger = logging.getLogger(__name__)
_TTL = 60.0
_cache: Dict[int, tuple] = {}          # id(foundry) -> (checked_at, set of active module ids)


async def active_module_ids(foundry: Any) -> set:
    """The ids of the world's active modules (cached 60 s per client); empty when it cannot be read."""
    hit = _cache.get(id(foundry))
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    try:
        info = await foundry.get_active_modules_info()
        mods = info.get("modules") if isinstance(info, dict) else None
        ids = {m.get("id") if isinstance(m, dict) else str(m) for m in (mods or [])}
    except Exception as e:
        logger.debug(f"[Storyteller] could not read active modules: {e}")
        return set()
    _cache[id(foundry)] = (time.monotonic(), ids)
    return ids


async def book_flags(foundry: Any) -> Dict[str, Any]:
    """Flags that make a journal open as a StoryTeller X book, when that module is active."""
    return book_sheet_flags(await active_module_ids(foundry))
