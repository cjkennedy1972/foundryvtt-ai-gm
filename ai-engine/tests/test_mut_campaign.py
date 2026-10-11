"""Mutation-audit tests: importer scan rules, enrichment splitting/skip, cinematic upload guards, checkpoint cleanup."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import campaign.orchestrator_assets  # noqa: F401  (patch target)
from campaign import orchestrator_enrich as oe
from campaign.importer import scan_product_folder
from campaign.orchestrator import CampaignOrchestrator
from test_cov_campaign_enrich import _kind, _llm, _resp, _run, _vault
from test_campaign_orchestrator_import import _FakeResponse


# 1 / 10 ---------------------------------------------------------------------
def test_dir_with_lock_but_no_current_is_still_scanned(tmp_path):
    d = tmp_path / "stuff"
    d.mkdir()
    (d / "LOCK").write_bytes(b"x")
    (d / "a.pdf").write_bytes(b"x")
    assert scan_product_folder(str(tmp_path))["total_files"] == 2
    (d / "CURRENT").write_bytes(b"x")  # a real LevelDB store: skipped
    assert scan_product_folder(str(tmp_path))["total_files"] == 0


def test_adventure_name_only_promotes_pdfs_in_generic_dirs(tmp_path):
    (tmp_path / "adventure notes.txt").write_bytes(b"x")
    (tmp_path / "Handouts").mkdir()
    (tmp_path / "Handouts" / "adventure.pdf").write_bytes(b"x")
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "adventure.pdf").write_bytes(b"x")
    res = scan_product_folder(str(tmp_path))
    assert [Path(p).parent.name for p in res["adventure_pdfs"]] == ["book"]
    assert any(p.endswith("adventure notes.txt") for p in res["unmatched"])


# 4 --------------------------------------------------------------------------
def test_halve_ignores_a_break_past_three_quarters_and_cuts_at_the_middle():
    text = "x" * 9000 + "\n\n" + "y" * 1000
    first, second = oe._halve([text])
    assert first[0] == text[: len(text) // 2] and first[0] != "x" * 9000
    assert second[0].endswith("y" * 1000)


# 11 -------------------------------------------------------------------------
def test_already_added_source_is_skipped_without_an_llm_call(tmp_path):
    vault, _ = _vault(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text(" ".join(f"word{i}" for i in range(60)) + " Soth rules.")

    async def handler(url, headers=None, json=None, timeout=None):
        k = _kind(json)
        if k == "delta":
            return _resp("===WORLD===\nNone\n===HISTORY===\nNone\n===CONFLICTS===\n===END===")
        return _resp('{"npcs": []}' if k == "entities" else "n")

    first = _run(CampaignOrchestrator(), vault, _llm(handler), source_path=str(src))
    assert first["sources"] and first["skipped"] == []
    llm = _llm(handler)
    again = _run(CampaignOrchestrator(), vault, llm, source_path=str(src))
    assert again["skipped"] == ["a-md"] and again["sources"] == []
    llm.post.assert_not_called()


# 5 --------------------------------------------------------------------------
def _upload(tmp_path, scene):
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    seen, summary = [], {"errors": []}

    async def fake_upload(client, path, folder, name, dest, mime_type=None):
        seen.append(name)
        return {"ok": True, "src": dest}

    with patch("campaign.orchestrator_assets.upload_image", fake_upload):
        asyncio.run(CampaignOrchestrator()._upload_cinematic_art(scene, MagicMock(), assets, "camp", summary))
    return seen, summary


def test_cinematic_file_escaping_the_asset_dir_is_refused(tmp_path):
    (tmp_path / "secret.png").write_bytes(b"x")
    (tmp_path / "assets_evil").mkdir()
    (tmp_path / "assets_evil" / "x.png").write_bytes(b"x")
    for name in ("../secret.png", "../assets_evil/x.png"):  # second shares the "assets" prefix
        seen, summary = _upload(tmp_path, {"name": "S", "_cinematic_still_file": name})
        assert seen == []
        assert any("outside the asset directory" in e for e in summary["errors"])


def test_cinematic_file_with_other_extension_is_not_uploaded(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "x.txt").write_bytes(b"x")
    (tmp_path / "assets" / "ok.png").write_bytes(b"x")
    seen, summary = _upload(tmp_path, {"_cinematic_still_file": "x.txt", "_cinematic_clip_file": "ok.png"})
    assert seen == ["ok.png"] and summary["errors"] == []


# 12 -------------------------------------------------------------------------
def test_checkpoint_file_is_removed_after_a_successful_import(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vault, product = tmp_path / "vault", tmp_path / "product"
    vault.mkdir()
    product.mkdir()
    entries = [{"name": "Chapter 1: Beginnings", "pages": [{"name": "p1", "html": "<p>" + "CHAPTERONE " * 30 + "</p>"}]}]
    ckpt = tmp_path / "campaign_assets" / "ckpt test" / "import_checkpoint.json"
    ckpt.parent.mkdir(parents=True)
    ckpt.write_text("{}")  # stale/mismatched: ignored, but must still be cleaned up on success

    class Client:
        async def post(self, url, headers=None, json=None, timeout=None):
            system = json["messages"][0]["content"]
            if "extracting GM notes" in system:
                return _FakeResponse("## NPCs\n- Someone")
            if "converting extracted GM notes" in system:
                return _FakeResponse(__import__("json").dumps(
                    {"campaign": {"name": "Ckpt Test"}, "scenes": [{"name": "A"}], "npcs": [], "locations": [], "quest_logs": []}))
            if "world lore documents" in system:
                return _FakeResponse("===WORLDBUILDING===\nX\n===HISTORY===\nY\n===END===")
            raise AssertionError(system[:80])

    orch = CampaignOrchestrator()
    fc = MagicMock(is_connected=True)
    with patch.object(orch, "_fetch_journal_pack", new_callable=AsyncMock, return_value=entries), \
         patch.object(orch, "_wait_for_foundry_ready", new_callable=AsyncMock), \
         patch.object(orch, "_fetch_world_document_index", new_callable=AsyncMock, return_value=[]), \
         patch.object(CampaignOrchestrator, "build_campaign", new_callable=AsyncMock,
                      return_value={"status": "complete", "steps": []}):
        res = asyncio.run(orch.import_campaign(source_path=str(product), campaign_name="Ckpt Test", llm_client=Client(),
                                               foundry_client=fc, vault_path=str(vault), journal_pack="p"))
    assert res["status"] == "complete"
    assert not ckpt.exists()


# Quartermaster: opt-out and img ---------------------------------------------
def test_quartermaster_honours_deploy_as_pile_false_and_sets_img():
    from campaign.modules.quartermaster import on_loot_table

    table = {"name": "Hoard", "loot_type": "party", "entries": []}
    assert asyncio.run(on_loot_table({**table, "deploy_as_pile": False}, {})) is None
    actor = asyncio.run(on_loot_table(table, {}))
    assert actor["img"].startswith("icons/") and actor["prototypeToken"]["texture"]["src"] == actor["img"]
