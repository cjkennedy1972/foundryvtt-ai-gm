"""Exercises the per-section Obsidian markdown writers plus the campaign
registry/listing/world-link helpers in campaign/obsidian_sync.py, and pins a
regression for a lost-update race in the campaign registry's
read-modify-write (see test_concurrent_registry_saves_do_not_drop_entries).

Run:
    cd ai-engine && python -m pytest tests/test_obsidian_sync_writers.py -v
"""
import asyncio
import json
from pathlib import Path

import config
from campaign import obsidian_sync


# ── save_quest_notes ────────────────────────────────────────────────────────

def test_save_quest_notes_uses_quest_logs_when_present(tmp_path):
    data = {
        "campaign": {"name": "Demo"},
        "quest_logs": [{"title": "Find the Amulet", "description": "A relic hunt."}],
        "quests": [{"title": "Should be ignored"}],
    }
    saved = asyncio.run(obsidian_sync.save_quest_notes(tmp_path, data))

    assert len(saved) == 1
    content = Path(saved[0]).read_text(encoding="utf-8")
    assert "Find the Amulet" in content
    assert "Should be ignored" not in content
    assert Path(saved[0]).name == "Find the Amulet.md"


def test_save_quest_notes_falls_back_to_quests_key(tmp_path):
    data = {"campaign": {"name": "Demo"}, "quests": [{"title": "Old Format Quest"}]}
    saved = asyncio.run(obsidian_sync.save_quest_notes(tmp_path, data))

    assert len(saved) == 1
    assert "Old Format Quest" in Path(saved[0]).read_text(encoding="utf-8")


# ── save_journal_entries ─────────────────────────────────────────────────────

def test_save_journal_entries_links_quest_and_reports_metadata(tmp_path):
    data = {
        "journal_entries": [
            {
                "title": "The Ambush",
                "body": "The party was ambushed at the ford.",
                "type": "event",
                "act": 2,
                "visible_to_players": False,
                "quest_id": "q-17",
            }
        ]
    }
    saved = asyncio.run(obsidian_sync.save_journal_entries(tmp_path, data))

    content = Path(saved[0]).read_text(encoding="utf-8")
    assert "The party was ambushed at the ford." in content
    assert "**Visible to Players:** False" in content
    assert "act-2" in content
    assert "Quest ID: `q-17`" in content


def test_save_journal_entries_without_quest_id_has_no_linked_quest_section(tmp_path):
    data = {"journal_entries": [{"title": "Quiet Night", "body": "Nothing happened."}]}
    saved = asyncio.run(obsidian_sync.save_journal_entries(tmp_path, data))

    content = Path(saved[0]).read_text(encoding="utf-8")
    assert "Linked Quest" not in content
    assert "**Visible to Players:** True" in content


# ── save_loot_tables ─────────────────────────────────────────────────────────

def test_save_loot_tables_renders_entries_table_and_details(tmp_path):
    data = {
        "loot_tables": [
            {
                "name": "Goblin Hoard",
                "table_type": "treasure",
                "description": "Pocket change and junk.",
                "entries": [
                    {"name": "Rusty Dagger", "type": "weapon", "rarity": "common",
                     "quantity": 1, "weight": 40, "description": "Barely sharp."},
                    {"name": "Gold Coins", "type": "currency", "rarity": "common",
                     "quantity": 12, "weight": 60},
                ],
            }
        ]
    }
    saved = asyncio.run(obsidian_sync.save_loot_tables(tmp_path, data))

    content = Path(saved[0]).read_text(encoding="utf-8")
    assert "| Rusty Dagger | weapon | common | 1 | 40% |" in content
    assert "| Gold Coins | currency | common | 12 | 60% |" in content
    assert "### Rusty Dagger [common] weapon" in content
    assert "Barely sharp." in content


# ── save_story_arcs ──────────────────────────────────────────────────────────

