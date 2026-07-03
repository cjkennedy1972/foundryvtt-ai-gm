"""Single access point for campaign persistence.

Wraps the two files every lifecycle operation touches:
- <vault>/<campaign>/campaign.json      — the campaign definition (LLM output
  plus asset references added by build/regenerate)
- campaign_assets/<safe>/deployment_state.json — UUIDs of deployed entities

Before this existed, the load/normalize/save dance was duplicated across
main.py endpoints and the orchestrator; the July 2026 "maps lost on resume"
bug was a direct result (one copy saved before assets existed).
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from campaign.obsidian_sync import get_campaign_folder, resolve_vault_path
from config import settings
from utils.path_safety import sanitize_filename


class CampaignNotFound(FileNotFoundError):
    """Raised when a campaign has no campaign.json in the vault."""


class CampaignStore:
    """Load/save one campaign's vault data and deployment state."""

    def __init__(self, campaign_name: str, vault_path: str = None):
        self.name = campaign_name
        self.vault = resolve_vault_path(vault_path or settings.campaign_vault_path)
        self.folder = get_campaign_folder(self.vault, campaign_name)
        self.campaign_file = self.folder / "campaign.json"
        self.safe_name = sanitize_filename(campaign_name.lower())
        self.assets_dir = Path("./campaign_assets") / self.safe_name
        self.maps_dir = Path("./campaign_assets") / (self.safe_name + "_maps")
        self.deployment_file = self.assets_dir / "deployment_state.json"

    @property
    def exists(self) -> bool:
        return self.campaign_file.exists()

    async def load(self, normalize: bool = True) -> Dict[str, Any]:
        """Load campaign.json; raises CampaignNotFound if absent.

        normalize=True unwraps section lists nested inside the "campaign"
        block (older saves / nest-happy local models).
        """
        if not self.exists:
            raise CampaignNotFound(f"Campaign '{self.name}' not found in vault")
        raw = await asyncio.to_thread(self.campaign_file.read_text, encoding="utf-8")
        data = json.loads(raw)
        if normalize:
            from campaign.generator import _normalize_campaign_sections
            data = _normalize_campaign_sections(data)
        return data

    async def save(self, campaign_data: Dict[str, Any]) -> None:
        await asyncio.to_thread(
            self.campaign_file.write_text,
            json.dumps(campaign_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    async def load_deployment(self) -> Dict[str, Any]:
        """Deployment state from the last deploy; {} if never deployed."""
        if not self.deployment_file.exists():
            return {}
        raw = await asyncio.to_thread(self.deployment_file.read_text, encoding="utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    async def save_deployment(self, deployment: Dict[str, Any]) -> None:
        await asyncio.to_thread(self.assets_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            self.deployment_file.write_text,
            json.dumps(deployment, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
