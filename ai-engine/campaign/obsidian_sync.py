"""
Campaign Obsidian Sync — Save generated campaigns to Obsidian vault.

Each campaign gets its own folder with:
- [[Campaign Name]] — Main index note with overview, links to all components
- [[Campaign Name]]/NPCs/ — Individual NPC notes (with portraits)
- [[Campaign Name]]/Locations/ — Individual location notes
- [[Campaign Name]]/Quests/ — Individual quest notes
- [[Campaign Name]]/Maps/ — Map images and descriptions
- [[Campaign Name]]/Story/ — Story arc notes and progression tracking
- [[Campaign Name]]/Journal/ — Journal entries
- [[Campaign Name]]/Loot/ — Loot tables
- [[Campaign Name]]/campaign.json — Structured data manifest

Plus a campaign registry file for easy listing and management.
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.path_safety import sanitize_filename, validate_contained_path

logger = logging.getLogger(__name__)

CAMPAIGNS_DIR_NAME = "Campaigns"
REGISTRY_FILE_NAME = "_registry.json"


def resolve_vault_path(campaign_vault_path: str) -> Path:
    """Resolve the Obsidian vault path, expanding ~."""
    return Path(campaign_vault_path).expanduser()


def get_campaign_folder(vault_path: Path, campaign_name: str) -> Path:
    """Get the campaign folder path within the vault."""
    safe_name = _sanitize_filename(campaign_name)
    return vault_path / CAMPAIGNS_DIR_NAME / safe_name


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filesystem path.

    Uses the centralized path safety utility to prevent path traversal attacks.
    Strips path separators, dots, and Windows reserved names.
    """
    return sanitize_filename(name)


async def ensure_campaign_dirs(campaign_folder: Path) -> Dict[str, Path]:
    """Create all necessary campaign subdirectories. Returns dict of dir paths."""
    dirs = {
        "root": campaign_folder,
        "npcs": campaign_folder / "NPCs",
        "locations": campaign_folder / "Locations",
        "quests": campaign_folder / "Quests",
        "maps": campaign_folder / "Maps",
        "portraits": campaign_folder / "Portraits",
        "story": campaign_folder / "Story",
        "journal": campaign_folder / "Journal",
        "loot": campaign_folder / "Loot",
    }
    for d in dirs.values():
        await asyncio.to_thread(d.mkdir, parents=True, exist_ok=True)
    return dirs


async def save_campaign_index(campaign_folder: Path, campaign_data: Dict[str, Any]) -> str:
    """Save the main campaign index note to Obsidian. Returns file path."""
    from campaign.generator import campaign_to_markdown

    content = campaign_to_markdown(campaign_data)
    index_file = campaign_folder / "Index.md"
    await asyncio.to_thread(index_file.write_text, content, encoding="utf-8")
    logger.info(f"Saved campaign index: {index_file}")
    return str(index_file)


async def save_npc_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
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
        await asyncio.to_thread(npc_file.write_text, content, encoding="utf-8")
        saved.append(str(npc_file))

        # Copy portrait if available
        if npc.get("portrait_file"):
            # Portrait files are generated externally; just note them
            pass

        logger.debug(f"Saved NPC note: {npc_file}")
    return saved


async def save_location_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
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
        await asyncio.to_thread(loc_file.write_text, content, encoding="utf-8")
        saved.append(str(loc_file))
        logger.debug(f"Saved location note: {loc_file}")
    return saved


