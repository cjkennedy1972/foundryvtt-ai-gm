"""
Campaign Obsidian Sync — Save generated campaigns to Obsidian vault.

Each campaign gets its own folder with:
- [[Campaign Name]] — Main index note with overview, links to all components
- [[Campaign Name]]/NPCs/ — Individual NPC notes
- [[Campaign Name]]/Locations/ — Individual location notes
- [[Campaign Name]]/Quests/ — Individual quest notes
- [[Campaign Name]]/Maps/ — Map images and descriptions
- [[Campaign Name]]/Story/ — Story arc notes and progression tracking
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def resolve_vault_path(campaign_vault_path: str) -> Path:
    """Resolve the Obsidian vault path, expanding ~."""
    return Path(campaign_vault_path).expanduser()


def get_campaign_folder(vault_path: Path, campaign_name: str) -> Path:
    """Get the campaign folder path within the vault."""
    safe_name = _sanitize_filename(campaign_name)
    return vault_path / "Aethelwyrd-Campaigns" / safe_name


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filesystem path."""
    sanitizedName = name.replace("/", "_").replace("\\", "_")
    sanitizedName = sanitizedName.replace(":", "_").replace("*", "_")
    sanitizedName = sanitizedName.replace("?", "_").replace('"', "_")
    sanitizedName = sanitizedName.replace("<", "_").replace(">", "_")
    sanitizedName = sanitizedName.replace("|", "_")
    return sanitizedName


