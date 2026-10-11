"""Coverage-gap tests for campaign.obsidian_sync: registry recovery, canon push,
asset sync, campaign listing / world matching / deletion edge cases."""

import asyncio
import json
import os
from unittest.mock import MagicMock


from campaign import obsidian_sync as obs


def _reg(vault):
    d = vault / obs.CAMPAIGNS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d / obs.REGISTRY_FILE_NAME


def _manifest(vault, name="Camp"):
    return {"vault_path": str(vault), "campaign_name": name, "campaign_folder": str(vault / name),
            "saved_at": "now", "stats": {"scenes": 2, "npcs": 3, "quests": 1}}


def _write_campaign(vault, name, data=None):
    folder = obs.get_campaign_folder(vault, name)
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps(data if data is not None else {"campaign": {"name": name}}))
    return folder


def test_registry_corrupt_file_that_cannot_be_moved_is_still_replaced(tmp_path, monkeypatch):
    reg = _reg(tmp_path)
    reg.write_text("{not json")
    real = os.replace

    def picky(src, dst):
        if str(dst).endswith(".corrupt"):
            raise OSError("read-only")
        return real(src, dst)

    monkeypatch.setattr(obs.os, "replace", picky)
    out = asyncio.run(obs.save_campaign_registry(tmp_path / "f", _manifest(tmp_path)))
    assert out == str(reg)
    saved = json.loads(reg.read_text())
    assert saved["campaigns"] == [{"name": "Camp", "folder": str(tmp_path / "Camp"), "saved_at": "now",
                                   "total_scenes": 2, "total_npcs": 3, "total_quests": 1}]
    assert not reg.with_name(reg.name + ".corrupt").exists()


def test_registry_corrupt_file_is_preserved_as_dot_corrupt(tmp_path):
    reg = _reg(tmp_path)
    reg.write_text("{not json")
    asyncio.run(obs.save_campaign_registry(tmp_path / "f", _manifest(tmp_path)))
    assert reg.with_name(reg.name + ".corrupt").read_text() == "{not json"


def test_push_canon_fact_live_without_llm_manager_only_writes_file(tmp_path):
    f = asyncio.run(obs.push_canon_fact_live(tmp_path, "The sun is black.", None))
    assert "The sun is black." in f.read_text()


def test_push_canon_fact_live_with_llm_manager_sets_dynamic_context(tmp_path):
    llm = MagicMock()
    f = asyncio.run(obs.push_canon_fact_live(tmp_path, "Fact two.", llm))
    arg = llm.set_dynamic_canon_context.call_args.args[0]
    assert arg == f"## Canon / Established Facts ##\n{f.read_text(encoding='utf-8')}"
    assert "Fact two." in arg


def test_sync_campaign_to_vault_writes_artifacts_and_factions(tmp_path):
    data = {"campaign": {"name": "Camp"}, "artifacts": [{"name": "Orb", "description": "glows"}],
            "factions": [{"name": "Knights", "description": "honor"}]}
    manifest = asyncio.run(obs.sync_campaign_to_vault(data, str(tmp_path)))
    folder = obs.get_campaign_folder(tmp_path, "Camp")
    names = {p.name for p in folder.rglob("*.md")}
    assert any("Artifact" in n for n in names) and any("Faction" in n for n in names)
    assert manifest["campaign_name"] == "Camp"


def test_sync_assets_copies_maps_and_portraits_and_reports_missing(tmp_path):
    assets = tmp_path / "assets"
    (assets / "portraits").mkdir(parents=True)
    (assets / "scene.png").write_bytes(b"s")
    (assets / "loc.png").write_bytes(b"l")
    (assets / "portraits" / "bob.png").write_bytes(b"b")
    data = {"campaign": {"name": "Camp"},
            "scenes": [{"name": "S", "map_file": "scene.png"}, {"name": "NoMap"}],
            "locations": [{"name": "L", "map_file": "loc.png"}, {"name": "L2"}],
            "npcs": [{"name": "Bob", "portrait_file": "bob.png"}, {"name": "Ann", "portrait_file": "ann.png"}]}
    res = asyncio.run(obs.sync_assets_to_vault("Camp", data, assets, str(tmp_path / "v")))
    assert res == {"copied": 3, "missing": ["ann.png"]}
    folder = obs.get_campaign_folder(tmp_path / "v", "Camp")
    assert (folder / "Maps" / "scene.png").read_bytes() == b"s"
    assert (folder / "Maps" / "loc.png").read_bytes() == b"l"
    assert (folder / "Portraits" / "bob.png").read_bytes() == b"b"


def test_sync_assets_copy_failure_is_reported_as_missing(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "scene.png").write_bytes(b"s")
    monkeypatch.setattr(obs.shutil, "copy2", MagicMock(side_effect=OSError("full")))
    data = {"campaign": {"name": "Camp"}, "scenes": [{"name": "S", "map_file": "scene.png"}]}
    res = asyncio.run(obs.sync_assets_to_vault("Camp", data, assets, str(tmp_path / "v")))
    assert res == {"copied": 0, "missing": ["scene.png"]}


