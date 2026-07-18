"""Deterministic campaign prologue helpers.

The build pipeline writes the prologue once as a Foundry JournalEntry. The
session-start path then replays that journal without asking the LLM to invent
anything live.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_FLAG_NAMESPACE = "ai-gm"


def _strip_html(text: str) -> str:
    """Collapse the simple HTML that Foundry journals store."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, limit: int = 280) -> str:
    text = _strip_html(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_prologue_pages(prologue: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the Foundry journal page payload for a campaign prologue."""
    pages: List[Dict[str, Any]] = []
    title = prologue.get("title", "Prologue")
    frame_narrative = (prologue.get("frame_narrative") or "").strip()
    panels = prologue.get("panels", [])

    if frame_narrative:
        pages.append(
            {
                "name": "Prologue",
                "type": "text",
                "text": {
                    "content": f"<h2>{title}</h2><p><em>{frame_narrative}</em></p>",
                    "format": 1,
                },
            }
        )

    for panel in panels:
        if not isinstance(panel, dict):
            continue
        panel_title = panel.get("title", "Panel")
        panel_body = (panel.get("body") or "").strip()
        era = (panel.get("era") or "").strip()
        image_src = panel.get("image_src") or (
            f"ai-gm-prologue/{panel.get('image_file')}" if panel.get("image_file") else None
        )
        if image_src:
            pages.append(
                {
                    "name": panel_title,
                    "type": "image",
                    "image": {"src": image_src},
                    "text": {"content": f"<p><strong>{panel_title}</strong></p>", "format": 1},
                }
            )

        era_header = f"<h3><em>[{era}]</em></h3>" if era else ""
        pages.append(
            {
                "name": panel_title,
                "type": "text",
                "text": {
                    "content": f"<h2>{panel_title}</h2>{era_header}<p>{panel_body}</p>",
                    "format": 1,
                },
            }
        )

    return pages


async def load_prologue_entry(
    foundry,
    journal_uuid: Optional[str] = None,
    include_shown: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch the deployed prologue journal and its flag/page payload."""
    target = json.dumps(journal_uuid) if journal_uuid else "null"
    shown_filter = "" if include_shown else " && !j.flags?.[\"ai-gm\"]?.shown"
    js = f"""
const target = {target};
let entry = null;
if (target) {{
  entry = await fromUuid(target);
}} else {{
  entry = game.journal.find(j => j.flags?.["{_FLAG_NAMESPACE}"]?.prologue{shown_filter});
}}
if (!entry) return null;
const flags = entry.flags?.["{_FLAG_NAMESPACE}"] || {{}};
const pages = Array.from(entry.pages?.contents ?? entry.pages ?? []).map((p, index) => ({{
  name: p.name ?? "",
  type: p.type ?? "",
  src: p.src ?? p.image?.src ?? "",
  content: p.text?.content ?? "",
  index,
}}));
return {{
  uuid: entry.uuid ?? entry.id ?? "",
  title: entry.name ?? "",
  vessel: flags.vessel ?? "",
  shown: !!flags.shown,
  frame_narrative: flags.frame_narrative ?? "",
  pages,
}};
"""
    result = await foundry.execute_js(js)
    data = result.get("result") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else None


def describe_prologue(entry: Dict[str, Any]) -> str:
    """Build a concise summary for the opening prompt."""
    title = entry.get("title") or "Prologue"
    vessel = entry.get("vessel") or "tome"
    pages = entry.get("pages") or []
    text_pages = [p for p in pages if isinstance(p, dict) and p.get("type") == "text" and p.get("content")]

    parts = [f"Prologue '{title}' ({vessel})."]
    if text_pages:
        body_pages = text_pages
        if text_pages[0].get("name") == "Prologue":
            frame = _truncate(text_pages[0].get("content", ""))
            if frame:
                parts.append(f"Frame: {frame}")
            body_pages = text_pages[1:]
        if body_pages:
            beats = []
            for page in body_pages:
                name = page.get("name") or "Panel"
                text = _truncate(page.get("content", ""))
                beats.append(f"{name}: {text}" if text else name)
            if beats:
                parts.append("Beats: " + " | ".join(beats))

    return " ".join(parts)


async def _set_prologue_shown(foundry, journal_uuid: str, shown: bool) -> bool:
    js = f"""
const journal = await fromUuid({json.dumps(journal_uuid)});
if (!journal) return false;
await journal.setFlag("{_FLAG_NAMESPACE}", "shown", {str(shown).lower()});
return true;
"""
    result = await foundry.execute_js(js)
    return bool(result.get("result")) if isinstance(result, dict) else False


async def reset_prologue_shown(foundry, journal_uuid: Optional[str] = None) -> bool:
    """Reset the replay flag so a restarted campaign can play again."""
    entry = await load_prologue_entry(foundry, journal_uuid, include_shown=True)
    if not entry:
        return False
    return await _set_prologue_shown(foundry, entry["uuid"], False)


async def _call_narrate(narrate_fn: Callable[[str], Awaitable[Any] | Any], text: str) -> None:
    result = narrate_fn(text)
    if inspect.isawaitable(result):
        await result


async def _share_image(foundry, src: str, title: str) -> None:
    js = f"""
const src = {json.dumps(src)};
const title = {json.dumps(title)};
const pop = new ImagePopout(src, {{ title, shareable: true }});
await pop.render(true);
if (typeof pop.shareImage === "function") {{
  await pop.shareImage();
}}
return true;
"""
    await foundry.execute_js(js)


async def _dwell(seconds: float, interrupt_event: Optional[asyncio.Event]) -> None:
    remaining = max(0.0, seconds)
    if interrupt_event and interrupt_event.is_set():
        await asyncio.sleep(1)
        return
    while remaining > 0:
        step = min(1.0, remaining)
        await asyncio.sleep(1.0 if interrupt_event and interrupt_event.is_set() else step)
        if interrupt_event and interrupt_event.is_set():
            return
        remaining -= step


def _panel_dwell_seconds(text: str) -> float:
    return min(25.0, 4.0 + len(text) / 15.0)


async def present_prologue(
    foundry,
    narrate_fn: Callable[[str], Awaitable[Any] | Any],
    journal_uuid: str,
    interrupt_event: Optional[asyncio.Event] = None,
    entry: Optional[Dict[str, Any]] = None,
) -> bool:
    """Replay the prologue journal as a deterministic presentation."""
    prologue = entry or await load_prologue_entry(foundry, journal_uuid)
    if not prologue or not prologue.get("uuid") or prologue.get("shown"):
        return False

    if not await _set_prologue_shown(foundry, prologue["uuid"], True):
        logger.warning("Could not mark prologue as shown before playback")
        return False

    event = interrupt_event or getattr(foundry, "_prologue_interrupt_event", None)
    pages = prologue.get("pages") or []
    if not pages:
        return True

    for page in pages:
        if not isinstance(page, dict):
            continue

        page_type = (page.get("type") or "").lower()
        if page_type == "image":
            src = page.get("src") or ""
            if src:
                try:
                    await _share_image(foundry, src, page.get("name") or prologue["title"])
                except Exception as exc:
                    logger.warning("Prologue image share failed: %s", exc)
            continue

        if page_type != "text":
            continue

        text = _strip_html(page.get("content") or "")
        if not text:
            continue

        try:
            await _call_narrate(narrate_fn, text)
        except Exception as exc:
            logger.warning("Prologue narration failed: %s", exc)
            continue

        dwell = 1.0 if page.get("name") == "Prologue" else _panel_dwell_seconds(text)
        try:
            await _dwell(dwell, event)
        except Exception as exc:
            logger.warning("Prologue dwell failed: %s", exc)

    return True
