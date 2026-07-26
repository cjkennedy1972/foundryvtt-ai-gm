"""Campaign Importer — Scan a published campaign folder, extract source material,
extract PDF text, chunk content, match assets, and prepare campaign data for the
existing pipeline.

Pure helper functions throughout for easy unit testing.
"""

import difflib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)

# ─── CONSTANTS ────────────────────────────────────────────────────────────

VALID_MAP_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
VALID_TOKEN_EXTENSIONS: Set[str] = {".png", ".jpg", ".jpeg", ".webp"}
VALID_HANDOUT_EXTENSIONS: Set[str] = {".pdf"}

# Prefixes that commercial map packs attach to indicate DPI/resolution quality
DPI_PREFIXES: Set[str] = {"300dpi", "300", "72dpi", "72", "100dpi", "150dpi"}
VARIANT_PREFIXES: Set[str] = {
    "grid", "gridless", "labels", "no labels", "gridded",
    "printer friendly", "printer_friendly", "vtt", "roll20",
    "closeup", "close_up", "close-up", "portrait", "fullbody", "full_body",
}

# File size thresholds
MAX_MAP_UPLOAD_BYTES: int = 40 * 1024 * 1024  # 40MB
MAX_FILE_BYTE_THRESHOLD: int = 50 * 1024 * 1024  # 50MB for conversion warning

# iCloud placeholder byte sizes (strict 0-byte check)
ICLOUD_PLACEHOLDER_SIZE: int = 0


# ─── PUBLIC API ───────────────────────────────────────────────────────────


def scan_product_folder(source_path: str) -> Dict[str, Any]:
    """Classify a published campaign folder into maps, tokens, handouts, and adventure PDFs.

    Returns:
        dict with keys: maps, tokens, handouts, adventure_pdfs, unmatched,
                        source_path, total_files, errors
    """
    src = Path(source_path)
    if not src.exists():
        return {"errors": [f"Source path does not exist: {source_path}"]}
    if not src.is_dir():
        return {"errors": [f"Source path is not a directory: {source_path}"]}

    result: Dict[str, Any] = {
        "maps": [],
        "tokens": [],
        "handouts": [],
        "adventure_pdfs": [],
        "unmatched": [],
        "source_path": str(src.resolve()),
        "total_files": 0,
        "errors": [],
    }

    for root, dirs, files in os.walk(src):
        root_path = Path(root)
        # Check for iCloud .icloud placeholder directories
        if any(p.endswith(".icloud") or p == "iCloud" for p in root_path.parts):
            continue

        # Skip FoundryVTT's own LevelDB stores (packs/*, data/*) — their LOCK
        # and *.log files are legitimately 0 bytes in a healthy database and
        # aren't product content to scan.
        if "CURRENT" in files and "LOCK" in files:
            continue

        dir_name = root_path.name.lower()
        classify = _classify_directory(dir_name)

        for fname in files:
            fpath = root_path / fname
            # Fail fast on 0-byte iCloud placeholders
            size = fpath.stat().st_size
            if size == ICLOUD_PLACEHOLDER_SIZE:
                result["errors"].append(
                    f"0-byte iCloud placeholder detected: {fpath}. "
                    "Run 'brctl download \"{fpath}\"' (on macOS) to fetch the real file."
                )
                continue

            if fname.startswith("."):
                continue

            ext = Path(fname).suffix.lower()

            # Adventure PDF preference: directory classification wins — a PDF
            # inside Handouts/ stays a handout even if named "Printer Friendly".
            # Only generic/adventure dirs promote name-keyword PDFs to adventures.
            fname_lower = fname.lower()
            is_adventure_name = any(kw in fname_lower for kw in ("adventure", "module", "scenario", "campaign", "printer"))
            if ext == ".pdf" and (classify == "adventure" or (classify == "generic" and is_adventure_name)):
                result["adventure_pdfs"].append(str(fpath))
            elif classify == "maps" and ext in VALID_MAP_EXTENSIONS:
                result["maps"].append(str(fpath))
            elif classify == "tokens" and ext in VALID_TOKEN_EXTENSIONS:
                result["tokens"].append(str(fpath))
            elif classify == "handouts" and ext in VALID_HANDOUT_EXTENSIONS:
                result["handouts"].append(str(fpath))
            else:
                result["unmatched"].append(str(fpath))

            result["total_files"] += 1

    # Prefer the Printer_Friendly PDF variant when multiple adventure PDFs exist
    if result["adventure_pdfs"]:
        result["adventure_pdfs"] = _pick_preferred_pdf(result["adventure_pdfs"])

    # Sort entries for deterministic order (except adventure_pdfs which keeps
    # the preferred order established by _pick_preferred_pdf)
    for key in ("maps", "tokens", "handouts", "unmatched"):
        result[key].sort(key=lambda p: str(p).lower())

    return result


