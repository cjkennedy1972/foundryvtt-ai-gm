"""
Campaign Context Loader — reads campaign data from the Obsidian vault
and makes it available to the AI GM's system prompt.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)


class CampaignLoader:
    """Loads and caches campaign data from the Obsidian vault."""

    # Files to load for the Aethelwyrd campaign
    DEFAULT_CAMPAIGN_FILES = [
        "Dungeons_and_Dragons/Worldbuilding.md",
        "Dungeons_and_Dragons/Aethelwyrd Campaign State.md",
        "Dungeons_and_Dragons/Act I - The Shattered Sky.md",
        "Dungeons_and_Dragons/NPCs - Act I.md",
        "Dungeons_and_Dragons/Character Hooks.md",
        "Dungeons_and_Dragons/DnD SRD_v5.2.1_Full_Text.txt",
        "Dungeons_and_Dragons/DM_Reference.md",
        "Dungeons_and_Dragons/Dungeons_and_Dragons.md",
    ]

    def __init__(self, vault_path: str = None):
        self.vault_path = vault_path or settings.campaign_vault_path
        self._data: Dict[str, str] = {}
        self._srd_chunks: List[str] = []

    def resolve_path(self) -> Path:
        """Resolve the vault path, handling ~ expansion."""
        path = Path(self.vault_path).expanduser()
        return path

    async def load(self, campaign_name: str = "Aethelwyrd") -> Dict[str, str]:
        """Load campaign files and return as dict of name->content."""
        if campaign_name == "Aethelwyrd" and self._data:
            return self._data  # Already loaded

        vault_path = self.resolve_path()
        if not vault_path.exists():
            logger.warning(f"Vault path not found: {vault_path}")
            return {}

        for filename in self.DEFAULT_CAMPAIGN_FILES:
            file_path = vault_path / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                # Use a consistent key
                key = filename.split("/")[-1].replace(".md", "").replace(".txt", "")
                self._data[key] = content

                # Chunk the SRD text for retrieval (token-aware, ~500 tokens/chunk)
                if "SRD" in filename:
                    self._srd_chunks = self._chunk_text(content)

        logger.info(f"Loaded {len(self._data)} campaign files from {vault_path}")
        return self._data

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

    async def load_custom_campaign(self, files: List[str]) -> Dict[str, str]:
        """Load a custom set of campaign files."""
        vault_path = self.resolve_path()
        for filepath in files:
            file_path = vault_path / filepath
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                key = file_path.name
                self._data[key] = content
                logger.info(f"Loaded custom file: {key}")
            else:
                logger.warning(f"Custom file not found: {filepath}")
        return self._data

    async def save_campaign(self, name: str, data: Dict[str, str]) -> bool:
        """Save a campaign configuration."""
        campaigns_dir = Path(__file__).parent.parent / "campaigns"
        campaigns_dir.mkdir(exist_ok=True)

        campaign_file = campaigns_dir / f"{name}.json"
        campaign_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info(f"Saved campaign: {name}")
        return True