async def save_quest_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save individual quest notes. Returns list of saved file paths."""
    from campaign.generator import build_quest_markdown

    campaign_name = campaign_data.get("campaign", {}).get("name", "Campaign")
    quests_dir = campaign_folder / "Quests"
    quests_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for quest in campaign_data.get("quest_logs", campaign_data.get("quests", [])):
        note_name = quest.get("title", "Unknown Quest")
        safe_name = _sanitize_filename(note_name)
        quest_file = quests_dir / f"{safe_name}.md"
        content = build_quest_markdown(campaign_name, quest)
        await asyncio.to_thread(quest_file.write_text, content, encoding="utf-8")
        saved.append(str(quest_file))
        logger.debug(f"Saved quest note: {quest_file}")
    return saved


async def save_scene_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save individual scene notes."""
    story_dir = campaign_folder / "Story"
    story_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    scenes = campaign_data.get("scenes", [])
    for scene in scenes:
        scene_title = scene.get("name", f"Scene {len(saved)+1}")
        safe_name = _sanitize_filename(scene_title)
        scene_file = story_dir / f"Scene - {safe_name}.md"

        content = f"""# {scene.get('name', 'Unnamed Scene')}

tags: [scene, act-{scene.get('act', '?')}]

## Overview

{scene.get('description', '')}

## Details

- **Type:** {scene.get('type', 'unknown')}
- **Act:** {scene.get('act', '?')}
- **Map Scale:** {scene.get('map_scale', 'room-scale')}
- **Token Count:** {scene.get('token_count', '?')}
- **Lighting:** {scene.get('lighting', 'default')}
- **Atmosphere:** {scene.get('atmosphere', 'neutral')}

## Map

{f"Map file: `maps/{scene.get('map_file', 'TBD')}`" if scene.get('map_style') else "Map TBD"}

## Description

{scene.get('description', 'TBD')}
"""
        await asyncio.to_thread(scene_file.write_text, content, encoding="utf-8")
        saved.append(str(scene_file))
    return saved