def extract_pdf_text(pdf_path: str, min_chars_per_page: int = 50) -> List[Tuple[int, str]]:
    """Extract text from PDF using pypdf, returning (page_number, text) pairs.
    Drops pages with fewer than min_chars_per_page characters.
    """
    try:
        import pypdf
    except ImportError as exc:
        logger.error("pypdf not installed; install with: pip install pypdf>=4.0,<6")
        raise

    pages: List[Tuple[int, str]] = []
    with open(pdf_path, "rb") as f:
        reader = pypdf.PdfReader(f)
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if len(text.strip()) >= min_chars_per_page:
                pages.append((i + 1, text.strip()))
    return pages


_JOURNAL_BLOCK_TAG_RE = re.compile(r"</(p|div|h[1-6]|li|tr|blockquote)>|<br\s*/?>", re.IGNORECASE)
_JOURNAL_TAG_RE = re.compile(r"<[^>]+>")


def _journal_html_to_text(html_content: str) -> str:
    """Convert a Foundry JournalEntryPage's stored HTML to plain text.

    Block-level closing tags become line breaks before the remaining tags
    are stripped, so paragraph structure survives for chunk_pages/pass-1
    (unlike a blind tag-strip, which would flatten everything to one line).
    """
    import html as _html_entities

    text = _JOURNAL_BLOCK_TAG_RE.sub("\n", html_content or "")
    text = _JOURNAL_TAG_RE.sub("", text)
    text = _html_entities.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_ADVENTURE_ENTRY_RE = re.compile(r"^(chapter\s+\d+|appendix\s+[a-z])\b", re.IGNORECASE)


def is_adventure_journal_entry(name: str) -> bool:
    """Match a JournalEntry name against 'Chapter N' / 'Appendix X' naming.

    A DDBImporter journals compendium is often shared across every
    sourcebook synced into the world, not just the adventure being
    imported (Player's Handbook, Xanathar's Guide, etc. alongside the
    actual chapters) — this separates the adventure's own chapters from
    that unrelated reference-book noise.
    """
    return bool(_ADVENTURE_ENTRY_RE.match((name or "").strip()))


def journal_entries_to_pages(
    entries: List[Dict[str, Any]], min_chars_per_page: int = 50
) -> List[Tuple[int, str]]:
    """Flatten Foundry JournalEntry pack documents into (page_number, text) pairs.

    Mirrors extract_pdf_text's return shape so chunk_pages() needs no changes.
    Each JournalEntryPage becomes one "page"; near-empty pages are dropped.
    """
    pages: List[Tuple[int, str]] = []
    page_num = 0
    for entry in entries:
        for page in entry.get("pages", []):
            page_num += 1
            text = _journal_html_to_text(page.get("html", ""))
            if len(text) >= min_chars_per_page:
                pages.append((page_num, text))
    return pages


def chunk_pages(pages: List[Tuple[int, str]], tokens_per_chunk: int = 12000) -> List[str]:
    """Chunk extracted PDF pages on page boundaries.
    Approximates 1 token ≈ 4 characters.
    Returns a list of text-chunks, each prefixed with page references.
    """
    if not pages:
        return []

    max_chars = tokens_per_chunk * 4
    chunks: List[List[Tuple[int, str]]] = []
    current: List[Tuple[int, str]] = []
    current_chars = 0

    for page_num, text in pages:
        if current_chars + len(text) > max_chars and current:
            chunks.append(current)
            current = [(page_num, text)]
            current_chars = len(text)
        else:
            current.append((page_num, text))
            current_chars += len(text)

    if current:
        chunks.append(current)

    result: List[str] = []
    for chunk in chunks:
        page_nums = [p[0] for p in chunk]
        header = f"Pages {min(page_nums)}-{max(page_nums)}\n{'=' * 40}\n"
        body = "\n\n".join(p[1] for p in chunk)
        result.append(header + body)

    return result


# ─── NORMALIZATION ────────────────────────────────────────────────────────