def test_save_story_arcs_renders_milestones_climax_and_transition(tmp_path):
    data = {
        "story_arcs": [
            {
                "act": 1,
                "title": "Rising Tide",
                "description": "The flood begins.",
                "milestones": [{"name": "Dam breaks", "description": "The water rises."}],
                "climax": "The village floods.",
                "transition_to_act2": "Survivors flee north.",
            }
        ]
    }
    saved = asyncio.run(obsidian_sync.save_story_arcs(tmp_path, data))

    content = Path(saved[0]).read_text(encoding="utf-8")
    assert "- **Dam breaks**: The water rises." in content
    assert "## Climax\n\nThe village floods." in content
    assert "## Transition\n\nSurvivors flee north." in content
    assert Path(saved[0]).name == "Act1 - Rising Tide.md"


def test_save_story_arcs_omits_climax_and_transition_when_absent(tmp_path):
    data = {"story_arcs": [{"act": 2, "title": "Calm", "description": "A lull."}]}
    saved = asyncio.run(obsidian_sync.save_story_arcs(tmp_path, data))

    content = Path(saved[0]).read_text(encoding="utf-8")
    assert "## Climax" not in content
    assert "## Transition" not in content


# ── save_artifacts ───────────────────────────────────────────────────────────

def test_save_artifacts_renders_fragments_and_powers(tmp_path):
    data = {
        "artifacts": [
            {
                "name": "Orb of Storms",
                "type": "legendary",
                "description": "Crackles with latent lightning.",
                "fragments": 3,
                "fragment_powers": ["Calls wind", "Calls rain", "Calls lightning"],
            }
        ]
    }
    path = asyncio.run(obsidian_sync.save_artifacts(tmp_path, data))

    content = Path(path).read_text(encoding="utf-8")
    assert "## Orb of Storms [legendary]" in content
    assert "**Fragments:** 3" in content
    assert "- Fragment 1: Calls wind" in content
    assert "- Fragment 3: Calls lightning" in content


def test_save_artifacts_with_no_fragments_omits_fragment_section(tmp_path):
    data = {"artifacts": [{"name": "Plain Sword", "description": "Just a sword."}]}
    path = asyncio.run(obsidian_sync.save_artifacts(tmp_path, data))

    content = Path(path).read_text(encoding="utf-8")
    assert "**Fragments:**" not in content
    assert "Just a sword." in content


# ── save_factions ────────────────────────────────────────────────────────────

def test_save_factions_renders_goals_strength_and_members(tmp_path):
    data = {
        "factions": [
            {
                "name": "Circle of Elders",
                "alignment": "LG",
                "description": "Keepers of the old ways.",
                "goals": ["Protect the grove", "Guide the chosen one"],
                "strength": "moderate",
                "members": 40,
            }
        ]
    }
    path = asyncio.run(obsidian_sync.save_factions(tmp_path, data))

    content = Path(path).read_text(encoding="utf-8")
    assert "## Circle of Elders" in content
    assert "**Alignment:** LG" in content
    assert "- Protect the grove" in content
    assert "- Guide the chosen one" in content
    assert "**Strength:** moderate" in content
    assert "**Members:** 40" in content


# ── save_encounter_notes ─────────────────────────────────────────────────────

def test_save_encounter_notes_renders_monsters_and_rewards(tmp_path):
    data = {
        "encounters": [
            {
                "name": "Ford Ambush",
                "linked_scene": "Riverbend Ford",
                "act": 1,
                "difficulty": "hard",
                "xp_award": 450,
                "trigger": "Party crosses the ford.",
                "description": "Bandits spring from the reeds.",
                "monsters": [{"name": "Bandit", "count": 3, "cr": "1/8", "hp": 11, "ac": 12}],
                "environment_notes": "Shallow water, slick rocks.",
                "tactical_notes": "Focus the spellcaster first.",
                "rewards": ["50 gp", "A rusty map"],
            }
        ]
    }
    path = asyncio.run(obsidian_sync.save_encounter_notes(tmp_path, data))

    content = Path(path).read_text(encoding="utf-8")
    assert "## Encounter: Ford Ambush" in content
    assert "**Difficulty:** HARD" in content  # known label looked up from the table
    assert "**Bandit** ×3 — CR 1/8 (HP 11, AC 12)" in content
    assert "- 50 gp" in content
    assert "- A rusty map" in content