async def save_journal_entries(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save journal entries."""
    journal_dir = campaign_folder / "Journal"
    journal_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    entries = campaign_data.get("journal_entries", [])
    for entry in entries:
        entry_name = entry.get("title", "Untitled Entry")
        safe_name = _sanitize_filename(entry_name)
        entry_file = journal_dir / f"{safe_name}.md"

        content = f"""# {entry.get('title', 'Untitled')}

tags: [journal, {entry.get('type', 'note')}, act-{entry.get('act', '?')}]

## Entry

{entry.get('body', '')}

## Metadata

- **Type:** {entry.get('type', 'note')}
- **Act:** {entry.get('act', '?')}
- **Visible to Players:** {entry.get('visible_to_players', True)}
"""
        if entry.get("quest_id"):
            content += f"\n## Linked Quest\n\nQuest ID: `{entry['quest_id']}`\n"

        await asyncio.to_thread(entry_file.write_text, content, encoding="utf-8")
        saved.append(str(entry_file))
    return saved


async def save_loot_tables(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save loot tables."""
    loot_dir = campaign_folder / "Loot"
    loot_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    tables = campaign_data.get("loot_tables", [])
    for table in tables:
        table_name = table.get("name", "Unnamed Table")
        safe_name = _sanitize_filename(table_name)
        table_file = loot_dir / f"{safe_name}.md"

        content = f"""# {table.get('name', 'Unnamed Loot Table')}

tags: [loot-table, {table.get('table_type', 'treasure')}]

## Description

{table.get('description', '')}

## Entries

| Item | Type | Rarity | Quantity | Weight |
|------|------|--------|----------|--------|
"""
        for entry in table.get("entries", []):
            content += f"| {entry.get('name', '?')} | {entry.get('type', '?')} | {entry.get('rarity', '?')} | {entry.get('quantity', 1)} | {entry.get('weight', '?')}% |\n"

        content += "\n## Details\n\n"
        for entry in table.get("entries", []):
            content += f"### {entry.get('name', 'Unknown Item')} [{entry.get('rarity', 'common')}] {entry.get('type', '')}\n\n{entry.get('description', '')}\n\n"

        await asyncio.to_thread(table_file.write_text, content, encoding="utf-8")
        saved.append(str(table_file))
    return saved


async def save_story_arcs(campaign_folder: Path, campaign_data: Dict[str, Any]) -> List[str]:
    """Save story arc notes."""
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

        await asyncio.to_thread(arc_file.write_text, content, encoding="utf-8")
        saved.append(str(arc_file))
    return saved


async def save_artifacts(campaign_folder: Path, campaign_data: Dict[str, Any]) -> str:
    """Save artifacts note."""
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
    await asyncio.to_thread(art_file.write_text, content, encoding="utf-8")
    return str(art_file)


async def save_factions(campaign_folder: Path, campaign_data: Dict[str, Any]) -> str:
    """Save factions note."""
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
    await asyncio.to_thread(faction_file.write_text, content, encoding="utf-8")
    return str(faction_file)


async def save_encounter_notes(campaign_folder: Path, campaign_data: Dict[str, Any]) -> str:
    """Save all campaign encounters to a single Encounters.md in the campaign root.

    Written to the campaign root (not a subfolder) so CampaignLoader picks it up
    with its *.md glob and makes it available at runtime for scene-filtered injection.

    Format uses `---` separators between encounters so get_encounter_context_for_scene()
    can split and filter by **Scene:** tag without a full parser.
    """
    encounters = campaign_data.get("encounters", [])
    if not encounters:
        return ""

    difficulty_label = {
        "easy": "EASY ✦", "medium": "MEDIUM ✦✦",
        "hard": "HARD ✦✦✦", "deadly": "DEADLY ✦✦✦✦",
    }

    lines = ["# Campaign Encounters\n"]
    lines.append(
        "> Pre-staged combat encounters. "
        "Tokens are placed hidden on each linked scene. "
        "Reveal them when the trigger condition fires.\n"
    )

    for enc in encounters:
        diff = enc.get("difficulty", "medium")
        monster_lines = "\n".join(
            f"- **{m['name']}** ×{m.get('count', 1)} — "
            f"CR {m.get('cr', '?')} (HP {m.get('hp', '?')}, AC {m.get('ac', '?')})"
            for m in enc.get("monsters", [])
        )
        reward_lines = "\n".join(f"- {r}" for r in enc.get("rewards", []))
        lines.append(f"## Encounter: {enc.get('name', 'Unknown')}")
        lines.append(f"**Scene:** {enc.get('linked_scene', '')}")
        lines.append(f"**Act:** {enc.get('act', '?')}")
        lines.append(f"**Difficulty:** {difficulty_label.get(diff, diff.upper())}")
        lines.append(f"**XP Award:** {enc.get('xp_award', 0)} XP")
        lines.append(f"**Trigger:** {enc.get('trigger', '')}\n")
        lines.append(f"### Description\n{enc.get('description', '')}\n")
        lines.append(f"### Monsters (pre-staged hidden on scene)\n{monster_lines}\n")
        lines.append(f"### Environment & Cover\n{enc.get('environment_notes', '')}\n")
        lines.append(f"### Tactical Notes\n{enc.get('tactical_notes', '')}\n")
        if reward_lines:
            lines.append(f"### Rewards\n{reward_lines}\n")
        lines.append("### How to Start This Encounter")
        lines.append(
            "When the trigger fires: narrate the ambush opening, "
            "then call `start_encounter` to reveal the hidden tokens and roll initiative. "
            "Do NOT use `generate_encounter` — the monsters are already on the scene.\n"
        )
        lines.append("---\n")

    content = "\n".join(lines)
    enc_file = campaign_folder / "Encounters.md"
    await asyncio.to_thread(enc_file.write_text, content, encoding="utf-8")
    logger.info(f"Saved encounter notes: {enc_file}")
    return str(enc_file)


async def save_campaign_registry(campaign_folder: Path, manifest: Dict[str, Any]) -> str:
    """Save/update the campaign registry file."""
    vault_path = manifest.get("vault_path", "")
    registry_file = Path(vault_path) / CAMPAIGNS_DIR_NAME / REGISTRY_FILE_NAME

    registry = {"campaigns": []}
    if registry_file.exists():
        try:
            content = await asyncio.to_thread(registry_file.read_text, encoding="utf-8")
            registry = json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            registry = {"campaigns": []}

    # Remove old entry for same campaign
    safe_name = manifest.get("campaign_name", "")
    registry["campaigns"] = [
        c for c in registry["campaigns"] if c.get("name") != safe_name
    ]

    # Counts live under manifest["stats"] (built by sync_campaign_to_vault),
    # not at the top level — read them from there so the library shows real totals.
    stats = manifest.get("stats", {})
    registry["campaigns"].append({
        "name": manifest.get("campaign_name"),
        "folder": manifest.get("campaign_folder"),
        "saved_at": manifest.get("saved_at"),
        "total_scenes": stats.get("scenes", 0),
        "total_npcs": stats.get("npcs", 0),
        "total_quests": stats.get("quests", 0),
    })

    await asyncio.to_thread(
        registry_file.write_text,
        json.dumps(registry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(registry_file)


async def sync_campaign_to_vault(campaign_data: Dict[str, Any], vault_path: str = None) -> Dict[str, Any]:
    """Sync a complete campaign to the Obsidian vault.

    Returns manifest with all saved file paths and campaign metadata.
    """
    if vault_path is None:
        from config import settings
        vault_path = settings.campaign_vault_path

    campaign_name = campaign_data.get("campaign", {}).get("name", "Unnamed Campaign")
    vault = resolve_vault_path(vault_path)
    campaign_folder = get_campaign_folder(vault, campaign_name)
    dirs = await ensure_campaign_dirs(campaign_folder)

    # Save all components
    await save_campaign_index(campaign_folder, campaign_data)
    npc_files = await save_npc_notes(campaign_folder, campaign_data)
    loc_files = await save_location_notes(campaign_folder, campaign_data)
    quest_files = await save_quest_notes(campaign_folder, campaign_data)
    scene_files = await save_scene_notes(campaign_folder, campaign_data)
    journal_files = await save_journal_entries(campaign_folder, campaign_data)
    loot_files = await save_loot_tables(campaign_folder, campaign_data)
    story_files = await save_story_arcs(campaign_folder, campaign_data)

    artifacts = campaign_data.get("artifacts", [])
    if artifacts:
        await save_artifacts(campaign_folder, campaign_data)

    factions = campaign_data.get("factions", [])
    if factions:
        await save_factions(campaign_folder, campaign_data)

    encounters = campaign_data.get("encounters", [])
    encounter_file = await save_encounter_notes(campaign_folder, campaign_data) if encounters else None

    # Save structured data
    data_file = campaign_folder / "campaign.json"
    await asyncio.to_thread(
        data_file.write_text,
        json.dumps(campaign_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

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
            "scenes": scene_files,
            "journal": journal_files,
            "loot_tables": loot_files,
            "story_arcs": story_files,
            "artifacts": str(dirs["story"] / "Artifacts.md") if artifacts else None,
            "factions": str(dirs["root"] / "Factions.md") if factions else None,
            "encounters": encounter_file,
        },
        "stats": {
            "npcs": len(npc_files),
            "locations": len(loc_files),
            "quests": len(quest_files),
            "scenes": len(scene_files),
            "journal_entries": len(journal_files),
            "loot_tables": len(loot_files),
            "story_arcs": len(story_files),
            "encounters": len(encounters),
        }
    }

    await save_campaign_registry(campaign_folder, manifest)

    logger.info(f"Campaign synced: {campaign_name} → {campaign_folder}")
    return manifest


def get_campaign_manifest(campaign_folder: Path) -> Optional[Dict]:
    """Load campaign manifest if it exists."""
    manifest_file = campaign_folder / "campaign.json"
    if manifest_file.exists():
        with open(manifest_file) as f:
            data = json.load(f)
        # Normalize older saves where the section lists are nested inside
        # the "campaign" block (see generator._normalize_campaign_sections).
        from campaign.generator import _normalize_campaign_sections
        return _normalize_campaign_sections(data)
    return None


def list_campaigns(vault_path: str = None) -> List[Dict[str, Any]]:
    """List all saved campaigns in the vault."""
    if vault_path is None:
        from config import settings
        vault_path = settings.campaign_vault_path

    vault = resolve_vault_path(vault_path)
    campaigns_dir = vault / CAMPAIGNS_DIR_NAME

    # Try registry file first
    registry_file = campaigns_dir / REGISTRY_FILE_NAME
    if registry_file.exists():
        try:
            with open(registry_file) as f:
                registry = json.load(f)
            campaigns = registry.get("campaigns", [])
            # Validate folders still exist
            result = []
            for c in campaigns:
                folder = Path(c.get("folder", ""))
                if folder.exists() and (folder / "campaign.json").exists():
                    result.append(c)
            return result
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    # Fall back to scanning directories
    campaigns = []
    if campaigns_dir.exists():
        for d in campaigns_dir.iterdir():
            if d.is_dir() and (d / "campaign.json").exists():
                manifest = get_campaign_manifest(d)
                if manifest:
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


def _normalize(s: str) -> str:
    """Lowercase, strip punctuation/spaces — used for fuzzy campaign matching."""
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_campaign_by_world(world_title: str, world_id: str = "", vault_path: str = None) -> Optional[str]:
    """Return the campaign name that best matches the active Foundry world.

    Matching priority:
    1. Exact ``world_name`` stored in a registry entry (set by ``link_world_to_campaign``).
    2. Fuzzy match: normalised world title/id against normalised campaign name.

    Returns the campaign name string on a confident match, or None.
    """
    campaigns = list_campaigns(vault_path)
    if not campaigns:
        return None

    # 1) Stored exact match
    for c in campaigns:
        stored = c.get("world_name", "")
        if stored and stored in (world_title, world_id):
            return c["name"]

    # 2) Fuzzy: normalised world title/id vs normalised campaign name
    norm_title = _normalize(world_title)
    norm_id = _normalize(world_id)
    for c in campaigns:
        norm_name = _normalize(c["name"])
        if not norm_name:
            continue
        # Accept if either world signal is a substring of the campaign name or vice-versa
        if (norm_title and (norm_title in norm_name or norm_name in norm_title)) or \
           (norm_id and (norm_id in norm_name or norm_name in norm_id)):
            logger.info(
                f"[WorldMatch] Fuzzy matched world {world_title!r}/{world_id!r} "
                f"→ campaign {c['name']!r}"
            )
            return c["name"]

    return None


def link_world_to_campaign(campaign_name: str, world_title: str, world_id: str = "", vault_path: str = None) -> bool:
    """Persist the association between a Foundry world and a campaign in the registry.

    Writes ``world_name: world_title`` into the matching registry entry so
    future startups can resolve the campaign without fuzzy matching.
    Returns True on success.
    """
    if vault_path is None:
        from config import settings
        vault_path = settings.campaign_vault_path

    vault = resolve_vault_path(vault_path)
    registry_file = vault / CAMPAIGNS_DIR_NAME / REGISTRY_FILE_NAME
    if not registry_file.exists():
        return False

    try:
        registry = json.loads(registry_file.read_text(encoding="utf-8"))
        updated = False
        for c in registry.get("campaigns", []):
            if c.get("name") == campaign_name:
                c["world_name"] = world_title
                if world_id:
                    c["world_id"] = world_id
                updated = True
                break
        if updated:
            registry_file.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"[WorldMatch] Linked world {world_title!r} → campaign {campaign_name!r}")
        return updated
    except Exception as e:
        logger.warning(f"[WorldMatch] Could not update registry: {e}")
        return False


def get_campaign_world(campaign_name: str, vault_path: str = None) -> Optional[Dict[str, str]]:
    """Return the persisted world association for a campaign, if one exists."""
    for campaign in list_campaigns(vault_path):
        if campaign.get("name") == campaign_name:
            return {
                "world_name": campaign.get("world_name", ""),
                "world_id": campaign.get("world_id", ""),
            }
    return None


async def delete_campaign(campaign_name: str, vault_path: str = None) -> bool:
    """Delete a campaign from the vault.

    Validates that the campaign folder is safely contained within the vault
    before deletion to prevent path traversal attacks.
    """
    if vault_path is None:
        from config import settings
        vault_path = settings.campaign_vault_path

    vault = resolve_vault_path(vault_path)
    campaigns_base = vault / CAMPAIGNS_DIR_NAME

    # Sanitize campaign name to prevent path traversal
    try:
        safe_name = _sanitize_filename(campaign_name)
        campaign_folder = campaigns_base / safe_name
        # Validate the resolved path is still within campaigns_base
        validate_contained_path(str(campaign_folder.relative_to(campaigns_base)), str(campaigns_base))
    except (ValueError, OSError) as e:
        logger.warning(f"Rejected unsafe campaign name '{campaign_name}': {e}")
        return False

    if campaign_folder.exists():
        try:
            await asyncio.to_thread(shutil.rmtree, campaign_folder)
            logger.info(f"Deleted campaign: {campaign_name}")
        except Exception as e:
            logger.error(f"Failed to delete campaign folder: {e}", exc_info=True)
            return False

        # Update registry
        registry_file = vault / CAMPAIGNS_DIR_NAME / REGISTRY_FILE_NAME
        if registry_file.exists():
            try:
                registry = await asyncio.to_thread(json.loads, registry_file.read_text(encoding="utf-8"))
                registry["campaigns"] = [
                    c for c in registry["campaigns"] if c.get("name") != campaign_name
                ]
                await asyncio.to_thread(registry_file.write_text, json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
            except (json.JSONDecodeError, FileNotFoundError):
                pass

        return True
    return False
