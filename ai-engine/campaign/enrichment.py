"""Folding a new source into an existing campaign's world and lore.

Pure functions only (no LLM, Foundry or vault I/O), so the merge rules can be
tested directly; orchestrator_enrich.py does the calling. The rule throughout:
existing content wins. A new source fills gaps and adds things, and anything
that disagrees is reported as a conflict for the GM's canon review instead of
being applied.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from campaign.importer import extract_pdf_text
from campaign.generator import _norm_scene
from utils.path_safety import sanitize_filename

SOURCE_SUFFIXES = {".pdf", ".md", ".txt"}
LORE_SECTIONS = ("npcs", "locations", "factions", "artifacts")

# Only these disagree in a way worth a GM's review. `role` and `type` hold the
# campaign's own vocabulary (a combat category like "boss", a location kind like
# "ruin"), so another source's job title or place-type will always differ.
_CONFLICT_KEYS = {"alignment", "faction"}
# Free text: two sources describing the same thing never word it the same way,
# so a difference is kept as a note under the source rather than flagged.
_PROSE_KEYS = {"description", "personality", "motivations"}

# Fields that are references or bookkeeping, not lore: never merged, never
# reported as conflicts.
_NON_LORE_KEYS = {
    "name", "source_chapter", "source_chapters", "sources", "existing_uuid",
    "portrait_file", "portrait_needed", "portrait_src", "map_file", "map_needed",
    "map_style", "foundry_scene_name", "existing_area", "act", "scenes",
}


# ─── SOURCES ──────────────────────────────────────────────────────────────


def source_id(name: str) -> str:
    """Stable, filesystem-safe id for a source (file stem, pack or folder name)."""
    return sanitize_filename(re.sub(r"\s+", " ", Path(name).stem or name).strip().lower())


def read_text_pages(path: Path) -> List[Tuple[int, str]]:
    """A .md/.txt file as [(page, text)]: one page per blank-line-separated
    block of about 3000 characters, so chunk_pages can split it further."""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    pages, block = [], ""
    for para in re.split(r"\n\s*\n", text):
        if block and len(block) + len(para) > 3000:
            pages.append(block)
            block = ""
        block += ("\n\n" if block else "") + para
    if block:
        pages.append(block)
    return list(enumerate(pages, 1))


def load_sources(source_path: str) -> List[Dict[str, Any]]:
    """Every readable file under source_path (or source_path itself) as
    {id, title, type, path, pages}. Hidden files, empty files and unsupported
    types are skipped, so pointing this at a folder of mixed material is safe.
    """
    root = Path(source_path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )
    sources = []
    for f in files:
        suffix = f.suffix.lower()
        if suffix not in SOURCE_SUFFIXES or f.stat().st_size == 0:
            continue
        pages = extract_pdf_text(str(f)) if suffix == ".pdf" else read_text_pages(f)
        if pages:
            sources.append({"id": source_id(f.name), "title": f.stem, "type": suffix[1:],
                            "path": str(f), "pages": pages})
    return sources


# ─── WORLD / HISTORY DELTA ────────────────────────────────────────────────

_DELTA_SYSTEM = (
    "You are extending the world lore of an existing TTRPG campaign with a NEW "
    "source. You are given the campaign's current Worldbuilding and History "
    "documents and GM notes extracted from the new source.\n\n"
    "Write ONLY what is new:\n"
    "- WORLD additions: geography, peoples, religion, magic, factions, places "
    "and other setting facts the current Worldbuilding document does not "
    "already cover.\n"
    "- HISTORY additions: events, eras and dates the current History document "
    "does not already cover.\n"
    "- CONFLICTS: any claim in the new notes that contradicts the current "
    "documents, one per line as: claim from the new source | what the current "
    "document says\n\n"
    "Rules:\n"
    "- EXTRACT/REPHRASE ONLY from the new notes. Do not invent lore.\n"
    "- Do not repeat anything the current documents already say.\n"
    "- A claim you list under CONFLICTS must NOT also appear under WORLD or "
    "HISTORY: the current documents stand until the GM decides.\n"
    "- Prefer specific names and facts; the text is indexed for search.\n"
    "- Output format is STRICT. Emit exactly:\n"
    "===WORLD===\n<markdown or nothing>\n"
    "===HISTORY===\n<markdown or nothing>\n"
    "===CONFLICTS===\n<lines or nothing>\n"
    "===END===\n"
    "- No other text before ===WORLD=== or after ===END===."
)


def build_delta_prompt(existing_world: str, existing_history: str, notes: str,
                       source_title: str) -> Tuple[str, str]:
    """(system, user) prompts for the world/history delta call."""
    user = (
        f"CURRENT WORLDBUILDING:\n{existing_world.strip() or '(none yet)'}\n\n"
        f"CURRENT HISTORY:\n{existing_history.strip() or '(none yet)'}\n\n"
        f"NEW SOURCE: {source_title}\nNOTES FROM THE NEW SOURCE:\n{notes}"
    )
    return _DELTA_SYSTEM, user


def parse_delta_response(text: str) -> Tuple[str, str, List[Dict[str, str]]]:
    """Split a delta response into (world_md, history_md, conflicts).

    Each conflict is {"claim": ..., "existing": ...}. A response with no
    markers yields nothing rather than guessing, since appending unparsed
    text to a lore document would be worse than adding nothing.
    """
    if not text or "===WORLD===" not in text:
        return "", "", []
    _, _, rest = text.partition("===WORLD===")
    world, _, rest = rest.partition("===HISTORY===")
    history, _, rest = rest.partition("===CONFLICTS===")
    conflicts_raw, _, _ = rest.partition("===END===")
    conflicts = []
    for line in conflicts_raw.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line or line.lower() in ("none", "(none)", "n/a"):
            continue
        claim, _, existing = line.partition("|")
        conflicts.append({"claim": claim.strip(), "existing": existing.strip()})
    return _blank_if_none(world), _blank_if_none(history), conflicts


def _blank_if_none(md: str) -> str:
    md = md.strip()
    return "" if md.lower() in ("", "none", "(none)", "n/a", "nothing") else md


_STOPWORDS = {"that", "this", "with", "from", "were", "where", "which", "their", "have", "been",
              "also", "into", "after", "before", "once", "known", "other", "there", "these"}


def _claim_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z']{4,}", text.lower()) if w not in _STOPWORDS}


def drop_conflicting_lines(markdown: str, conflicts: List[Dict[str, str]]) -> str:
    """Remove added lines that carry a claim the model also flagged as a conflict.

    The prompt tells the model not to add a claim it lists under CONFLICTS, but
    it does anyway, which would write the contradiction into the lore. A line
    is dropped when it contains most of a flagged claim's significant words, or
    is made up mostly of them. The claim is still in the canon queue and the
    source's own notes, so nothing is lost.
    """
    claims = [w for w in (_claim_words(c.get("claim", "")) for c in conflicts) if len(w) >= 3]
    if not claims:
        return markdown
    kept = []
    for line in markdown.splitlines():
        words = _claim_words(line)
        flagged = any(
            len(words & claim) / len(claim) >= 0.75 or (words and len(words & claim) / len(words) >= 0.6)
            for claim in claims
        )
        if not flagged:
            kept.append(line)
    return "\n".join(kept).strip()


def append_section(existing: str, addition: str, source_title: str) -> str:
    """existing plus a '## From <source>' section. The existing text is kept
    byte-for-byte; an empty addition returns it unchanged."""
    if not addition.strip():
        return existing
    if not existing or existing.endswith("\n\n"):
        sep = ""
    elif existing.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    return f"{existing}{sep}## From {source_title}\n\n{addition.strip()}\n"


# ─── ENTITIES ─────────────────────────────────────────────────────────────

_ENTITIES_SYSTEM = (
    "Extract the lore entities from these GM notes as JSON. Output ONLY one JSON "
    "object with exactly these keys, each an array (empty if none):\n"
    '"npcs": {name, role, description, personality, motivations, faction, alignment}\n'
    '"locations": {name, type, description, key_features (array), rumors (array)}\n'
    '"factions": {name, description, goals (array), members (array), alignment}\n'
    '"artifacts": {name, type, description}\n'
    "Rules: EXTRACT ONLY what the notes state; leave a field out rather than "
    "guessing; use the name exactly as the notes give it."
)


def build_entities_prompt(notes: str) -> Tuple[str, str]:
    return _ENTITIES_SYSTEM, f"GM NOTES:\n{notes}"


def enrich_entity(existing: Dict[str, Any], incoming: Dict[str, Any],
                  src: str) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Merge incoming into existing, existing winning.

    Empty fields are filled and lists are unioned (order kept, no duplicates).
    A non-empty scalar that differs is left alone: an alignment or faction
    clash is returned as a conflict, a different description is kept as a
    `source_notes` entry, and anything else is ignored. `src` is added to the entity's `sources`. The input dicts are not modified.
    """
    merged = dict(existing)
    conflicts: List[Dict[str, str]] = []
    name = existing.get("name", "")
    for key, value in incoming.items():
        if key in _NON_LORE_KEYS or value in (None, "", [], {}):
            continue
        have = merged.get(key)
        if isinstance(value, list):
            base = list(have) if isinstance(have, list) else ([] if not have else [have])
            base += [v for v in value if v not in base]
            merged[key] = base
        elif have in (None, "", [], {}):
            merged[key] = value
        elif isinstance(have, str) and isinstance(value, str) and _differs(have, value):
            if key in _CONFLICT_KEYS and not (key == "alignment" and not _alignments_oppose(have, value)):
                conflicts.append({"entity": name, "field": key, "existing": have, "claim": value})
            elif key in _PROSE_KEYS:
                note = f"{key}: {value} (from {src})"
                merged["source_notes"] = list(dict.fromkeys(list(merged.get("source_notes", [])) + [note]))
    merged["sources"] = list(dict.fromkeys(list(existing.get("sources", [])) + [src]))
    return merged, conflicts