def test_save_encounter_notes_falls_back_to_upper_for_unknown_difficulty(tmp_path):
    data = {"encounters": [{"name": "Weird Fight", "difficulty": "nightmare"}]}
    path = asyncio.run(obsidian_sync.save_encounter_notes(tmp_path, data))

    content = Path(path).read_text(encoding="utf-8")
    assert "**Difficulty:** NIGHTMARE" in content


def test_save_encounter_notes_returns_empty_string_when_no_encounters(tmp_path):
    result = asyncio.run(obsidian_sync.save_encounter_notes(tmp_path, {}))
    assert result == ""
    assert not (tmp_path / "Encounters.md").exists()


# ── save_campaign_registry ───────────────────────────────────────────────────

def test_save_campaign_registry_replaces_prior_entry_for_same_campaign(tmp_path):
    manifest_v1 = {
        "campaign_name": "Valenthal", "vault_path": str(tmp_path),
        "campaign_folder": str(tmp_path / "Campaigns" / "Valenthal"), "saved_at": "t1",
        "stats": {"npcs": 1, "scenes": 2, "quests": 0},
    }
    (tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME).mkdir(parents=True)
    asyncio.run(obsidian_sync.save_campaign_registry(tmp_path, manifest_v1))

    manifest_v2 = dict(manifest_v1, saved_at="t2", stats={"npcs": 5, "scenes": 2, "quests": 1})
    registry_path = asyncio.run(obsidian_sync.save_campaign_registry(tmp_path, manifest_v2))

    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    assert len(registry["campaigns"]) == 1
    assert registry["campaigns"][0]["saved_at"] == "t2"
    assert registry["campaigns"][0]["total_npcs"] == 5