def normalize_name(name: str) -> str:
    """Normalize a file name so fuzzy matching is deterministic and independent
    of vendor prefixes/suffixes.

    Strips:
      - file extension
      - DPI prefixes (300DPI_/72DPI_ etc.)
      - variant prefixes (Grid_/Gridless_/Labels_ etc.)
      - product-name suffixes after ' - '
      - non-alphanumeric noise (underscores, repeated spaces)
    """
    base = Path(name).stem
    # Strip product-name suffixes after ' - ' or ' — '
    for sep in (" - ", " — ", "–", "—"):
        if sep in base:
            base = base.split(sep)[0]

    # Strip known prefixes
    words = re.split(r"[ _\-]", base.lower())
    words = [
        w
        for w in words
        if w and w not in DPI_PREFIXES and w not in VARIANT_PREFIXES
    ]
    # Remove standalone numbers (page numbers, CRP codes, etc.)
    words = [w for w in words if not w.isdigit()]
    return " ".join(words).strip()


def similarity(a: str, b: str) -> float:
    """Token-overlap similarity score between 0.0 and 1.0.
    Combines difflib SequenceMatcher with token-set overlap.
    """
    a_norm = normalize_name(a)
    b_norm = normalize_name(b)
    if not a_norm or not b_norm:
        return 0.0

    seq = difflib.SequenceMatcher(None, a_norm, b_norm).ratio()

    a_tokens = set(a_norm.split())
    b_tokens = set(b_norm.split())
    if not a_tokens or not b_tokens:
        return seq

    overlap = len(a_tokens & b_tokens)
    token_sim = overlap / max(len(a_tokens), len(b_tokens))

    # Average of both metrics
    return round((seq + token_sim) / 2, 3)


# ─── ASSET MATCHING ───────────────────────────────────────────────────────


