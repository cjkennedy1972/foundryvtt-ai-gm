#!/usr/bin/env python3
"""The vault is a Markdown render of campaign.json. Three things were wrong
with the round trip.

1. context/loader.py rebuilt NPC records by regex-scanning that render for
   `## Name` headings, while the structured source sat in campaign.json in
   the same folder. The scan produced a 400-character slab of raw Markdown as
   the "description" and could not see alignment, disposition or personality
   at all, because the render drops them.

2. ensure_campaign_dirs created Maps/ and Portraits/ in the vault and nothing
   ever wrote to them. The generated images lived only in ./campaign_assets
   and in Foundry's own data directory, so a human reading the vault got
   prose with no pictures, and build_location_markdown printed a guessed
   filename in backticks rather than the one the pipeline actually produced.

3. The wikilinks omit the Campaigns/ segment the files are actually under, so
   they point at paths that do not exist.

Run:
    cd ai-engine && python -m pytest tests/test_vault_structure_images_links.py -v
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign import obsidian_sync
from campaign.generator import build_location_markdown, build_npc_markdown, campaign_to_markdown
from context.loader import CampaignLoader
from npc.registry import NPCRegistry

CAMPAIGN = {
    "campaign": {"name": "Valenthal", "description": "A drowned kingdom.", "theme": "gothic"},
    "npcs": [
        {
            "name": "Elder Morwenna",
            "role": "mentor",
            "faction": "Circle of Elders",
            "alignment": "LG",
            "description": "A wise but weary herbalist who knows more than she lets on.",
            "personality": ["patient", "secretive", "protective"],
            "motivations": ["Protect the ancient secret"],
            "relationships": ["dislikes Baron Vex"],
            "disposition": 1,
            "portrait_file": "portrait_elder_morwenna.png",
        }
    ],
    "locations": [
        {
            "name": "Riverbend Village",
            "type": "village",
            "act": 1,
            "description": "A small farming village.",
            "map_style": "top-down village",
            "map_file": "riverbend_village_a1b2.png",
        }
    ],
    "quest_logs": [],
    "factions": [{"name": "Circle of Elders", "alignment": "LG", "description": "Keepers."}],
}


def _vault(tmp_path: Path) -> Path:
    """The vault as Phase 3 leaves it: notes written before any asset exists.

    build_campaign syncs to the vault before it generates maps and portraits,
    so map_file and portrait_file are absent at this point. A fixture that
    includes them would not exercise the re-render that Phase 4 depends on.
    """
    before_assets = json.loads(json.dumps(CAMPAIGN))
    for npc in before_assets["npcs"]:
        npc.pop("portrait_file", None)
    for loc in before_assets["locations"]:
        loc.pop("map_file", None)

    asyncio.run(obsidian_sync.sync_campaign_to_vault(before_assets, str(tmp_path)))
    return tmp_path


def _assets(tmp_path: Path) -> Path:
    """The ./campaign_assets layout the map generator writes."""
    assets = tmp_path / "assets"
    (assets / "portraits").mkdir(parents=True)
    (assets / "riverbend_village_a1b2.png").write_bytes(b"\x89PNG map")
    (assets / "portraits" / "portrait_elder_morwenna.png").write_bytes(b"\x89PNG face")
    return assets


# ── 1. structure comes from campaign.json, not a regex over the render ────

def test_npcs_are_registered_with_the_fields_the_json_holds(tmp_path):
    """alignment, disposition and personality never survive the Markdown
    render, so a scan of it cannot recover them."""
    vault = _vault(tmp_path)
    loader = CampaignLoader(vault_path=str(vault))
    asyncio.run(loader.load("Valenthal"))
    registry = NPCRegistry()

    loader.register_vault_npcs(registry)

    record = registry.get_npc_by_name("Elder Morwenna")
    assert record is not None
    assert record.alignment == "LG"
    assert record.disposition == 1
    assert record.personality, "personality traits are in the json and were dropped"


def test_the_description_is_the_real_one_not_a_slab_of_markdown(tmp_path):
    vault = _vault(tmp_path)
    loader = CampaignLoader(vault_path=str(vault))
    asyncio.run(loader.load("Valenthal"))
    registry = NPCRegistry()

    loader.register_vault_npcs(registry)

    description = registry.get_npc_by_name("Elder Morwenna").description
    assert description == "A wise but weary herbalist who knows more than she lets on."
    assert "##" not in description and "tags:" not in description


def test_section_headings_are_not_mistaken_for_characters(tmp_path):
    """The scan registered any `## Heading` it found, guarded by a hardcoded
    list of words to skip."""
    vault = _vault(tmp_path)
    loader = CampaignLoader(vault_path=str(vault))
    asyncio.run(loader.load("Valenthal"))
    registry = NPCRegistry()

    loader.register_vault_npcs(registry)

    names = {r.npc_name for r in registry.list_npcs()}
    assert names == {"Elder Morwenna"}, f"invented NPCs from headings: {names - {'Elder Morwenna'}}"


def test_a_vault_with_no_campaign_json_still_scans_the_markdown(tmp_path):
    """Hand-made and legacy vaults have notes but no manifest."""
    vault = _vault(tmp_path)
    (vault / "Campaigns" / "Valenthal" / "campaign.json").unlink()
    loader = CampaignLoader(vault_path=str(vault))
    asyncio.run(loader.load("Valenthal"))
    registry = NPCRegistry()

    loader.register_vault_npcs(registry)

    assert registry.get_npc_by_name("Elder Morwenna") is not None


# ── 2. images reach the vault and the notes point at them ─────────────────

def test_generated_images_are_copied_into_the_vault(tmp_path):
    vault = _vault(tmp_path)
    assets = _assets(tmp_path)

    asyncio.run(obsidian_sync.sync_assets_to_vault("Valenthal", CAMPAIGN, assets, str(vault)))

    folder = vault / "Campaigns" / "Valenthal"
    assert (folder / "Maps" / "riverbend_village_a1b2.png").read_bytes() == b"\x89PNG map"
    assert (folder / "Portraits" / "portrait_elder_morwenna.png").read_bytes() == b"\x89PNG face"


def test_the_npc_note_shows_the_portrait(tmp_path):
    vault = _vault(tmp_path)
    assets = _assets(tmp_path)

    asyncio.run(obsidian_sync.sync_assets_to_vault("Valenthal", CAMPAIGN, assets, str(vault)))

    note = (vault / "Campaigns" / "Valenthal" / "NPCs" / "Elder Morwenna.md").read_text()
    assert "![" in note and "portrait_elder_morwenna.png" in note


def test_the_location_note_shows_the_map_that_was_actually_generated(tmp_path):
    """It printed a guessed `maps/<name>_map.png` in backticks, which is
    neither the real filename nor a reference to anything."""
    vault = _vault(tmp_path)
    assets = _assets(tmp_path)

    asyncio.run(obsidian_sync.sync_assets_to_vault("Valenthal", CAMPAIGN, assets, str(vault)))

    note = (vault / "Campaigns" / "Valenthal" / "Locations" / "Riverbend Village.md").read_text()
    assert "riverbend_village_a1b2.png" in note
    assert "![" in note
    assert "riverbend_village_map.png" not in note


def test_a_missing_asset_file_does_not_break_the_sync(tmp_path):
    vault = _vault(tmp_path)
    empty = tmp_path / "nothing"
    empty.mkdir()

    result = asyncio.run(obsidian_sync.sync_assets_to_vault("Valenthal", CAMPAIGN, empty, str(vault)))

    assert result["copied"] == 0


def test_the_image_reference_is_a_path_that_resolves_from_the_note(tmp_path):
    """A note in NPCs/ has to reach up into Portraits/."""
    vault = _vault(tmp_path)
    assets = _assets(tmp_path)
    asyncio.run(obsidian_sync.sync_assets_to_vault("Valenthal", CAMPAIGN, assets, str(vault)))

    note_path = vault / "Campaigns" / "Valenthal" / "NPCs" / "Elder Morwenna.md"
    note = note_path.read_text()
    ref = note.split("](", 1)[1].split(")", 1)[0]

    assert (note_path.parent / ref).resolve().exists(), f"{ref} does not resolve"


# ── 3. links point at paths that exist ────────────────────────────────────

def test_index_links_include_the_folder_the_files_are_under(tmp_path):
    """Files live at <vault>/Campaigns/<name>/..., and a wikilink containing
    a slash resolves from the vault root."""
    vault = _vault(tmp_path)
    index = (vault / "Campaigns" / "Valenthal" / "Index.md").read_text()

    for link in _wikilinks(index):
        if "/" not in link:
            continue
        assert link.startswith(f"{obsidian_sync.CAMPAIGNS_DIR_NAME}/"), \
            f"[[{link}]] is missing the {obsidian_sync.CAMPAIGNS_DIR_NAME}/ prefix"


def test_every_index_link_resolves_to_a_file_on_disk(tmp_path):
    """After the full sequence: notes first, then assets copied in."""
    vault = _vault(tmp_path)
    asyncio.run(obsidian_sync.sync_assets_to_vault(
        "Valenthal", CAMPAIGN, _assets(tmp_path), str(vault)))
    index = (vault / "Campaigns" / "Valenthal" / "Index.md").read_text()

    broken = [
        link for link in _wikilinks(index)
        if "/" in link
        and not (vault / f"{link}.md").exists()   # a note
        and not (vault / link).exists()           # an image or other file
        and not (vault / link).is_dir()           # a folder
    ]
    assert broken == [], f"links point at nothing: {broken}"


def test_the_npc_note_starts_with_a_heading_not_a_half_link(tmp_path):
    """`# [[Valenthal]]/Elder Morwenna` renders as a link to the campaign
    followed by loose text, which is not a title and not a backlink."""
    note = build_npc_markdown("Valenthal", CAMPAIGN["npcs"][0])

    assert note.startswith("# Elder Morwenna"), note.splitlines()[0]


def _wikilinks(text):
    """Link targets, with any |display alias stripped."""
    import re
    return [m.split("|", 1)[0] for m in re.findall(r"\[\[([^\]]+)\]\]", text)]


# ── the build pipeline actually calls the asset sync ──────────────────────

def test_build_campaign_copies_assets_into_the_vault_after_generating_them():
    """A sync nothing calls is the defect this whole review kept finding.

    Pinned by source rather than by running build_campaign, which needs a
    live Foundry, an LLM and ComfyUI. What matters is the ordering: the call
    has to sit after the asset phase, because Phase 3 renders the notes
    before any map_file exists.
    """
    import inspect

    from campaign.orchestrator import CampaignOrchestrator

    source = inspect.getsource(CampaignOrchestrator.build_campaign)

    # The call, not the import beside it: a bare name check passes against
    # `from campaign.obsidian_sync import sync_assets_to_vault` even when
    # nothing invokes it.
    call = "await sync_assets_to_vault("
    assert call in source, "generated images never reach the vault"

    assert source.index("Phase 4: Generate assets") < source.index(call), \
        "the copy must run after the assets exist"


def test_the_index_gains_the_map_link_once_the_map_exists():
    """Phase 3 writes the index with no map_file to link, so the asset sync
    has to re-render it. Without that the index never mentions the map."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        vault = _vault(Path(tmp))
        before = (vault / "Campaigns" / "Valenthal" / "Index.md").read_text()
        assert "riverbend_village_a1b2.png" not in before

        asyncio.run(obsidian_sync.sync_assets_to_vault(
            "Valenthal", CAMPAIGN, _assets(Path(tmp)), str(vault)))

        after = (vault / "Campaigns" / "Valenthal" / "Index.md").read_text()
        assert "riverbend_village_a1b2.png" in after