def test_save_campaign_registry_recovers_from_corrupted_registry_file(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    campaigns_dir.mkdir(parents=True)
    (campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).write_text("{not json", encoding="utf-8")

    manifest = {
        "campaign_name": "Fresh Start", "vault_path": str(tmp_path),
        "campaign_folder": str(campaigns_dir / "Fresh Start"), "saved_at": "t1",
        "stats": {},
    }
    registry_path = asyncio.run(obsidian_sync.save_campaign_registry(tmp_path, manifest))

    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    assert [c["name"] for c in registry["campaigns"]] == ["Fresh Start"]


def test_concurrent_registry_saves_do_not_drop_entries(tmp_path, monkeypatch):
    """Two campaigns in the same vault syncing at (nearly) the same time
    must not clobber each other's registry entry.

    save_campaign_registry does a plain read -> modify -> write with no
    locking, unlike Canon.md just below it in this module, which the module's
    own comment explains needs a lock for exactly this reason (several
    concurrent writers). A registry shared by every campaign in a vault has
    the same hazard: if both syncs read the file before either writes, the
    second write overwrites the first entry's addition entirely.

    The read step is slowed down here to force two saves to overlap instead
    of relying on real timing, which would make the regression flaky.
    """
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    campaigns_dir.mkdir(parents=True)

    real_read_text = Path.read_text

    def slow_read_text(self, *args, **kwargs):
        result = real_read_text(self, *args, **kwargs)
        import time
        time.sleep(0.05)
        return result

    monkeypatch.setattr(Path, "read_text", slow_read_text)

    def manifest_for(name):
        return {
            "campaign_name": name, "vault_path": str(tmp_path),
            "campaign_folder": str(campaigns_dir / name), "saved_at": "t",
            "stats": {},
        }

    async def _run_both():
        await asyncio.gather(
            obsidian_sync.save_campaign_registry(tmp_path, manifest_for("Alpha")),
            obsidian_sync.save_campaign_registry(tmp_path, manifest_for("Beta")),
        )

    asyncio.run(_run_both())

    registry = json.loads((campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).read_text(encoding="utf-8"))
    names = {c["name"] for c in registry["campaigns"]}
    assert names == {"Alpha", "Beta"}, (
        f"a concurrent registry save dropped an entry: got {names}"
    )


# ── sync_campaign_to_vault: default vault path ───────────────────────────────

def test_sync_campaign_to_vault_uses_configured_vault_path_when_none_given(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "campaign_vault_path", str(tmp_path))

    manifest = asyncio.run(obsidian_sync.sync_campaign_to_vault(
        {"campaign": {"name": "Default Path Campaign"}}
    ))

    assert manifest["vault_path"] == str(tmp_path)
    assert (tmp_path / "Campaigns" / "Default Path Campaign" / "Index.md").exists()


# ── sync_assets_to_vault: copy failure path ──────────────────────────────────

def test_sync_assets_to_vault_records_a_copy_failure_as_missing(tmp_path, monkeypatch):
    campaign_data = {
        "npcs": [{"name": "Hermit", "portrait_file": "hermit.png"}],
    }
    asyncio.run(obsidian_sync.sync_campaign_to_vault(
        dict(campaign_data, campaign={"name": "CopyFail"}), str(tmp_path)
    ))

    assets = tmp_path / "assets"
    (assets / "portraits").mkdir(parents=True)
    (assets / "portraits" / "hermit.png").write_bytes(b"\x89PNG")

    def boom(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(obsidian_sync.shutil, "copy2", boom)

    result = asyncio.run(obsidian_sync.sync_assets_to_vault(
        "CopyFail", dict(campaign_data, campaign={"name": "CopyFail"}), assets, str(tmp_path)
    ))

    assert result == {"copied": 0, "missing": ["hermit.png"]}


# ── get_campaign_manifest ────────────────────────────────────────────────────

def test_get_campaign_manifest_normalizes_legacy_nested_sections(tmp_path):
    folder = tmp_path / "Campaign"
    folder.mkdir()
    (folder / "campaign.json").write_text(json.dumps({
        "campaign": {"name": "Legacy", "npcs": [{"name": "Old NPC"}]},
    }), encoding="utf-8")

    manifest = obsidian_sync.get_campaign_manifest(folder)

    assert manifest is not None
    assert manifest["npcs"] == [{"name": "Old NPC"}]


def test_get_campaign_manifest_returns_none_when_no_file(tmp_path):
    assert obsidian_sync.get_campaign_manifest(tmp_path / "nope") is None


# ── list_campaigns ───────────────────────────────────────────────────────────

def test_list_campaigns_filters_out_registry_entries_whose_folder_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "campaign_vault_path", str(tmp_path))
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    campaigns_dir.mkdir(parents=True)

    live_folder = campaigns_dir / "Live"
    live_folder.mkdir()
    (live_folder / "campaign.json").write_text("{}", encoding="utf-8")

    registry = {
        "campaigns": [
            {"name": "Live", "folder": str(live_folder)},
            {"name": "Deleted", "folder": str(campaigns_dir / "Deleted")},
        ]
    }
    (campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).write_text(
        json.dumps(registry), encoding="utf-8"
    )

    result = obsidian_sync.list_campaigns()

    assert [c["name"] for c in result] == ["Live"]


def test_list_campaigns_falls_back_to_directory_scan_without_registry(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    folder = campaigns_dir / "Scanned"
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps({"campaign_name": "Scanned"}), encoding="utf-8")

    result = obsidian_sync.list_campaigns(str(tmp_path))

    assert len(result) == 1
    assert result[0]["name"] == "Scanned"
    assert result[0]["folder"] == str(folder)


# ── _normalize ────────────────────────────────────────────────────────────────

def test_normalize_strips_punctuation_and_lowercases():
    assert obsidian_sync._normalize("The Lost Mines! (part 2)") == "thelostminespart2"
    assert obsidian_sync._normalize(None) == ""


# ── find_campaign_by_world ───────────────────────────────────────────────────

def test_find_campaign_by_world_prefers_stored_exact_match(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    folder = campaigns_dir / "Valenthal"
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text("{}", encoding="utf-8")
    registry = {"campaigns": [{"name": "Valenthal", "world_name": "My Foundry World", "folder": str(folder)}]}
    (campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).write_text(json.dumps(registry), encoding="utf-8")

    found = obsidian_sync.find_campaign_by_world("My Foundry World", vault_path=str(tmp_path))
    assert found == "Valenthal"


def test_find_campaign_by_world_falls_back_to_fuzzy_match(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    folder = campaigns_dir / "Valenthal Reborn"
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps({"campaign_name": "Valenthal Reborn"}), encoding="utf-8")

    found = obsidian_sync.find_campaign_by_world("valenthal-reborn", vault_path=str(tmp_path))
    assert found == "Valenthal Reborn"


def test_find_campaign_by_world_returns_none_when_nothing_matches(tmp_path):
    assert obsidian_sync.find_campaign_by_world("Nothing Here", vault_path=str(tmp_path)) is None


# ── link_world_to_campaign / get_campaign_world ──────────────────────────────

def test_link_world_to_campaign_persists_world_name_and_id(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    folder = campaigns_dir / "Valenthal"
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text("{}", encoding="utf-8")
    registry = {"campaigns": [{"name": "Valenthal", "folder": str(folder)}]}
    (campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).write_text(json.dumps(registry), encoding="utf-8")

    ok = obsidian_sync.link_world_to_campaign("Valenthal", "My World", "world-123", str(tmp_path))
    assert ok is True

    world = obsidian_sync.get_campaign_world("Valenthal", str(tmp_path))
    assert world == {"world_name": "My World", "world_id": "world-123"}


def test_link_world_to_campaign_returns_false_without_registry_file(tmp_path):
    assert obsidian_sync.link_world_to_campaign("Anything", "World", vault_path=str(tmp_path)) is False


def test_link_world_to_campaign_returns_false_when_campaign_not_found(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    campaigns_dir.mkdir(parents=True)
    (campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).write_text(
        json.dumps({"campaigns": [{"name": "Other"}]}), encoding="utf-8"
    )

    assert obsidian_sync.link_world_to_campaign("Valenthal", "World", vault_path=str(tmp_path)) is False


def test_link_world_to_campaign_fails_closed_on_bad_registry_json(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    campaigns_dir.mkdir(parents=True)
    (campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).write_text("not json", encoding="utf-8")

    assert obsidian_sync.link_world_to_campaign("Valenthal", "World", vault_path=str(tmp_path)) is False


def test_get_campaign_world_returns_none_when_campaign_unknown(tmp_path):
    assert obsidian_sync.get_campaign_world("Nope", str(tmp_path)) is None


# ── delete_campaign ───────────────────────────────────────────────────────────

def test_delete_campaign_removes_folder_and_registry_entry(tmp_path):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    folder = campaigns_dir / "ToDelete"
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text("{}", encoding="utf-8")
    registry = {"campaigns": [{"name": "ToDelete"}, {"name": "KeepMe"}]}
    (campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).write_text(json.dumps(registry), encoding="utf-8")

    ok = asyncio.run(obsidian_sync.delete_campaign("ToDelete", str(tmp_path)))

    assert ok is True
    assert not folder.exists()
    remaining = json.loads((campaigns_dir / obsidian_sync.REGISTRY_FILE_NAME).read_text(encoding="utf-8"))
    assert [c["name"] for c in remaining["campaigns"]] == ["KeepMe"]


def test_delete_campaign_returns_false_when_folder_does_not_exist(tmp_path):
    assert asyncio.run(obsidian_sync.delete_campaign("Ghost", str(tmp_path))) is False


def test_delete_campaign_fails_closed_on_path_traversal_attempt(tmp_path):
    """A campaign name crafted to escape the vault must not delete anything
    outside Campaigns/, and must not raise — it reports failure instead."""
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    campaigns_dir.mkdir(parents=True)
    sibling = tmp_path / "sibling_dir"
    sibling.mkdir()
    (sibling / "do_not_delete.txt").write_text("precious", encoding="utf-8")

    ok = asyncio.run(obsidian_sync.delete_campaign("../sibling_dir", str(tmp_path)))

    assert ok is False
    assert (sibling / "do_not_delete.txt").exists()


def test_delete_campaign_returns_false_when_rmtree_raises(tmp_path, monkeypatch):
    campaigns_dir = tmp_path / obsidian_sync.CAMPAIGNS_DIR_NAME
    folder = campaigns_dir / "Stubborn"
    folder.mkdir(parents=True)

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(obsidian_sync.shutil, "rmtree", boom)

    ok = asyncio.run(obsidian_sync.delete_campaign("Stubborn", str(tmp_path)))

    assert ok is False
    assert folder.exists()
