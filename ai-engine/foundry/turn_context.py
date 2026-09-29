"""What of the world goes into one turn's context, and how much of it.

The rule: what a turn carries must not grow with the campaign. Every actor in
the world (10k tokens on a 154-NPC campaign) and every map (1k tokens for 174)
went out on every turn. A turn now carries the characters in play — player
characters, whoever has a token on the map, and whoever the conversation just
named — and the maps within reach of this scene. The rest of the world reaches
the model through lore retrieval, when a turn is about it.

fit_blocks then holds the per-turn context to a budget, so whatever grows next
can't silently crowd recent conversation out of the model's context.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from utils.token_counter import estimate_tokens

logger = logging.getLogger(__name__)

# Words that start a name without identifying anyone: "Lord" alone must not
# pull every lord in the world into play.
_TITLES = {
    "lord", "lady", "sir", "dame", "king", "queen", "prince", "princess", "captain",
    "general", "elder", "high", "master", "mistress", "father", "mother", "brother",
    "sister", "scribe", "archivist", "commander", "knight", "saint", "old", "young",
    "the", "doctor", "baron", "baroness", "duke", "duchess", "count", "countess",
}
# Words in map names that say nothing about where a map is.
_MAP_WORDS = {"map", "scene", "level", "part", "area", "room", "floor", "upper",
              "lower", "the", "and", "interior", "exterior", "outside", "inside"}

MAPS_IN_REACH = 8


# ─── characters ───────────────────────────────────────────────────────────

def is_named(name: str, text: str, shared_first_words: frozenset = frozenset()) -> bool:
    """True if `text` (lowercased) names this character: the full name, or the
    first word of it when that word identifies someone ("Kansaldi" for
    "Kansaldi Fire-Eyes", but not "Lord" for "Lord Soth", nor "Dragonarmy"
    when it starts several actors' names)."""
    name = (name or "").strip().lower()
    if not name:
        return False
    keys = [name]
    first = name.split()[0]
    if len(first) >= 4 and first not in _TITLES and first not in shared_first_words and first != name:
        keys.append(first)
    return any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in keys)


def _same_actor(actor: Dict, token: Dict) -> bool:
    ref = token.get("actorUuid") or ""
    uuid = actor.get("uuid") or ""
    if ref and uuid and (uuid == ref or uuid.endswith("." + ref)):
        return True
    return bool(actor.get("name")) and actor.get("name") == token.get("name")


def characters_in_play(actors: List[Dict], tokens: List[Dict], player_names: Iterable[str],
                       conversation: str) -> List[Tuple[Dict, Optional[Dict]]]:
    """(actor, its token on this map or None) for each actor in play, then
    ({}, token) for any token with no actor in the world list."""
    players = {n.lower() for n in player_names}
    text = conversation.lower()
    # A first word several actors share ("Dragonarmy Soldier", "Dragonarmy
    # Officer") names none of them: matching it would pull them all in.
    firsts = [a.get("name", "").strip().lower().split()[0] for a in actors if a.get("name", "").strip()]
    shared = frozenset(w for w in firsts if firsts.count(w) > 1)
    in_play, matched = [], set()
    for actor in actors:
        token = next((t for t in tokens if _same_actor(actor, t)), None)
        if token is not None:
            matched.add(id(token))
        name = actor.get("name", "")
        if token is not None or name.lower() in players or is_named(name, text, shared):
            in_play.append((actor, token))
    in_play += [({}, t) for t in tokens if id(t) not in matched]
    return in_play


# ─── maps ─────────────────────────────────────────────────────────────────

def _map_words(name: str) -> List[str]:
    return [w for w in re.findall(r"[a-z']{4,}", name.lower()) if w not in _MAP_WORDS]


def maps_in_reach(current: str, all_names: List[str], campaign_scenes: List[Dict],
                  conversation: str, cap: int = MAPS_IN_REACH) -> Tuple[List[str], int]:
    """(map names worth offering switch_scene, how many others exist).

    First the maps the conversation names, then this scene's neighbours —
    the maps of its chapter, or its act — up to `cap`.
    """
    others = [n for n in all_names if n and n != current]
    if not others:
        return [], 0
    text = conversation.lower()
    # A campaign scene is known by its own name and by its Foundry map's name.
    def foundry_name(s: Dict) -> str:
        return s.get("foundry_scene_name") or s.get("name", "")

    named = []
    for name in others:
        aliases = [name] + [s.get("name", "") for s in campaign_scenes if foundry_name(s) == name]
        if any(re.search(r"\b" + re.escape(w) + r"\b", text) for a in aliases for w in _map_words(a)):
            named.append(name)

    here = next((s for s in campaign_scenes if current in (foundry_name(s), s.get("name"))), None)
    neighbours = []
    if here:
        for key in ("source_chapter", "act"):
            if here.get(key) not in (None, ""):
                neighbours = [foundry_name(s) for s in campaign_scenes
                              if s is not here and s.get(key) == here.get(key)]
                break
    chosen = list(dict.fromkeys(n for n in named + neighbours if n in others))[:cap]
    if not chosen and len(others) <= cap:
        chosen = others  # a small or improvised world: just list them
    return chosen, len(others) - len(chosen)


# ─── budget ───────────────────────────────────────────────────────────────

@dataclass
class Block:
    """One section of a turn's context. Lower priority numbers are kept first;
    priority 1 is never dropped."""
    priority: int
    label: str
    text: str


def fit_blocks(blocks: List[Block], budget_tokens: int) -> str:
    """Join the blocks that fit the budget, dropping the lowest-priority ones
    first, and log the turn's size so growth is visible, not silent."""
    kept, dropped, used = [], [], 0
    for block in sorted((b for b in blocks if b.text.strip()), key=lambda b: b.priority):
        cost = estimate_tokens(block.text)
        if block.priority > 1 and used + cost > budget_tokens:
            dropped.append(f"{block.label} ({cost})")
            continue
        kept.append(block)
        used += cost
    sizes = ", ".join(f"{b.label} {estimate_tokens(b.text)}" for b in kept)
    logger.info(f"[Turn] context ~{used} tokens (budget {budget_tokens}): {sizes}")
    if dropped:
        logger.warning(f"[Turn] over budget, dropped: {', '.join(dropped)}")
    if used > budget_tokens:
        logger.warning(f"[Turn] essential context alone is ~{used} tokens, over the {budget_tokens} budget")
    # Keep the caller's order: blocks read as location, characters, then the rest.
    order = {id(b): i for i, b in enumerate(blocks)}
    return "\n\n".join(b.text for b in sorted(kept, key=lambda b: order[id(b)]))