_ALIGNMENT_ABBREV = {"lg": "lawful good", "ng": "neutral good", "cg": "chaotic good", "ln": "lawful neutral",
                     "n": "neutral", "tn": "neutral", "cn": "chaotic neutral", "le": "lawful evil",
                     "ne": "neutral evil", "ce": "chaotic evil"}


def _alignment_words(text: str) -> set:
    text = _ALIGNMENT_ABBREV.get(text.strip().lower(), text.lower())
    return {w for w in ("lawful", "chaotic", "good", "evil") if w in text}


def _alignments_oppose(a: str, b: str) -> bool:
    """True only when two alignments contradict: good against evil, or lawful
    against chaotic. "Evil" against "LE" (or "Good" against "lawful good") is
    the same thing in different words or a narrower statement, not a clash."""
    wa, wb = _alignment_words(a), _alignment_words(b)
    for axis in ({"good", "evil"}, {"lawful", "chaotic"}):
        if wa & axis and wb & axis and wa & axis != wb & axis:
            return True
    return False


def _differs(a: str, b: str) -> bool:
    """True when two strings disagree; a difference of wording only in one
    containing the other (a fuller description of the same fact) is not one."""
    na, nb = _norm_scene(a), _norm_scene(b)
    return bool(na and nb) and na != nb and na not in nb and nb not in na


