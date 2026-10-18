"""A canonical character's look, read from the setting's wiki, for NPCs the engine has no stored look for.

Only text is fetched: the infobox (race, hair, eyes) and the sentences that describe appearance. A page counts only
when its title is the NPC's own name, so a loose search hit never paints the wrong person. Answers are cached on disk;
any failure returns None and the portrait falls back to its derived look.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

CACHE_FILE = Path("./campaign_assets/lore_look_cache.json")
_LOOK_WORDS = re.compile(r"\b(?:beard|mustache|ears|skin|tall|slender|muscular|frail|gaunt|scarred|\w+ (?:hair|eyes))\b", re.I)
_PRONOUNS = re.compile(r"\b(?:his|her|their|the|my|your|its) (?:hair|eyes)\b", re.I)


def _plain(wikitext: str) -> str:
    t = re.sub(r"\{\{[^{}]*\}\}", "", wikitext)
    t = re.sub(r"<ref[^>]*?(?:/>|>.*?</ref>)", "", t, flags=re.S)
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", t)
    return re.sub(r"<[^>]+>|'''?|==+[^=]*==+|\s+", " ", t).strip()


def look_from_wikitext(wikitext: str) -> Optional[str]:
    """Race, hair, eyes and up to two appearance sentences, or None when the page says nothing visual."""
    info = {}
    box = re.search(r"\{\{Infobox[^|]*\|(.*?)\n?\}\}", wikitext, re.S | re.I)
    if box:
        for part in re.split(r"\|(?=\s*[\w ]+=)", box.group(1)):
            key, _, val = part.partition("=")
            info[key.strip().lower()] = _plain(val)
    facts = [info[k] for k in ("race",) if info.get(k)]
    facts += [f"{info[k].lower()} {label}" for k, label in (("hair color", "hair"), ("eye color", "eyes")) if info.get(k)]
    body = _plain(wikitext.split("\n", 1)[1] if wikitext.startswith("{{") else wikitext)
    body = _plain(re.sub(r"\{\{Infobox.*?\n\}\}\n?", "", wikitext, flags=re.S | re.I)) if box else body
    sentences = [s for s in re.split(r"(?<=[.!?])\s", body) if _LOOK_WORDS.search(_PRONOUNS.sub("", s)) and len(s) < 220][:2]
    if not facts and not sentences:
        return None
    return ", ".join(facts + sentences)


async def canon_look(name: str) -> Optional[str]:
    """The wiki's description of the named character, or None."""
    api = settings.lore_wiki_api
    clean = re.sub(r"\s*\(.*?\)", "", name or "").strip()
    if not api or not clean:
        return None
    try:
        cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    except (OSError, ValueError):
        cache = {}
    key = clean.lower()
    if key in cache:
        return cache[key]
    look = None
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "aigm-portraits/1.0"}) as client:
            hit = (await client.get(api, params={"action": "query", "list": "search", "srsearch": clean, "srlimit": 1, "format": "json"})).json()
            found = hit["query"]["search"]
            if found and found[0]["title"].lower() == key:
                page = (await client.get(api, params={"action": "parse", "page": found[0]["title"], "prop": "wikitext", "format": "json"})).json()
                look = look_from_wikitext(page["parse"]["wikitext"]["*"])
                look = f"{clean}, {look}" if look else None
    except Exception as e:                     # a lookup must never block a portrait
        logger.warning(f"Lore lookup failed for '{clean}': {e}")
        return None
    cache[key] = look
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=1))
    except OSError:
        pass
    return look