def ensure_campaign_dirs(campaign_folder: Path) -> Dict[str, Path]:
    """Create all necessary campaign subdirectories. Returns dict of dir paths."""
    dirs = {
        "root": campaign_folder,
        "npcs": campaign_folder / "NPCs",
        "locations": campaign_folder / "Locations",
        "quests": campaign_folder / "Quests",
        "maps": campaign_folder / "Maps",
        "story": campaign_folder / "Story",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def save_campaign_index(campaign_folder: Path, campaign_data: Dict[str, Any]) -> str:
    """Save the main campaign index note to Obsidian. Returns file path."""
    from campaign.generator import campaign_to_markdown

    content = campaign_to_markdown(campaign_data)
    index_file = campaign_folder / "Index.md"
    index_file.write_text(content, encoding="utf-8")

    # Also save structured JSON for programmatic access
    data_file = campaign_folder / "campaign.json"
    data_file.write_text(json.dumps(campaign_data, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(f"Saved campaign index: {index_file}")
    return str(index_file)


def save_npc_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save individual NPC notes. Returns list of saved file paths."""
    from campaign.generator import build_npc_markdown

    campaign_name = campaign_data.get("campaign", {}).get("name", "Campaign")
    npcs_dir = campaign_folder / "NPCs"
    npcs_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for npc in campaign_data.get("npcs", []):
        note_name = npc.get("name", "Unknown")
        safe_name = _sanitize_filename(note_name)
        npc_file = npcs_dir / f"{safe_name}.md"
        content = build_npc_markdown(campaign_name, npc)
        npc_file.write_text(content, encoding="utf-8")
        saved.append(str(npc_file))
        logger.debug(f"Saved NPC note: {npc_file}")

    return saved


def save_location_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save individual location notes. Returns list of saved file paths."""
    from campaign.generator import build_location_markdown

    campaign_name = campaign_data.get("campaign", {}).get("name", "Campaign")
    locs_dir = campaign_folder / "Locations"
    locs_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for loc in campaign_data.get("locations", []):
        note_name = loc.get("name", "Unknown")
        safe_name = _sanitize_filename(note_name)
        loc_file = locs_dir / f"{safe_name}.md"
        content = build_location_markdown(campaign_name, loc)
        loc_file.write_text(content, encoding="utf-8")
        saved.append(str(loc_file))
        logger.debug(f"Saved location note: {loc_file}")

    return saved


def save_quest_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save individual quest notes. Returns list of saved file paths."""
    from campaign.generator import build_quest_markdown

    campaign_name = campaign_data.get("campaign", {}).get("name", "Campaign")
    quests_dir = campaign_folder / "Quests"
    quests_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for quest in campaign_data.get("quests", []):
        note_name = quest.get("title", "Unknown Quest")
        safe_name = _sanitize_filename(note_name)
        quest_file = quests_dir / f"{safe_name}.md"
        content = build_quest_markdown(campaign_name, quest)
        quest_file.write_text(content, encoding="utf-8")
        saved.append(str(quest_file))
        logger.debug(f"Saved quest note: {quest_file}")

    return saved


def save_story_arcs(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save story arc notes. Returns list of saved file paths."""
    story_dir = campaign_folder / "Story"
    story_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for arc in campaign_data.get("story_arcs", []):
        arc_title = arc.get("title", f"Act {arc.get('act', '?')}")
        safe_name = _sanitize_filename(arc_title)
        arc_file = story_dir / f"Act{arc.get('act', '?')} - {safe_name}.md"

        content = f"""# {arc.get('title', f'Act {arc.get("act", "?")}')}

tags: [story-arc, act-{arc.get('act', '?')}]

## Overview

{arc.get('description', '')}

## Milestones

"""
        for ms in arc.get("milestones", []):
            content += f"- **{ms.get('name', '')}**: {ms.get('description', '')}\n"

        if arc.get("climax"):
            content += f"\n## Climax\n\n{arc['climax']}\n"
        if arc.get("transition_to_act2"):
            content += f"\n## Transition\n\n{arc['transition_to_act2']}\n"

        arc_file.write_text(content, encoding="utf-8")
        saved.append(str(arc_file))
        logger.debug(f"Saved story arc: {arc_file}")

    return saved


def save_artifacts(campaign_folder: Path, campaign_data: Dict[str, Any]) -> str:
    """Save artifacts note. Returns file path."""
    story_dir = campaign_folder / "Story"
    story_dir.mkdir(parents=True, exist_ok=True)

    content = "# Artifacts & McGuffins\n\n"
    for art in campaign_data.get("artifacts", []):
        content += f"## {art.get('name', 'Unnamed Artifact')} [{art.get('type', 'common')}]\n\n"
        content += f"{art.get('description', '')}\n\n"
        if art.get("fragments"):
            content += f"**Fragments:** {art['fragments']}\n"
            for i, power in enumerate(art.get("fragment_powers", []), 1):
                content += f"- Fragment {i}: {power}\n"
            content += "\n"

    art_file = story_dir / "Artifacts.md"
    art_file.write_text(content, encoding="utf-8")
    return str(art_file)


def save_factions(campaign_folder: Path, campaign_data: Dict[str, Any]) -> str:
    """Save factions note. Returns file path."""
    content = "# Factions\n\n"
    for f in campaign_data.get("factions", []):
        content += f"## {f.get('name', 'Unknown Faction')}\n\n"
        content += f"**Alignment:** {f.get('alignment', '???')}\n\n"
        content += f"{f.get('description', '')}\n\n"
        content += f"**Goals:**\n"
        for g in f.get("goals", []):
            content += f"- {g}\n"
        content += f"\n**Strength:** {f.get('strength', 'unknown')}\n\n"
        content += f"**Members:** {f.get('members', 'unknown')}\n\n"

    faction_file = campaign_folder / "Factions.md"
    faction_file.write_text(content, encoding="utf-8")
    return str(faction_file)


def sync_campaign_to_vault(campaign_data: Dict[str, Any], vault_path: str = None) -> Dict[str, Any]:
    """Sync a complete campaign to the Obsidian vault.

    Returns manifest with all saved file paths and campaign metadata.
    """
    if vault_path is None:
        from config import settings
        vault_path = settings.campaign_vault_path

    campaign_name = campaign_data.get("campaign", {}).get("name", "Unnamed Campaign")
    vault = resolve_vault_path(vault_path)
    campaign_folder = get_campaign_folder(vault, campaign_name)
    dirs = ensure_campaign_dirs(campaign_folder)

    # Save all components
    save_campaign_index(campaign_folder, campaign_data)
    npc_files = save_npc_notes(campaign_folder, campaign_data)
    loc_files = save_location_notes(campaign_folder, campaign_data)
    quest_files = save_quest_notes(campaign_folder, campaign_data)
    story_files = save_story_arcs(campaign_folder, campaign_data)

    artifacts = campaign_data.get("artifacts", [])
    if artifacts:
        save_artifacts(campaign_folder, campaign_data)

    factions = campaign_data.get("factions", [])
    if factions:
        save_factions(campaign_folder, campaign_data)

    manifest = {
        "campaign_name": campaign_name,
        "vault_path": str(vault),
        "campaign_folder": str(campaign_folder),
        "saved_at": datetime.now().isoformat(),
        "files": {
            "index": str(dirs["root"] / "Index.md"),
            "npcs": npc_files,
            "locations": loc_files,
            "quests": quest_files,
            "story_arcs": story_files,
            "artifacts": str(dirs["story"] / "Artifacts.md") if artifacts else None,
            "factions": str(dirs["root"] / "Factions.md") if factions else None,
        }
    }

    logger.info(f"Campaign synced: {campaign_name} → {campaign_folder}")
    return manifest


def get_campaign_manifest(campaign_folder: Path) -> Optional[Dict]:
    """Load campaign manifest if it exists."""
    manifest_file = campaign_folder / "campaign.json"
    if manifest_file.exists():
        with open(manifest_file) as f:
            return json.load(f)
    return None


def list_campaigns(vault_path: str = None) -> List[str]:
    """List all saved campaigns in the vault."""
    if vault_path is None:
        from config import settings
        vault_path = settings.campaign_vault_path

    vault = resolve_vault_path(vault_path)
    campaigns_dir = vault / "Aethelwyrd-Campaigns"

    campaigns = []
    if campaigns_dir.exists():
        for d in campaigns_dir.iterdir():
            if d.is_dir() and (d / "campaign.json").exists():
                manifest = get_campaign_manifest(d)
                if manifest:
                    # campaign.json holds the campaign data, where the name
                    # lives under campaign.name; fall back to the folder name
                    name = (
                        manifest.get("campaign_name")
                        or manifest.get("campaign", {}).get("name")
                        or d.name
                    )
                    campaigns.append({
                        "name": name,
                        "folder": str(d),
                        "saved_at": manifest.get("saved_at", "unknown"),
                    })

    return campaigns


def delete_campaign(campaign_name: str, vault_path: str = None) -> bool:
    """Delete a campaign from the vault."""
    if vault_path is None:
        from config import settings
        vault_path = settings.campaign_vault_path

    vault = resolve_vault_path(vault_path)
    campaign_folder = get_campaign_folder(vault, campaign_name)

    if campaign_folder.exists():
        shutil.rmtree(campaign_folder)
        logger.info(f"Deleted campaign: {campaign_name}")
        return True
    return False