def merge_entities(
    existing_items: List[Dict[str, Any]],
    incoming_items: List[Dict[str, Any]],
    src: str,
    aliases: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[Dict[str, str]]]:
    """Fold incoming_items into existing_items for one section.

    aliases maps an incoming name to the existing name it is the same thing as
    (from semantic matching); otherwise names are compared normalized. Returns
    (items, {"added": n, "enriched": n}, conflicts). Existing order is kept and
    new entities are appended.
    """
    aliases = aliases or {}
    items = [dict(i) for i in existing_items]
    index = {_norm_scene(i.get("name", "")): n for n, i in enumerate(items) if i.get("name")}
    stats = {"added": 0, "enriched": 0}
    conflicts: List[Dict[str, str]] = []
    for inc in incoming_items:
        name = (inc.get("name") or "").strip()
        if not name:
            continue
        target = index.get(_norm_scene(aliases.get(name, name)))
        if target is None:
            new = {k: v for k, v in inc.items() if v not in (None, "", [], {})}
            new["name"] = name
            new["sources"] = [src]
            items.append(new)
            index[_norm_scene(name)] = len(items) - 1
            stats["added"] += 1
        else:
            before = items[target]
            merged, found = enrich_entity(before, inc, src)
            if merged != before:
                stats["enriched"] += 1
            items[target] = merged
            conflicts += found
    return items, stats, conflicts
