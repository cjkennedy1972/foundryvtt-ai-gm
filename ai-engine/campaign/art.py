"""What counts as real art on an actor or token, as opposed to stock or generic images.

Foundry gives a new actor a generic image (the mystery-man icon, or a core icon) when none is set. The
source book's compendia ship stock images too: class and item icons, and the shared humanoid avatar that
many monsters use (its tokens are cut from the same picture). None of these is a portrait of the actor,
so they are treated as missing.

GENERIC_MARKERS is the one list. The Foundry-side checks below are built from it, so the two never drift.
"""

import json

GENERIC_MARKERS = (
    "icons/",            # Foundry core icons, including the mystery-man default
    "/icons/",           # a system's or module's icon folder
    "/classes/",         # class icons
    "/class/",
    "mystery-man",
    "ddb-images/other/",  # the source book's stock images
    "/humanoid/",        # shared humanoid avatars
    "humanoid-",         # tokens cut from those avatars
)


def is_generic_art(path: str | None) -> bool:
    p = (path or "").strip().lower()
    if not p:
        return True
    return any(marker in p for marker in GENERIC_MARKERS)


def _generic_js() -> str:
    markers = json.dumps(list(GENERIC_MARKERS))
    return (
        f"const markers = {markers};"
        "const generic = p => { p = (p || '').trim().toLowerCase(); return !p || markers.some(m => p.includes(m)); };"
    )


def compendium_art_js(name: str) -> str:
    """The first actor in a compendium with this name, with its portrait and token art, or null."""
    want = json.dumps(name.strip().lower())
    return (
        f"const want = {want};"
        "for (const pack of game.packs) {"
        "  if (pack.documentName !== 'Actor') continue;"
        "  const hit = pack.index.find(e => (e.name || '').toLowerCase() === want);"
        "  if (!hit) continue;"
        "  const doc = await pack.getDocument(hit._id);"
        "  return {uuid: doc.uuid, img: doc.img || '', tokenImg: doc.prototypeToken?.texture?.src || doc.img || ''};"
        "}"
        "return null;"
    )


def art_check_js(names: list) -> str:
    """Names of the given world actors whose portrait or token is generic or missing."""
    return (
        _generic_js()
        + f"const want = new Set({json.dumps(list(names))});"
        "return game.actors.filter(a => want.has(a.name)"
        " && (generic(a.img) || generic(a.prototypeToken?.texture?.src)))"
        ".map(a => a.name);"
    )


def flag_generic_art_js(names: list) -> str:
    """Flag the given world actors for portrait generation when their art is generic. Returns how many."""
    return (
        _generic_js()
        + f"const want = new Set({json.dumps(list(names))}); let n = 0;"
        "for (const a of game.actors) {"
        "  if (!want.has(a.name)) continue;"
        "  if (!(generic(a.img) || generic(a.prototypeToken?.texture?.src))) continue;"
        "  await a.update({'flags.ai-gm.needs_portrait': true});"
        "  n++;"
        "}"
        "return n;"
    )
