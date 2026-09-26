"""
Campaign Context Loader — reads campaign data from the Obsidian vault
and makes it available to the AI GM's system prompt.
"""

import asyncio
import math
import os
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from config import settings
from utils.path_safety import sanitize_filename, validate_contained_path

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#{1,3}\s+.+$", re.MULTILINE)
_WORD_RE = re.compile(r"\w+")


# Headings that title a section rather than name a character. Needed for the
# multi-NPC layout, where the characters are ## headings and their own
# subsections are ### headings at the same depth the regex matches. A note
# that holds ONE NPC does not rely on this list — its title names the
# character whatever its sections are called.
SECTION_HEADINGS = frozenset({
    "abilities", "act i npcs", "act ii npcs", "act iii npcs", "allies",
    "antagonists", "appearance", "background", "description", "enemies",
    "first appearance", "goals", "history", "hooks", "inventory",
    "key npcs", "motivations", "notes", "npcs", "overview", "personality",
    "quotes", "relationships", "secrets", "stat block", "summary",
    "tactics", "voice",
})


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())


def _bm25_rank(query: str, documents: List[str], max_results: int,
               k1: float = 1.5, b: float = 0.75) -> List[int]:
    """Rank documents against a query with BM25. Returns indices of the top
    max_results documents with a nonzero score, best first.

    Pure Python, no dependencies: a campaign vault is a few hundred lore
    chunks at most, far too small to justify a vector index or an embedding
    model, and BM25's term-rarity weighting handles this proper-noun-heavy
    content (NPC/place names) better than naive keyword-overlap counting.
    """
    if not documents or not query.strip():
        return []
    tokenized_docs = [_tokenize(doc) for doc in documents]
    doc_lens = [len(d) for d in tokenized_docs]
    avgdl = sum(doc_lens) / len(doc_lens) if doc_lens else 0
    n_docs = len(documents)

    df: Dict[str, int] = {}
    for doc in tokenized_docs:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1

    scores = [0.0] * n_docs
    for term in set(_tokenize(query)):
        n_qi = df.get(term, 0)
        if n_qi == 0:
            continue
        idf = math.log((n_docs - n_qi + 0.5) / (n_qi + 0.5) + 1)
        for i, doc in enumerate(tokenized_docs):
            f = doc.count(term)
            if f == 0:
                continue
            denom = f + k1 * (1 - b + b * doc_lens[i] / avgdl) if avgdl else f
            scores[i] += idf * (f * (k1 + 1)) / denom

    ranked = sorted(range(n_docs), key=lambda i: scores[i], reverse=True)
    return [i for i in ranked[:max_results] if scores[i] > 0]


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CampaignLoader:
    """Loads and caches campaign data from the Obsidian vault."""

    # Shared reference files loaded for every campaign
    SHARED_FILES = [
        "DnD_SRD_v5.2.1_Full_Text.txt",
        "DM_Reference.md",
        "Dungeons_and_Dragons.md",
    ]

    def __init__(self, vault_path: str = None, semantic_indexer=None):
        self.vault_path = vault_path or settings.campaign_vault_path
        self._data: Dict[str, str] = {}
        # campaign.json for the loaded campaign: the structured source the
        # Markdown is rendered from. Held so callers read fields off it rather
        # than parsing them back out of the render.
        self._campaign_data: Dict[str, Any] = {}
        self._loaded_campaign: str = ""
        self._srd_chunks: List[str] = []
        # (source_key, chunk_text) for campaign-specific lore — excludes the
        # shared reference files (SRD/DM reference), which have their own
        # dedicated search_srd() path for rules lookups.
        self._vault_chunks: List[Tuple[str, str]] = []
        self._semantic_indexer = semantic_indexer

    @property
    def current_campaign_name(self) -> str:
        """The campaign whose vault files are currently loaded, "" if none.

        Public read accessor for `_loaded_campaign`, which api/routes/campaign.py
        needs to answer "which campaign is loaded right now?".
        """
        return self._loaded_campaign

    def resolve_path(self) -> Path:
        """Resolve the vault path, handling ~ expansion."""
        path = Path(self.vault_path).expanduser()
        return path

    async def load(self, campaign_name: str = "") -> Dict[str, str]:
        """Load campaign files and return as dict of name->content.

        Loads shared reference files plus all .md files found in the
        campaign's own subfolder (if campaign_name is provided).
        """
        if campaign_name and campaign_name == self._loaded_campaign and self._data:
            return self._data

        self._data = {}
        self._campaign_data = {}
        self._loaded_campaign = campaign_name
        vault_path = self.resolve_path()
        if not vault_path.exists():
            logger.warning(f"Vault path not found: {vault_path}")
            return {}

        # Load shared reference files
        for filename in self.SHARED_FILES:
            file_path = vault_path / filename
            if file_path.exists():
                content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
                key = filename.split("/")[-1].replace(".md", "").replace(".txt", "")
                self._data[key] = content
                if "SRD" in filename:
                    self._srd_chunks = self._chunk_text(content)

        # Load campaign-specific files from the vault's Campaigns/<safe_name>/
        # folder (and all its subfolders: Story/, NPCs/, Quests/, ...).
        # Resolved via the same helper the deploy pipeline uses so the
        # sanitized folder name (e.g. ":" -> "_") matches on disk.
        campaign_files = 0
        if campaign_name:
            from campaign.obsidian_sync import get_campaign_folder
            campaign_dir = get_campaign_folder(vault_path, campaign_name)
            if not campaign_dir.is_dir():
                # Fall back to the legacy flat layout <vault>/<campaign_name>/
                campaign_dir = vault_path / campaign_name
            if campaign_dir.is_dir():
                for file_path in sorted(campaign_dir.rglob("*.md")):
                    try:
                        content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
                    except (UnicodeDecodeError, OSError) as read_err:
                        logger.warning(f"Skipping {file_path.name}: {read_err}")
                        continue
                    # Use the path relative to the campaign dir as the key so
                    # files with the same stem in different subfolders (e.g.
                    # NPCs/Index.md vs Quests/Index.md) don't clobber each other.
                    rel = file_path.relative_to(campaign_dir).with_suffix("")
                    self._data[str(rel)] = content
                    campaign_files += 1

                manifest = campaign_dir / "campaign.json"
                if manifest.exists():
                    try:
                        raw = await asyncio.to_thread(manifest.read_text, encoding="utf-8")
                        loaded = json.loads(raw)
                        if isinstance(loaded, dict):
                            self._campaign_data = loaded
                    except (json.JSONDecodeError, OSError) as manifest_err:
                        logger.warning(f"Could not read {manifest}: {manifest_err}")
            else:
                logger.warning(
                    f"Campaign folder not found for {campaign_name!r}: tried "
                    f"{get_campaign_folder(vault_path, campaign_name)} and "
                    f"{vault_path / campaign_name}"
                )

        logger.info(
            f"Loaded {len(self._data)} files for campaign={campaign_name!r} "
            f"({campaign_files} campaign-specific) from {vault_path}"
        )
        self._build_vault_index()
        await self._index_semantic()
        return self._data

    async def _index_semantic(self) -> None:
        """Embed the campaign's lore chunks into the semantic index that
        SemanticRAG queries each turn. Nothing else ever filled it, so vault
        lore retrieval always came back empty. Chunks already in the index
        (it persists to disk) are skipped, so a reload only embeds new text."""
        if not self._semantic_indexer or not self._vault_chunks:
            return
        known = set(self._semantic_indexer.chunks)
        new = [(src, text) for src, text in self._vault_chunks if text not in known]
        if not new:
            return
        try:
            await self._semantic_indexer.add_chunks([t for _, t in new], [s for s, _ in new])
        except Exception as e:
            logger.warning(f"Semantic indexing of campaign lore failed: {e}")

    def _build_vault_index(self) -> None:
        """Chunk every loaded campaign-lore file for search_vault().

        Excludes the shared reference files (SRD/DM reference/core rules) —
        those are rules text, not campaign lore, and already have their own
        search_srd() path.
        """
        shared_keys = {
            f.split("/")[-1].replace(".md", "").replace(".txt", "")
            for f in self.SHARED_FILES
        }
        self._vault_chunks = []
        for key, content in self._data.items():
            if key in shared_keys:
                continue
            for chunk in self._chunk_by_headings(content):
                self._vault_chunks.append((key, chunk))

    def register_vault_npcs(self, npc_registry) -> int:
        """Register this campaign's NPCs in the personality registry.

        Reads campaign.json, which is what the Markdown notes are rendered
        from. This used to regex the render instead, which cost every field
        the render drops (alignment, disposition, personality) and handed the
        registry a 400-character slab of raw Markdown as the description.

        Falls back to scanning the notes when there is no campaign.json, for
        hand-made and legacy vaults that only ever had prose.

        Returns the number of newly registered NPCs.
        """
        npcs = self._campaign_data.get("npcs")
        if isinstance(npcs, list) and npcs:
            return self._register_npcs_from_manifest(npcs, npc_registry)
        return self._register_npcs_from_notes(npc_registry)

    @staticmethod
    def _register_npcs_from_manifest(npcs: List[Dict[str, Any]], npc_registry) -> int:
        import re

        registered = 0
        for npc in npcs:
            if not isinstance(npc, dict):
                continue
            name = str(npc.get("name", "")).strip()
            if not name or npc_registry.get_npc_by_name(name) is not None:
                continue

            record = npc_registry.register_npc(
                npc_id=re.sub(r"[^a-z0-9_]", "_", name.lower()),
                npc_name=name,
                description=str(npc.get("description", "")),
                alignment=npc.get("alignment") or None,
                disposition=_as_float(npc.get("disposition"), 0.0),
            )
            # NPCRecord.personality is {category: [trait, ...]}; the manifest
            # carries a flat list.
            traits = npc.get("personality")
            if isinstance(traits, list) and traits:
                npc_registry.set_npc_personality(record.npc_id, {"traits": [str(t) for t in traits]})
            elif isinstance(traits, dict) and traits:
                npc_registry.set_npc_personality(record.npc_id, traits)

            for note in ("role", "faction", "first_appearance"):
                if npc.get(note):
                    record.notes.append(f"{note.replace('_', ' ').title()}: {npc[note]}")
            registered += 1

        if registered:
            logger.info(f"[NPC] Registered {registered} NPCs from campaign.json")
        return registered

    def _register_npcs_from_notes(self, npc_registry) -> int:
        """Legacy path: recover names from the Markdown when no manifest exists."""
        import re
        registered = 0
        seen: set = set()

        heading_re = re.compile(r"^#{1,3}\s+(.+)", re.MULTILINE)
        title_re = re.compile(r"^#\s+(.+)", re.MULTILINE)
        bold_re = re.compile(r"^\*\*([^*:]+):\*\*", re.MULTILINE)

        def is_section(text: str) -> bool:
            return text.strip().strip("*").strip().lower() in SECTION_HEADINGS

        for key, content in self._data.items():
            if "npc" not in key.lower():
                continue
            # Two layouts have to work here. One note per NPC, where the single
            # title IS the character and every ## below it is a section of
            # their sheet — take the title alone, or the note's own
            # "## Description" and "## Stat Block" register as characters. And
            # one note listing many NPCs, where the title is a section label
            # like "Key NPCs" and the ## headings are the characters — scan
            # everything, as before.
            titles = title_re.findall(content)
            if len(titles) == 1 and not is_section(titles[0]):
                names = titles
            else:
                names = heading_re.findall(content) or bold_re.findall(content)
            for raw in names:
                name = raw.strip().strip("*").strip()
                if not name or len(name) > 60 or is_section(name):
                    continue
                if name in seen:
                    continue
                seen.add(name)

                # Extract a short description from the text block after the heading
                idx = content.find(raw)
                snippet = content[idx : idx + 400].strip()

                npc_id = re.sub(r"[^a-z0-9_]", "_", name.lower())
                if npc_registry.get_npc_by_name(name) is None:
                    npc_registry.register_npc(
                        npc_id=npc_id,
                        npc_name=name,
                        description=snippet,
                    )
                    registered += 1

        if registered:
            logger.info(f"[NPC] Registered {registered} vault NPCs by scanning notes")
        return registered

    def _chunk_text(self, text: str, target_tokens: int = 500) -> List[str]:
        """Split text into token-aware chunks for context window management.

        Uses a ~6 char/token ratio as a rough estimate to stay within
        token budgets while breaking at paragraph boundaries.
        """
        char_budget = target_tokens * 6  # Rough char/token ratio for English
        window = 200  # how far from the budget a break is still worth taking
        chunks = []
        start = 0
        while start < len(text):
            end = start + char_budget
            if end < len(text):
                # Break at a paragraph boundary for clean context, else a line
                # ending, else hard-split on the budget.
                #
                # Both directions: this only searched forward, so a paragraph
                # break a few characters BEFORE the budget was ignored and the
                # chunk cut mid-sentence into the next paragraph instead. And
                # the line-ending search was unbounded, so a note with one long
                # line produced a single 40,000-character chunk against a 3,000
                # budget — which also skews avgdl and misprices every other
                # chunk in the BM25 ranking.
                for sep, offset in (("\n\n", 2), ("\n", 1)):
                    back = text.rfind(sep, max(start, end - window), end)
                    forward = text.find(sep, end, end + window)
                    breaks = [p + offset for p in (back, forward) if p != -1]
                    if breaks:
                        end = min(breaks, key=lambda p: abs(p - end))
                        break
            chunks.append(text[start:end].strip())
            start = end
        return chunks

    def _chunk_by_headings(self, text: str, target_tokens: int = 150) -> List[str]:
        """Split markdown into sections at ## headings, so one NPC/location/
        quest stays a single retrievable chunk instead of being cut mid-thought
        by a fixed character budget. Falls back to _chunk_text (paragraph-
        boundary splitting) for headingless text and for any section still
        oversized after the heading split.

        target_tokens defaults much smaller than _chunk_text's SRD default
        (150 vs 500): these chunks get rendered as single-line "anchor fact"
        bullets, not verbose rules references, so shorter is more useful.
        """
        positions = [m.start() for m in _HEADING_RE.finditer(text)]
        if not positions:
            return self._chunk_text(text, target_tokens=target_tokens)

        sections = []
        if positions[0] > 0:
            lead = text[: positions[0]].strip()
            if lead:
                sections.append(lead)
        for i, start in enumerate(positions):
            end = positions[i + 1] if i + 1 < len(positions) else len(text)
            sections.append(text[start:end].strip())

        char_budget = target_tokens * 6
        chunks = []
        for section in sections:
            if not section:
                continue
            if len(section) > char_budget * 1.5:
                chunks.extend(self._chunk_text(section, target_tokens=target_tokens))
            else:
                chunks.append(section)
        return chunks

    def search_vault(self, query: str, max_results: int = 5) -> List[str]:
        """Retrieve the most relevant campaign-lore chunks for a query via BM25.

        Scoped to campaign-specific lore (world notes, NPCs, quests,
        locations) — rules lookups have their own search_srd() path.
        """
        if not self._vault_chunks:
            return []
        texts = [text for _source, text in self._vault_chunks]
        top = _bm25_rank(query, texts, max_results)
        return [f"[{self._vault_chunks[i][0]}] {self._vault_chunks[i][1]}" for i in top]

    @property
    def srd_chunks(self) -> List[str]:
        return self._srd_chunks

    async def search_srd(self, query: str, max_results: int = 3) -> str:
        """Simple keyword search through the SRD chunks."""
        query_lower = query.lower()
        scores = []
        keywords = query_lower.split()

        for i, chunk in enumerate(self._srd_chunks):
            chunk_lower = chunk.lower()
            score = sum(1 for kw in keywords if kw in chunk_lower)
            if score > 0:
                scores.append((score, i, chunk))

        scores.sort(reverse=True)
        selected = [chunk for _, _, chunk in scores[:max_results]]

        if selected:
            return "## SRD Reference ##\n" + "\n---\n".join(selected)
        return ""

    async def get_npc_context(self) -> str:
        """Extract NPC context from loaded files."""
        return self.get_npc_context_sync()

    def get_npc_context_sync(self) -> str:
        """Synchronous version of get_npc_context for use in system prompt."""
        for key, content in self._data.items():
            if "NPC" in key:
                return f"## Act I NPCs ##\n{content}"
        return ""

    async def get_world_context(self) -> str:
        """Extract worldbuilding context from loaded files."""
        return self.get_world_context_sync()

    def get_world_context_sync(self) -> str:
        """Synchronous version of get_world_context for use in system prompt."""
        for key, content in self._data.items():
            if "Worldbuilding" in key or "World" in key:
                return f"## Worldbuilding ##\n{content}"
        return ""

    def get_house_rules_context_sync(self) -> str:
        """Synchronous version of get_house_rules_context for use in system prompt."""
        for key, content in self._data.items():
            # Match "HOUSE_RULES", "HouseRules", "house-rules", etc.
            normalized = key.lower().replace("_", "").replace("-", "")
            if normalized == "houserules" or "houserules" in normalized:
                return f"## House Rules ##\n{content}"
        return ""

    def get_canon_context_sync(self) -> str:
        """Synchronous version of get_canon_context for use in system prompt."""
        for key, content in self._data.items():
            if "Canon" in key:
                return f"## Canon / Established Facts ##\n{content}"
        return ""

    def get_scene_briefing(self, scene_name: str) -> str:
        """Return the authored description/atmosphere for a scene, for per-turn
        grounding.

        The GM is a text model and cannot see the map image, so without this it
        narrates off the campaign *title* and drifts (e.g. narrating a "library
        of flesh" while standing on an authored wilderness-cave map). This pulls
        the scene's own vault file so narration matches the map that is actually
        displayed. Returns "" for improvised/generated scenes with no vault file.
        """
        if not scene_name:
            return ""
        want = scene_name.strip().lower()
        # Prefer the scene-specific Story file, then a Locations file, then any
        # loaded file whose name contains the scene name.
        best = None
        for key, content in self._data.items():
            kl = key.lower()
            if not (kl.startswith("story/") or kl.startswith("locations/")):
                continue
            base = key.split("/")[-1].lower()
            # "Scene - The Whispering Caves Entrance" or "The Whispering Caves"
            if base == want or base == f"scene - {want}" or want in base:
                # Story/Scene file is the most specific — take it and stop.
                if kl.startswith("story/scene - "):
                    best = content
                    break
                best = best or content
        if not best:
            return ""
        # Drop the "## Map" section — that's the image-gen prompt, not narration
        # the GM should read aloud — and the leading tags line.
        lines = []
        skip = False
        for line in best.splitlines():
            if line.strip().lower().startswith("## map"):
                skip = True
                continue
            if line.startswith("## "):
                skip = False
            if skip or line.strip().startswith("tags:"):
                continue
            lines.append(line)
        briefing = "\n".join(lines).strip()
        return briefing

    def get_encounter_context_for_scene(self, scene_name: str) -> str:
        """Return encounter briefs whose linked scene matches scene_name.

        Reads the loaded Encounters.md (key "Encounters"), splits on `---`
        separators, and returns only the sections whose **Scene:** line matches.
        Returns an empty string when no encounters are relevant.
        """
        raw = self._data.get("Encounters", "")
        if not raw or not scene_name:
            return ""

        # Split into per-encounter blocks (separator written by save_encounter_notes)
        blocks = [b.strip() for b in raw.split("---") if b.strip()]
        matched = []
        for block in blocks:
            # Match lines like "**Scene:** The Sunken Crypt"
            for line in block.splitlines():
                if line.startswith("**Scene:**"):
                    scene_val = line.replace("**Scene:**", "").strip()
                    if scene_val.lower() == scene_name.lower():
                        matched.append(block)
                    break

        if not matched:
            return ""

        header = (
            "## Encounter Briefs for This Scene\n"
            "Pre-staged hidden tokens are already on the map. "
            "Watch for trigger conditions in player actions and dialogue.\n\n"
        )
        return header + "\n\n---\n\n".join(matched)

    async def get_campaign_state(self) -> str:
        """Extract current campaign state."""
        for key, content in self._data.items():
            if "State" in key or "Campaign" in key:
                return f"## Campaign State ##\n{content}"
        return ""

    async def get_character_hooks(self) -> str:
        """Extract character hooks/backstory."""
        for key, content in self._data.items():
            if "Character" in key or "Hook" in key:
                return f"## Character Hooks ##\n{content}"
        return ""

    async def get_dm_reference(self) -> str:
        """Extract DM reference notes."""
        for key, content in self._data.items():
            if "DM" in key or "Reference" in key:
                return f"## DM Reference ##\n{content}"
        return ""

    async def get_session_plan(self) -> str:
        """Extract session plan."""
        for key, content in self._data.items():
            if "Session" in key or "Shattered Dawn" in key:
                return f"## Session Plan ##\n{content}"
        return ""

    def get_all_loaded_data(self) -> Dict[str, str]:
        """Return all loaded campaign data."""
        return dict(self._data)

    async def load_custom_campaign(
        self, name: str, files: List[str]
    ) -> Dict[str, Any]:
        """Load a custom set of campaign files into a campaign-specific subfolder.

        Creates a subfolder under the vault at:
            <vault>/<CampaignName>/
        Copies (via symlink) the selected source files into that subfolder
        so Obsidian can still see the original files and any edits the GM
        makes inside the campaign folder propagate back.

        All file paths are validated to ensure they stay within the vault
        directory (prevents path traversal attacks).

        Returns a summary dict with the campaign folder path and loaded files.
        """
        vault_path = self.resolve_path()
        # `name` arrives straight from CampaignCreate.name on POST
        # /api/campaign/load. Every other campaign-name path in the tree
        # sanitizes (campaign/vault.py, campaign/obsidian_sync.py); this one
        # did not, so "../../../../tmp/pwn" created that directory and dropped
        # vault files into it. The source paths below were already validated,
        # so the hole was the destination, not the content.
        campaign_dir = validate_contained_path(
            sanitize_filename(name), str(vault_path), allow_absolute=False
        )
        campaign_dir.mkdir(exist_ok=True)

        linked: List[str] = []
        for filepath in files:
            # Strip known vault prefixes to handle mismatched paths
            stripped = filepath
            for prefix in ("Dungeons_and_Dragons/", "Vaults/MyStuff/games/Dungeons_and_Dragons/", "~/Vaults/MyStuff/games/Dungeons_and_Dragons/"):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
                    break

            # Validate that the resolved path stays within the vault
            try:
                src = validate_contained_path(stripped, str(vault_path))
            except ValueError as e:
                logger.warning(f"Rejected unsafe file path: {filepath} — {e}")
                continue

            if not src.exists():
                logger.warning(f"Source file not found: {filepath} (tried {src})")
                continue

            dest = campaign_dir / src.name
            # Overwrite or create symlink
            try:
                dest.symlink_to(src.resolve())
                logger.info(f"Linked {src.name} → {src.resolve()}")
            except OSError:
                # Symlink already exists or not supported – copy as fallback
                src_bytes = await asyncio.to_thread(src.read_bytes)
                await asyncio.to_thread(dest.write_bytes, src_bytes)
                logger.info(f"Copied {src.name} into campaign folder")

            linked.append(src.name)
            # Also read into in-memory cache
            content = await asyncio.to_thread(dest.read_text, encoding="utf-8")
            self._data[dest.name] = content

        return {"status": "ok", "name": name, "folder": str(campaign_dir), "linked_files": linked}

    async def save_campaign(self, name: str, data: Dict[str, str]) -> bool:
        """Save a campaign configuration."""
        campaigns_dir = Path(__file__).parent.parent / "campaigns"
        campaigns_dir.mkdir(exist_ok=True)

        campaign_file = validate_contained_path(
            f"{sanitize_filename(name)}.json", str(campaigns_dir), allow_absolute=False
        )
        await asyncio.to_thread(
            campaign_file.write_text, json.dumps(data, indent=2), encoding="utf-8"
        )
        logger.info(f"Saved campaign: {name}")
        return True