def match_maps_to_scenes(
    scene_names: List[str],
    map_files: List[str],
    maps_dir: Path,
    threshold: float = 0.6,
    scene_aliases: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Fuzzy-match source maps to scene names.

    Prefers 72DPI and gridless variants. Downscales 300DPI maps to 72DPI.
    Returns dict with matched_scenes, unmatched_scenes, summary.

    scene_aliases maps a scene name to extra candidate names to match against
    (e.g. its containing location/region). Published maps are often named after
    regions rather than individual scenes, so a scene inherits its location's
    map when its own name doesn't match anything. The scene's own name is always
    tried first, so existing per-scene matches are unaffected.
    """
    scene_aliases = scene_aliases or {}
    import asyncio  # lazy import for optional image processing

    matched: Dict[str, Dict[str, Any]] = {}
    unmatched: List[str] = []
    warnings: List[str] = []

    def _is_better_match(existing_path: str, candidate_path: str) -> bool:
        """Prefer 72DPI over 300DPI, gridless over gridded."""
        existing_lower = str(existing_path).lower()
        candidate_lower = str(candidate_path).lower()
        # Prefer 72DPI
        if "72" in candidate_lower and "300" in existing_lower:
            return True
        # Prefer gridless
        if "gridless" in candidate_lower and "grid" in existing_lower and "gridless" not in existing_lower:
            return True
        return False

    for scene_name in scene_names:
        best_score = 0.0
        best_file: Optional[str] = None

        # Own name first, then location/region aliases as fallback candidates.
        candidate_names = [scene_name, *scene_aliases.get(scene_name, [])]

        for map_file in map_files:
            map_name = str(Path(map_file).name)
            score = max(similarity(cand, map_name) for cand in candidate_names)
            if score >= threshold:
                if score > best_score or (score == best_score and _is_better_match(best_file, map_file)):
                    best_score = score
                    best_file = map_file

        if best_file:
            safe_name = normalize_name(scene_name)
            dest_name = f"map_{safe_name}.jpg"
            dest_path = maps_dir / dest_name

            # Check if we need downscaling
            original_size = Path(best_file).stat().st_size
            if original_size > MAX_MAP_UPLOAD_BYTES or "300" in str(best_file).lower():
                try:
                    from PIL import Image
                    img = Image.open(best_file)
                    # Downscale to 72/300 (24%) if 300DPI, or to fit under 40MB
                    if "300" in str(best_file).lower():
                        new_w = int(img.width * 0.24)
                        new_h = int(img.height * 0.24)
                    else:
                        # Simple resize to reduce below threshold
                        scale = (MAX_MAP_UPLOAD_BYTES / original_size) ** 0.5 * 0.9
                        new_w = int(img.width * scale)
                        new_h = int(img.height * scale)

                    img_resized = img.resize((max(1, new_w), max(1, new_h)), Image.Resampling.LANCZOS)
                    if img_resized.mode in ("RGBA", "P", "LA"):
                        img_resized = img_resized.convert("RGB")
                    img_resized.save(str(dest_path), "JPEG", quality=85)
                    warnings.append(
                        f"Map '{scene_name}': converted '{Path(best_file).name}' to "
                        f"{new_w}×{new_h} @{dest_path}"
                    )
                except Exception as e:
                    # Fallback: copy original, warn
                    import shutil
                    shutil.copy(best_file, str(dest_path))
                    warnings.append(
                        f"Map '{scene_name}': resize failed ({e}), copied original "
                        f"({original_size} bytes)"
                    )
            else:
                import shutil
                shutil.copy(best_file, str(dest_path))

            # Compute grid from image dimensions (÷64)
            try:
                from PIL import Image
                img = Image.open(str(dest_path))
                grid_w = max(1, img.width // 64)
                grid_h = max(1, img.height // 64)
                width_px, height_px = img.width, img.height
            except Exception:
                grid_w, grid_h = 16, 12  # fallback grid
                width_px, height_px = grid_w * 64, grid_h * 64

            matched[scene_name] = {
                "map_file": str(dest_path),
                "source_file": best_file,
                "map_needed": False,
                "grid_width": grid_w,
                "grid_height": grid_h,
                "grid_size_px": 64,
                "width_px": width_px,
                "height_px": height_px,
                "walls": [],
                "lights": [],
                "sounds": [],
                "score": best_score,
            }
        else:
            unmatched.append(scene_name)

    return {
        "matched_scenes": matched,
        "unmatched_scenes": unmatched,
        "warnings": warnings,
    }


def match_tokens_to_npcs(
    npc_names: List[str],
    token_files: List[str],
    tokens_dir: Path,
    threshold: float = 0.75,
) -> Dict[str, Any]:
    """Match NPC names to source tokens. Prefers CLOSEUP variants.
    Returns dict with matched_npcs, unmatched_npcs, summary.
    """
    matched: Dict[str, Dict[str, Any]] = {}
    unmatched: List[str] = []
    warnings: List[str] = []

    def _is_better_token(existing_path: str, candidate_path: str) -> bool:
        """Prefer CLOSEUP variants; otherwise prefer PNG."""
        cand_lower = str(candidate_path).lower()
        if "closeup" in cand_lower or "close_up" in cand_lower or "close-up" in cand_lower:
            return True
        if Path(candidate_path).suffix.lower() == ".png":
            return True
        return False

    for npc_name in npc_names:
        best_score = 0.0
        best_file: Optional[str] = None

        for token_file in token_files:
            score = similarity(npc_name, str(Path(token_file).name))
            if score >= threshold:
                if score > best_score or (score == best_score and _is_better_token(best_file, token_file)):
                    best_score = score
                    best_file = token_file

        if best_file:
            safe_name = normalize_name(npc_name)
            dest_name = f"token_{safe_name}.png"
            dest_path = tokens_dir / dest_name

            try:
                from PIL import Image
                img = Image.open(best_file)
                if img.mode in ("RGBA", "P", "LA"):
                    img.save(str(dest_path), "PNG")
                else:
                    img = img.convert("RGBA")
                    img.save(str(dest_path), "PNG")
            except Exception as e:
                import shutil
                shutil.copy(best_file, str(dest_path))
                warnings.append(
                    f"Token '{npc_name}': conversion failed ({e}), copied original"
                )

            matched[npc_name] = {
                "portrait_file": str(dest_path),
                "source_file": best_file,
                "portrait_needed": False,
                "score": best_score,
            }
        else:
            unmatched.append(npc_name)

    return {
        "matched_npcs": matched,
        "unmatched_npcs": unmatched,
        "warnings": warnings,
    }


def _normalize_document_name(name: str) -> str:
    """Normalize a Foundry document name for fuzzy matching.

    Deliberately NOT normalize_name(): that uses Path(name).stem, which is
    correct for real filenames but wrongly treats a Foundry document name
    like DDBImporter's 'Map 3.2: Battle of High Hill' as having a file
    extension (the '.2' after '3'), truncating it down to 'Map 3' and
    silently discarding the entire descriptive title it needs to match on.
    """
    text = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    words = [w for w in text.split() if w and not w.isdigit()]
    return " ".join(words)


def _document_similarity(a: str, b: str) -> float:
    """Same token-overlap + SequenceMatcher blend as similarity(), but using
    _normalize_document_name() instead of the file-oriented normalize_name().
    """
    a_norm = _normalize_document_name(a)
    b_norm = _normalize_document_name(b)
    if not a_norm or not b_norm:
        return 0.0

    seq = difflib.SequenceMatcher(None, a_norm, b_norm).ratio()

    a_tokens = set(a_norm.split())
    b_tokens = set(b_norm.split())
    if not a_tokens or not b_tokens:
        return seq

    overlap = len(a_tokens & b_tokens)
    token_sim = overlap / max(len(a_tokens), len(b_tokens))
    return round((seq + token_sim) / 2, 3)


def match_names_to_existing(
    names: List[str], existing: List[Dict[str, str]], threshold: float = 0.6
) -> Dict[str, Any]:
    """Match campaign-generated NPC/scene names to Foundry documents already
    in the world (e.g. Actors/Scenes a DDBImporter sync pre-created for the
    adventure), so deployment can link to them instead of creating
    duplicates. `existing` is a list of {"name", "uuid"} dicts.

    Each existing document is claimed by at most one name (its best match),
    same greedy-best-score approach as match_tokens_to_npcs.
    """
    matched: Dict[str, str] = {}
    unmatched: List[str] = []
    claimed_uuids: Set[str] = set()

    for name in names:
        best_score = 0.0
        best_uuid: Optional[str] = None
        for doc in existing:
            uuid = doc.get("uuid", "")
            if not uuid or uuid in claimed_uuids:
                continue
            score = _document_similarity(name, doc.get("name", ""))
            if score > best_score:
                best_score = score
                best_uuid = uuid
        if best_uuid and best_score >= threshold:
            matched[name] = best_uuid
            claimed_uuids.add(best_uuid)
        else:
            unmatched.append(name)

    return {"matched": matched, "unmatched": unmatched}


def filter_candidates_by_campaign_folder(
    candidates: List[Dict[str, str]], campaign_name: str, threshold: float = 0.4
) -> List[Dict[str, str]]:
    """Narrow existing-document candidates to ones filed under a folder
    matching the campaign name, when such a folder exists.

    A world can have multiple sourcebooks synced in — without this, every
    NPC/scene match (fuzzy or semantic) would be searching across all of
    them, bloating the semantic-match prompt with irrelevant candidates and
    risking a false match against an unrelated book's similarly-named
    content. Falls back to the full candidate list when nothing scores
    above threshold, so a world with only one synced book (or folder
    naming that doesn't line up with campaign_name) doesn't lose every
    real candidate to an over-eager filter.
    """
    scoped = [
        c for c in candidates
        if c.get("folder") and _document_similarity(campaign_name, c["folder"]) >= threshold
    ]
    return scoped if scoped else candidates


def build_semantic_match_prompt(
    kind: str, items: List[Dict[str, Any]], candidates: List[Dict[str, str]]
) -> Tuple[str, str]:
    """Build a system/user prompt asking an LLM to match campaign-generated
    NPCs/scenes to pre-existing Foundry documents by CONTENT, not just name
    text — catching cases a fuzzy string match can't, like a generated
    'Vogler — The Brass Crab' that should map to an existing 'Map 3.1:
    Vogler' despite low text similarity, because it's the same in-world
    location the adventure describes.

    `items` are campaign-generated dicts (need "name", plus whatever
    descriptive fields exist — description/atmosphere/type for scenes,
    description/role/faction for NPCs). `candidates` are existing Foundry
    documents as {"name", "folder"} — uuids are resolved back in Python
    afterward, never shown to the model. Returns (system_prompt, user_prompt).
    """
    noun = "location/scene" if kind == "scene" else "NPC/character"
    system = (
        f"You are matching newly-generated {noun}s from an adventure summary "
        f"against {noun}s that already exist in a FoundryVTT world (pre-built "
        "by an official import — e.g. maps or stat blocks from the published "
        "book). Using the names and any description/folder context given, "
        "decide which existing entry (if any) is the SAME in-world "
        f"{noun} as each generated one — not just similar text, but the same "
        "place or character the adventure is describing.\n\n"
        "Rules:\n"
        f"- Only match when confident it's the same {noun} — a wrong match "
        "is worse than no match.\n"
        "- Each existing entry may be used for at most one generated entry.\n"
        "- If nothing fits, use null.\n"
        "- Respond with ONLY a JSON object: "
        '{"<generated name>": "<existing name or null>", ...}\n'
        "- No commentary before or after the JSON."
    )

    def _describe(item: Dict[str, Any]) -> str:
        parts = [item.get("name", "")]
        for key in ("type", "description", "atmosphere", "role", "faction"):
            val = item.get(key)
            if val:
                parts.append(f"{key}: {val}")
        return " — ".join(str(p) for p in parts if p)

    items_block = "\n".join(f"- {_describe(i)}" for i in items)
    candidates_block = "\n".join(
        f"- {c.get('name', '')}" + (f" (folder: {c['folder']})" if c.get("folder") else "")
        for c in candidates
    )
    user = (
        f"Generated {noun}s needing a match:\n{items_block}\n\n"
        f"Existing {noun}s already in the world:\n{candidates_block}\n\n"
        "Return the JSON mapping now."
    )
    return system, user


def parse_semantic_match_response(text: str) -> Dict[str, Optional[str]]:
    """Parse a semantic-match LLM response into {generated_name: existing_name_or_None}.

    Tolerant of markdown code fences and stray text around the JSON object.
    Returns {} on anything unparseable rather than raising — this match is
    a best-effort layer on top of fuzzy name matching, never something the
    import should fail over.
    """
    if not text:
        return {}
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    result: Dict[str, Optional[str]] = {}
    for k, v in data.items():
        if isinstance(v, str) and v.strip().lower() not in ("null", "none", ""):
            result[k] = v
        else:
            result[k] = None
    return result


# ─── HANDOUT PREPARATION ───────────────────────────────────────────────────


def prepare_handouts(
    handout_pdfs: List[str],
    campaign_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Create journal entries from PDF handouts with preserved source references.
    Returns a list of journal entry dicts ready for the campaign pipeline.

    Variant duplicates (e.g. 'Handouts - Full Color.pdf' and
    'Handouts - Printer Friendly.pdf') normalize to the same title — keep one
    entry per title. Full Color wins for on-screen display; ties break by
    input order (scan sorts alphabetically).
    """
    def _title_for(pdf_path: str) -> str:
        title = Path(pdf_path).stem
        for sep in (" - ", " — ", "–", "—"):
            if sep in title:
                title = title.split(sep)[0]
        return title

    def _variant_rank(pdf_path: str) -> int:
        lower = pdf_path.lower()
        if "full color" in lower or "full_color" in lower or "fullcolor" in lower:
            return 0
        if "printer" in lower:
            return 1
        return 2

    best_by_title: Dict[str, str] = {}
    for pdf_path in handout_pdfs:
        title = _title_for(pdf_path)
        key = title.lower()
        if key not in best_by_title or _variant_rank(pdf_path) < _variant_rank(best_by_title[key]):
            best_by_title[key] = pdf_path

    entries: List[Dict[str, Any]] = []
    for key, pdf_path in best_by_title.items():
        entries.append({
            "title": _title_for(pdf_path),
            "body": "",
            "type": "handout",
            "visible_to_players": False,
            "pdf_file": pdf_path,
            "pdf_src": pdf_path,
        })

    return entries


# ─── PDF EXTRACT + CHUNKING FOR LLM ───────────────────────────────────────


def _pick_preferred_pdf(pdf_paths: List[str]) -> List[str]:
    """If multiple PDFs, return list sorted by preference (Printer_Friendly first)."""
    preferred: List[str] = []
    others: List[str] = []
    for pdf_path in pdf_paths:
        lower = str(pdf_path).lower()
        if "printer_friendly" in lower or "printer friendly" in lower:
            preferred.append(pdf_path)
        else:
            others.append(pdf_path)
    # Return the preferred list if any, otherwise keep the original ordered list
    if preferred:
        preferred.extend(o for o in others if o not in preferred)
        return preferred
    return pdf_paths


def _classify_directory(dir_name_lower: str) -> str:
    """Classify a directory by name into map/token/handout/adventure/generic."""
    if any(kw in dir_name_lower for kw in ("map", "battlemap", "map_pack", "maps")):
        return "maps"
    if any(kw in dir_name_lower for kw in ("token", "tokens", "npc", "portrait", "portraits")):
        return "tokens"
    if any(kw in dir_name_lower for kw in ("handout", "handouts", "reference", "refs", "journal")):
        return "handouts"
    if any(kw in dir_name_lower for kw in ("adventure", "module", "scenario")):
        return "adventure"
    return "generic"


# ─── IMPORT SUMMARY ───────────────────────────────────────────────────────


def build_import_summary(
    scan_result: Dict[str, Any],
    map_match_result: Dict[str, Any],
    token_match_result: Dict[str, Any],
    handout_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Aggregate all matching results into a user-facing summary."""
    matched_maps = list(map_match_result.get("matched_scenes", {}).keys())
    unmatched_maps = map_match_result.get("unmatched_scenes", [])
    matched_tokens = list(token_match_result.get("matched_npcs", {}).keys())
    unmatched_tokens = token_match_result.get("unmatched_npcs", [])

    return {
        "source_path": scan_result.get("source_path", ""),
        "total_files_scanned": scan_result.get("total_files", 0),
        "maps": {
            "found": len(scan_result.get("maps", [])),
            "matched": len(matched_maps),
            "unmatched": len(unmatched_maps),
            "matched_scenes": matched_maps,
            "unmatched_scenes": unmatched_maps,
        },
        "tokens": {
            "found": len(scan_result.get("tokens", [])),
            "matched": len(matched_tokens),
            "unmatched": len(unmatched_tokens),
            "matched_npcs": matched_tokens,
            "unmatched_npcs": unmatched_tokens,
        },
        "handouts": {
            "found": len(scan_result.get("handouts", [])),
            "prepared": len(handout_entries),
        },
        "warnings": (
            map_match_result.get("warnings", [])
            + token_match_result.get("warnings", [])
        ),
    }


# ─── LLM PASS PROMPTS ─────────────────────────────────────────────────────
# Three-pass map→reduce used by CampaignOrchestrator.import_campaign:
#   Pass 1 (per chunk): extract markdown GM notes under fixed headings.
#   Pass 2 (single call): reduce notes → campaign JSON (schema comes from the
#         existing CAMPAIGN_GENERATOR_PROMPT; the orchestrator concatenates it).
#   Pass 3 (single call): notes → Worldbuilding.md + History.md.

PASS1_HEADINGS: Tuple[str, ...] = (
    "World/History",
    "Factions",
    "NPCs",
    "Locations",
    "Scenes",
    "Encounters",
    "Plot Beats",
    "Handouts",
)


def build_pass1_prompt(chunk_text: str) -> str:
    """System prompt for Pass 1: extract structured GM notes from one PDF chunk.

    Extract-only — the model must not invent content that is not in the chunk.
    """
    headings = "\n".join(f"## {h}" for h in PASS1_HEADINGS)
    return (
        "You are extracting GM notes from a published TTRPG adventure so it can "
        "be rebuilt inside FoundryVTT. Read the source pages and produce markdown "
        "notes under EXACTLY these headings, in this order:\n\n"
        f"{headings}\n\n"
        "Rules:\n"
        "- EXTRACT ONLY. Never invent NPCs, locations, scenes, or plot points "
        "that are not present in the source text.\n"
        "- Keep proper nouns (names, places, factions) exactly as written.\n"
        "- Under Scenes: list every distinct encounter area / map-worthy location "
        "by the name the adventure uses for it.\n"
        "- Under Handouts: list any player handout, letter, or read-aloud text.\n"
        "- If a heading has no content in this chunk, write '(none)' under it.\n"
        "- Output markdown only — no commentary before or after."
    )


def build_pass1_user(chunk_text: str) -> str:
    """User prompt for Pass 1: the raw chunk text to extract from."""
    return (
        "Extract the GM notes from this adventure excerpt:\n\n"
        f"{chunk_text}"
    )


_PASS2_SYSTEM = (
    "You are converting extracted GM notes from a published adventure into the "
    "campaign JSON structure used by this system. Follow the schema below "
    "EXACTLY.\n\n"
    "Rules specific to imported material:\n"
    "- Content counts come from the SOURCE NOTES, not from the generic targets: "
    "include every named NPC, location, scene, and plot beat found in the notes, "
    "and do not pad with invented content to hit a number.\n"
    "- Stay faithful to the source lore — names, factions, relationships, and "
    "history must match the notes.\n"
    "- Set map_needed=true for every scene that would benefit from a battle map; "
    "the importer will clear the flag for scenes it can match to a pre-made map.\n"
    "- scene_setup may be omitted on scenes — it is auto-filled during validation.\n\n"
)


def build_pass2_user(combined_notes: str, campaign_name: str, level_range: str) -> str:
    """User prompt for Pass 2: reduce all pass-1 notes into one campaign JSON."""
    return (
        f"Build the campaign '{campaign_name}' (level range {level_range}) from "
        "these extracted GM notes. Respond with the SINGLE campaign JSON object "
        "only.\n\n"
        f"{combined_notes}"
    )


def build_pass2_chapter_user(
    combined_notes: str,
    campaign_name: str,
    level_range: str,
    chapter_label: str,
    existing_names: Dict[str, List[str]],
) -> str:
    """User prompt for one chapter's Pass 2 call, when a multi-chapter
    published adventure is imported chapter-by-chapter instead of as one
    combined whole-book call.

    A single Pass 2 call over an entire multi-chapter book's notes reliably
    produced only ~3-5 scenes regardless of how much source material went
    in — the verbose campaign JSON schema this system uses doesn't fit a
    whole 7-chapter campaign in one response, and the model defaults to a
    short-arc-sized result rather than exhaustively enumerating everything.
    Running one full generate+merge cycle per chapter (mirroring
    extend_campaign_arc's existing generate-then-merge pattern, but staying
    strictly extract-only like a single-shot import — never escalating or
    inventing new content the way a from-scratch arc extension deliberately
    does) gives each chapter its own full token budget.

    existing_names is {label: [name, ...]} for content already extracted
    from earlier chapters (scenes/NPCs/locations so far), so this chapter
    doesn't recreate or contradict them.
    """
    existing_lines = [
        f"- Existing {label} (do not repeat): {', '.join(names)}"
        for label, names in existing_names.items()
        if names
    ]
    existing_block = (
        "Content already extracted from earlier chapters:\n" + "\n".join(existing_lines) + "\n\n"
        if existing_lines
        else ""
    )
    return (
        f"Build the '{chapter_label}' section of campaign '{campaign_name}' "
        f"(level range {level_range}) from these extracted GM notes. This is ONE "
        "chapter of a larger published adventure being imported chapter-by-chapter "
        "— respond with the SINGLE campaign JSON object containing ONLY this "
        "chapter's new scenes/NPCs/locations/quests/encounters/loot (same schema "
        "as a full campaign). Do not recreate or contradict content from earlier "
        "chapters.\n\n"
        f"{existing_block}"
        f"{combined_notes}"
    )


_PASS3_SYSTEM = (
    "You are writing world lore documents for a TTRPG campaign imported from a "
    "published adventure. From the extracted GM notes, write TWO markdown "
    "documents:\n\n"
    "1. WORLDBUILDING — the setting itself: geography, factions, peoples, "
    "religion, magic, important NPCs and their relationships.\n"
    "2. HISTORY — the world's history and the events leading up to the "
    "adventure's present.\n\n"
    "Rules:\n"
    "- EXTRACT/REPHRASE ONLY from the notes. Do not invent new lore.\n"
    "- These documents will be indexed for semantic search at runtime — prefer "
    "specific names and facts over prose flourishes.\n"
    "- Output format is STRICT. Emit exactly:\n"
    "===WORLDBUILDING===\n"
    "<worldbuilding markdown>\n"
    "===HISTORY===\n"
    "<history markdown>\n"
    "===END===\n"
    "- No other text before ===WORLDBUILDING=== or after ===END===."
)


def build_pass3_user(combined_notes: str) -> str:
    """User prompt for Pass 3: the combined pass-1 notes to summarize."""
    return (
        "Write the Worldbuilding and History documents from these GM notes:\n\n"
        f"{combined_notes}"
    )


def parse_pass3_response(text: str) -> Tuple[str, str]:
    """Split a Pass 3 response into (worldbuilding_md, history_md).

    Primary contract: ===WORLDBUILDING=== / ===HISTORY=== / ===END=== markers.
    Tolerant fallbacks: markdown '# Worldbuilding' / '# History' headings, then
    'everything is worldbuilding, no history'.
    """
    if not text:
        return "", ""

    # Primary: explicit markers
    if "===WORLDBUILDING===" in text:
        wb_part, _, rest = text.partition("===WORLDBUILDING===")
        wb, _, rest = rest.partition("===HISTORY===")
        hist, _, _ = rest.partition("===END===")
        return wb.strip(), hist.strip()

    # Fallback: markdown headings
    wb_match = re.search(r"^#\s+Worldbuilding\b", text, flags=re.IGNORECASE | re.MULTILINE)
    hist_match = re.search(r"^#\s+History\b", text, flags=re.IGNORECASE | re.MULTILINE)
    if wb_match and hist_match:
        if wb_match.start() < hist_match.start():
            return (
                text[wb_match.start():hist_match.start()].strip(),
                text[hist_match.start():].strip(),
            )
        return (
            text[wb_match.start():].strip(),
            text[hist_match.start():wb_match.start()].strip(),
        )

    # Last resort: whole response is worldbuilding
    return text.strip(), ""
