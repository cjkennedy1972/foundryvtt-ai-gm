"""
Campaign Context Loader — reads campaign data from the Obsidian vault
and makes it available to the AI GM's system prompt.
"""

import asyncio
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from config import settings
from utils.path_safety import validate_contained_path

logger = logging.getLogger(__name__)


class CampaignLoader:
    """Loads and caches campaign data from the Obsidian vault."""

    # Shared reference files loaded for every campaign
    SHARED_FILES = [
        "DnD_SRD_v5.2.1_Full_Text.txt",
        "DM_Reference.md",
        "Dungeons_and_Dragons.md",
    ]

    def __init__(self, vault_path: str = None):
        self.vault_path = vault_path or settings.campaign_vault_path
        self._data: Dict[str, str] = {}
        self._loaded_campaign: str = ""
        self._srd_chunks: List[str] = []

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
        return self._data

    def register_vault_npcs(self, npc_registry) -> int:
        """Parse loaded campaign files and register named NPCs in the personality registry.

        Scans every file whose path contains 'NPC' (case-insensitive) for
        Markdown headings (## Name) and bold-name patterns (**Name:**) and
        registers each as an NPCRecord so they get persistent TTS voices and
        can have personality data injected during combat.

        Returns the number of newly registered NPCs.
        """
        import re
        registered = 0
        seen: set = set()

        heading_re = re.compile(r"^#{1,3}\s+(.+)", re.MULTILINE)
        bold_re = re.compile(r"^\*\*([^*:]+):\*\*", re.MULTILINE)

        for key, content in self._data.items():
            if "npc" not in key.lower():
                continue
            # Try headings first (most specific), then bold-name patterns
            names = heading_re.findall(content) or bold_re.findall(content)
            for raw in names:
                name = raw.strip().strip("*").strip()
                # Skip generic section headings
                if not name or len(name) > 60 or name.lower() in (
                    "overview", "npcs", "act i npcs", "act ii npcs", "act iii npcs",
                    "key npcs", "allies", "enemies", "antagonists", "summary",
                ):
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
            logger.info(f"[NPC] Registered {registered} vault NPCs in personality registry")
        return registered

    def _chunk_text(self, text: str, target_tokens: int = 500) -> List[str]:
        """Split text into token-aware chunks for context window management.

        Uses a ~6 char/token ratio as a rough estimate to stay within
        token budgets while breaking at paragraph boundaries.
        """
        char_budget = target_tokens * 6  # Rough char/token ratio for English
        chunks = []
        start = 0
        while start < len(text):
            end = start + char_budget
            if end < len(text):
                # Break at paragraph boundary for clean context
                next_break = text.find("\n\n", end)
                if next_break != -1 and next_break < end + 200:
                    end = next_break + 2
                else:
                    next_break = text.find("\n", end)
                    if next_break != -1:
                        end = next_break + 1
            chunks.append(text[start:end].strip())
            start = end
        return chunks

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
        campaign_dir = vault_path / name
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

        campaign_file = campaigns_dir / f"{name}.json"
        await asyncio.to_thread(
            campaign_file.write_text, json.dumps(data, indent=2), encoding="utf-8"
        )
        logger.info(f"Saved campaign: {name}")
        return True