def test_list_campaigns_registry_filters_stale_and_corrupt_falls_back_to_scan(tmp_path):
    live = _write_campaign(tmp_path, "Live")
    reg = _reg(tmp_path)
    reg.write_text(json.dumps({"campaigns": [{"name": "Live", "folder": str(live)},
                                             {"name": "Gone", "folder": str(tmp_path / "gone")}]}))
    assert [c["name"] for c in obs.list_campaigns(str(tmp_path))] == ["Live"]
    reg.write_text("{broken")
    assert [c["name"] for c in obs.list_campaigns(str(tmp_path))] == ["Live"]  # scanned from disk


def test_list_campaigns_scan_skips_non_campaign_dirs_and_empty_manifests(tmp_path):
    base = tmp_path / obs.CAMPAIGNS_DIR_NAME
    (base / "stray").mkdir(parents=True)
    (base / "afile.txt").write_text("x")
    _write_campaign(tmp_path, "Named", {"campaign": {"name": "Real Name"}, "saved_at": "t"})
    empty = obs.get_campaign_folder(tmp_path, "Empty")
    empty.mkdir()
    (empty / "campaign.json").write_text("null")
    found = obs.list_campaigns(str(tmp_path))
    assert [c["name"] for c in found] == ["Real Name"] and found[0]["saved_at"] == "t"


def test_list_campaigns_missing_dir_is_empty(tmp_path):
    assert obs.list_campaigns(str(tmp_path / "nowhere")) == []


def test_find_campaign_by_world_exact_fuzzy_and_no_match(tmp_path):
    reg = _reg(tmp_path)
    f1, f2 = _write_campaign(tmp_path, "Krynn Saga"), _write_campaign(tmp_path, "Other")
    reg.write_text(json.dumps({"campaigns": [
        {"name": "Krynn Saga", "folder": str(f1)},
        {"name": "!!!", "folder": str(f2)},
        {"name": "Other", "folder": str(f2), "world_name": "Linked World"},
    ]}))
    v = str(tmp_path)
    assert obs.find_campaign_by_world("Linked World", vault_path=v) == "Other"           # stored link
    assert obs.find_campaign_by_world("The Krynn Saga Of Heroes", vault_path=v) == "Krynn Saga"  # fuzzy via title
    assert obs.find_campaign_by_world("zzz", "krynn-saga", vault_path=v) == "Krynn Saga"          # fuzzy via id
    assert obs.find_campaign_by_world("Totally Different", "nope", vault_path=v) is None
    assert obs.find_campaign_by_world("x", vault_path=str(tmp_path / "empty")) is None


def test_link_world_to_campaign_outcomes(tmp_path):
    v = str(tmp_path)
    assert obs.link_world_to_campaign("C", "W", vault_path=v) is False          # no registry yet
    f = _write_campaign(tmp_path, "C")
    reg = _reg(tmp_path)
    reg.write_text(json.dumps({"campaigns": [{"name": "C", "folder": str(f)}]}))
    assert obs.link_world_to_campaign("C", "World A", vault_path=v) is True
    entry = json.loads(reg.read_text())["campaigns"][0]
    assert entry["world_name"] == "World A" and "world_id" not in entry
    assert obs.link_world_to_campaign("C", "World A", "wid", vault_path=v) is True
    assert json.loads(reg.read_text())["campaigns"][0]["world_id"] == "wid"
    assert obs.link_world_to_campaign("Missing", "W", vault_path=v) is False
    reg.write_text("{bad")
    assert obs.link_world_to_campaign("C", "W", vault_path=v) is False
    assert obs.get_campaign_world("C", v) == {"world_name": "", "world_id": ""}   # registry unreadable -> scan
    assert obs.get_campaign_world("Nope", v) is None


def test_delete_campaign_removes_folder_and_registry_entry_and_tolerates_bad_registry(tmp_path):
    v = str(tmp_path)
    f = _write_campaign(tmp_path, "C")
    reg = _reg(tmp_path)
    reg.write_text(json.dumps({"campaigns": [{"name": "C", "folder": str(f)}, {"name": "K", "folder": "x"}]}))
    assert asyncio.run(obs.delete_campaign("C", v)) is True
    assert not f.exists()
    assert [c["name"] for c in json.loads(reg.read_text())["campaigns"]] == ["K"]
    f2 = _write_campaign(tmp_path, "D")
    reg.write_text("{bad")
    assert asyncio.run(obs.delete_campaign("D", v)) is True and not f2.exists()
    assert asyncio.run(obs.delete_campaign("Never Existed", v)) is False


def test_delete_campaign_reports_failed_rmtree(tmp_path, monkeypatch):
    f = _write_campaign(tmp_path, "C")
    monkeypatch.setattr(obs.shutil, "rmtree", MagicMock(side_effect=PermissionError("locked")))
    assert asyncio.run(obs.delete_campaign("C", str(tmp_path))) is False
    assert f.exists()
