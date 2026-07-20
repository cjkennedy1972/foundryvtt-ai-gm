"""Phase 2: orchestrator integration and lore consistency.

- build_campaign short-circuit when campaign_data is provided
- byte-identical prompt when lore_context is empty
- vault fallback uses campaign.json via CampaignStore
- import_campaign end-to-end wiring with a stubbed LLM (scan → extract →
  pass 1/2/3 → validate_campaign auto-fills scene_setup → asset matching →
  lore writeout → delegated build)
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from campaign.generator import generate_arc_extension_prompt
from campaign.orchestrator import CampaignOrchestrator
from campaign.vault import CampaignStore


def test_build_campaign_short_circuits_generation_when_data_provided():
    """If campaign_data is supplied, the LLM generation phase must be skipped."""
    orch = CampaignOrchestrator()
    data = {"campaign": {"name": "Test"}, "npcs": [], "scenes": []}

    with patch.object(orch, "generate_campaign_data", new_callable=AsyncMock) as mock_gen:
        with patch.object(orch, "save_to_vault", new_callable=AsyncMock, return_value={}) as mock_save:
            with patch.object(orch, "generate_assets", new_callable=AsyncMock, return_value={"maps": [], "portraits": []}) as mock_assets:
                with patch.object(orch, "upload_maps_to_foundry", new_callable=AsyncMock, return_value={"uploaded": 0}) as mock_upload_maps:
                    with patch.object(orch, "upload_portraits_to_foundry", new_callable=AsyncMock, return_value={"uploaded": 0}) as mock_upload_portraits:
                        with patch.object(orch, "deploy_to_foundry", new_callable=AsyncMock, return_value={}) as mock_deploy:
                            with patch.object(orch, "enrich_scenes", new_callable=AsyncMock, return_value={"scenes_enriched": 0}) as mock_enrich:
                                result = asyncio.run(orch.build_campaign(
                                    prompt="irrelevant",
                                    campaign_name="Test",
                                    campaign_data=data,
                                ))

    mock_gen.assert_not_called()
    assert result["campaign_data"] == data
    mock_save.assert_awaited()


def test_build_campaign_default_calls_generation():
    """Without campaign_data, the LLM generation phase must still be invoked."""
    orch = CampaignOrchestrator()

    with patch.object(orch, "generate_campaign_data", new_callable=AsyncMock, return_value={
        "campaign": {"name": "Generated"}, "npcs": [], "scenes": []
    }) as mock_gen:
        with patch.object(orch, "save_to_vault", new_callable=AsyncMock, return_value={}) as mock_save:
            with patch.object(orch, "generate_assets", new_callable=AsyncMock, return_value={"maps": [], "portraits": []}) as mock_assets:
                with patch.object(orch, "upload_maps_to_foundry", new_callable=AsyncMock, return_value={"uploaded": 0}) as mock_upload_maps:
                    with patch.object(orch, "upload_portraits_to_foundry", new_callable=AsyncMock, return_value={"uploaded": 0}) as mock_upload_portraits:
                        with patch.object(orch, "deploy_to_foundry", new_callable=AsyncMock, return_value={}) as mock_deploy:
                            with patch.object(orch, "enrich_scenes", new_callable=AsyncMock, return_value={"scenes_enriched": 0}) as mock_enrich:
                                result = asyncio.run(orch.build_campaign(
                                    prompt="A test campaign",
                                    campaign_name="Test",
                                ))

    mock_gen.assert_awaited_once()


def test_arc_extension_prompt_byte_identical_without_lore():
    """When lore_context is empty the prompt output must be byte-identical to
    the pre-lore-injection baseline (no lore block inserted)."""
    data = {
        "campaign": {
            "name": "Demo",
            "description": "A demo world",
            "theme": "Dark fantasy",
            "level_range": "1-5",
        },
        "scenes": [],
        "npcs": [],
        "quest_logs": [],
        "story_arcs": [],
    }
    prompt_default = generate_arc_extension_prompt(data, current_level=1, arc_number=2)
    prompt_empty = generate_arc_extension_prompt(data, current_level=1, arc_number=2, lore_context="")
    assert prompt_default == prompt_empty
    assert "Established World Lore" not in prompt_empty


def test_arc_extension_prompt_injects_lore_when_present():
    """Non-empty lore_context inserts the established-lore block."""
    data = {
        "campaign": {
            "name": "Demo",
            "description": "A demo world",
            "theme": "Dark fantasy",
            "level_range": "1-5",
        },
        "scenes": [],
        "npcs": [],
        "quest_logs": [],
        "story_arcs": [],
    }
    prompt = generate_arc_extension_prompt(data, current_level=1, arc_number=2, lore_context="Dragons are extinct.")
    assert "Established World Lore (STAY CONSISTENT" in prompt
    assert "Dragons are extinct." in prompt


def test_vault_store_campaign_file_is_json():
    """CampaignStore must resolve to campaign.json, not campaign_data.json."""
    with tempfile.TemporaryDirectory() as tmp:
        store = CampaignStore("Test Campaign", vault_path=tmp)
        assert store.campaign_file.name == "campaign.json"
        assert "campaign_data.json" not in str(store.campaign_file)


# ─── import_campaign end-to-end wiring (stubbed LLM) ──────────────────────


class _FakeResponse:
    def __init__(self, content: str):
        self.status_code = 200
        self._content = content
        self.text = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _StubLLMClient:
    """Routes chat calls by which pass system-prompt is present."""

    _json_mod = json  # capture module; the post() `json` kwarg shadows it

    def __init__(self, campaign_payload: dict):
        self.campaign_payload = campaign_payload
        self.pass1_calls = 0
        self.pass2_calls = 0
        self.pass3_calls = 0

    async def post(self, url, headers=None, json=None, timeout=None):
        system = json["messages"][0]["content"]
        if "extracting GM notes" in system:            # Pass 1
            self.pass1_calls += 1
            return _FakeResponse(
                "## NPCs\n- Sir Valor\n## Scenes\n- The Gilded Tavern\n- Deep Forest"
            )
        if "converting extracted GM notes" in system:  # Pass 2 (_PASS2_SYSTEM)
            self.pass2_calls += 1
            return _FakeResponse(self._json_mod.dumps(self.campaign_payload))
        if "world lore documents" in system:           # Pass 3 (_PASS3_SYSTEM)
            self.pass3_calls += 1
            return _FakeResponse(
                "===WORLDBUILDING===\nSetting facts.\n"
                "===HISTORY===\nPast events.\n===END==="
            )
        raise AssertionError(f"Unexpected LLM call: {system[:120]}")


def test_import_campaign_end_to_end_with_stubbed_llm(tmp_path, monkeypatch):
    """Full import_campaign run against a synthetic product folder.

    Proves: pass-2 reduce → validate_campaign auto-fills scene_setup; matched
    maps/tokens pre-place files + clear flags; unmatched scenes keep
    map_needed=True; handouts land as pdf journal entries; lore .md files are
    written into the vault; build_campaign is delegated the mutated
    campaign_data via the short-circuit kwarg.
    """
    pytest = __import__("pytest")
    pytest.importorskip("PIL")
    from PIL import Image

    # ── Synthetic product folder ──
    product = tmp_path / "product"
    (product / "Maps").mkdir(parents=True)
    (product / "Tokens").mkdir(parents=True)
    (product / "Handouts").mkdir(parents=True)
    Image.new("RGB", (640, 480)).save(
        str(product / "Maps" / "72DPI_Gridless_Gilded_Tavern.jpg"), "JPEG")
    Image.new("RGBA", (200, 200)).save(
        str(product / "Tokens" / "Sir_Valor_CLOSEUP.png"), "PNG")
    (product / "Handouts" / "player_letter.pdf").write_bytes(b"%PDF-1.4 fake")
    (product / "adventure_module.pdf").write_bytes(b"%PDF-1.4 fake")

    # ── Pass-2 LLM output: deliberately NO scene_setup (validate must fill) ──
    campaign_payload = {
        "campaign": {"name": "Imported Test", "description": "From a book"},
        "scenes": [
            {"name": "The Gilded Tavern", "type": "tavern", "map_needed": True},
            {"name": "Deep Forest", "type": "wilderness", "map_needed": True},
        ],
        "npcs": [{"name": "Sir Valor", "description": "A knight"}],
        "locations": [{"name": "Town"}],
        "quest_logs": [],
        "story_arcs": [],
    }

    # ── extract_pdf_text: 3 heavy pages → 2 chunks at the 12k-token default ──
    fake_pages = [(1, "a" * 20000), (2, "b" * 20000), (3, "c" * 20000)]
    monkeypatch.setattr(
        "campaign.importer.extract_pdf_text",
        lambda pdf_path, min_chars_per_page=50: fake_pages,
    )
    # Keep ./campaign_assets writes inside tmp_path
    monkeypatch.chdir(tmp_path)

    vault = tmp_path / "vault"
    vault.mkdir()
    orch = CampaignOrchestrator()
    stub = _StubLLMClient(campaign_payload)

    with patch.object(
        CampaignOrchestrator, "build_campaign",
        new_callable=AsyncMock,
        return_value={"status": "complete", "steps": []},
    ) as mock_build:
        result = asyncio.run(orch.import_campaign(
            source_path=str(product),
            campaign_name="Imported Test",
            llm_client=stub,
            foundry_client=None,
            vault_path=str(vault),
        ))

    # ── LLM call shape: pass1 per chunk, pass2 + pass3 once each ──
    assert stub.pass1_calls == 2
    assert stub.pass2_calls == 1
    assert stub.pass3_calls == 1

    # ── Delegated to build_campaign with campaign_data kwarg ──
    mock_build.assert_awaited_once()
    passed_data = mock_build.call_args.kwargs["campaign_data"]
    assert passed_data["imported_from"] == str(product)
    assert "validation_warnings" in passed_data

    scenes = {s["name"]: s for s in passed_data["scenes"]}

    # Matched scene: pre-placed file, flag cleared, grid from image, empty walls
    tavern = scenes["The Gilded Tavern"]
    assert tavern["map_needed"] is False
    assert tavern["map_file"] == "map_the gilded tavern.jpg"
    assert tavern["scene_setup"]["grid_width"] == 10   # 640 // 64
    assert tavern["scene_setup"]["grid_height"] == 7   # 480 // 64
    assert tavern["scene_setup"]["walls"] == []
    assert tavern["scene_setup"]["lights"] == []
    assert tavern["scene_setup"]["sounds"] == []
    assert tavern["_map_width_px"] == 640
    assert tavern["_map_height_px"] == 480

    # Unmatched scene: still needs an AI map; validate_campaign auto-filled setup
    forest = scenes["Deep Forest"]
    assert forest["map_needed"] is True
    assert "map_file" not in forest
    assert "scene_setup" in forest
    assert forest["scene_setup"]["grid_size_px"] == 64

    # Token match: portrait pre-placed under maps_dir/portraits, flag cleared
    npc = passed_data["npcs"][0]
    assert npc["portrait_needed"] is False
    assert npc["portrait_file"] == "token_sir valor.png"

    # Handouts became pdf journal entries with preserved source references
    handouts = [j for j in passed_data["journal_entries"] if j.get("pdf_src")]
    assert len(handouts) == 1
    assert handouts[0]["title"] == "player_letter"

    # ── Files landed where the pipeline expects them ──
    safe = "imported test"
    assert (tmp_path / "campaign_assets" / f"{safe}_maps" / tavern["map_file"]).exists()
    assert (tmp_path / "campaign_assets" / f"{safe}_maps" / "portraits" / npc["portrait_file"]).exists()

    camp_folder = vault / "Campaigns" / "Imported Test"
    assert (camp_folder / "Worldbuilding.md").read_text() == "Setting facts."
    assert (camp_folder / "History.md").read_text() == "Past events."
    assert (camp_folder / "Lore" / "Part 01.md").exists()
    assert (camp_folder / "Lore" / "Part 02.md").exists()
    assert (camp_folder / "Handouts" / "player_letter.md").exists()

    # ── Import summary ──
    summary = result["import_summary"]
    assert summary["chunks_processed"] == 2
    assert summary["maps_matched"] == ["The Gilded Tavern"]
    assert summary["maps_unmatched"] == ["Deep Forest"]
    assert summary["tokens_matched"] == ["Sir Valor"]
    assert summary["handouts"] == ["player_letter"]


def test_import_campaign_passes_requested_level_range_to_validation(tmp_path, monkeypatch):
    """Import validation must honor the requested level range."""
    pytest = __import__("pytest")
    pytest.importorskip("PIL")
    from PIL import Image

    product = tmp_path / "product"
    (product / "Maps").mkdir(parents=True)
    (product / "Tokens").mkdir(parents=True)
    (product / "Handouts").mkdir(parents=True)
    Image.new("RGB", (640, 480)).save(
        str(product / "Maps" / "72DPI_Gridless_Gilded_Tavern.jpg"), "JPEG")
    (product / "adventure_module.pdf").write_bytes(b"%PDF-1.4 fake")

    campaign_payload = {
        "campaign": {"name": "Imported Test", "description": "From a book"},
        "scenes": [{"name": "The Gilded Tavern", "type": "tavern", "map_needed": True}],
        "npcs": [{"name": "Sir Valor", "description": "A knight"}],
        "locations": [{"name": "Town"}],
        "quest_logs": [],
        "story_arcs": [],
    }

    fake_pages = [(1, "a" * 20000)]
    monkeypatch.setattr(
        "campaign.importer.extract_pdf_text",
        lambda pdf_path, min_chars_per_page=50: fake_pages,
    )
    monkeypatch.chdir(tmp_path)

    vault = tmp_path / "vault"
    vault.mkdir()
    orch = CampaignOrchestrator()
    stub = _StubLLMClient(campaign_payload)

    with patch(
        "campaign.generator.validate_campaign",
        return_value=[],
    ) as mock_validate:
        with patch.object(
            CampaignOrchestrator, "build_campaign",
            new_callable=AsyncMock,
            return_value={"status": "complete", "steps": []},
        ):
            asyncio.run(orch.import_campaign(
                source_path=str(product),
                campaign_name="Imported Test",
                llm_client=stub,
                foundry_client=None,
                vault_path=str(vault),
                level_range="10-12",
            ))

    mock_validate.assert_called_once()
    assert mock_validate.call_args.kwargs["level_range"] == "10-12"


def test_import_campaign_fails_fast_on_icloud_placeholders(tmp_path):
    """A 0-byte iCloud placeholder aborts the import with brctl guidance."""
    product = tmp_path / "product"
    (product / "Maps").mkdir(parents=True)
    (product / "Maps" / "map.jpg").write_bytes(b"")  # 0-byte placeholder

    orch = CampaignOrchestrator()
    result = asyncio.run(orch.import_campaign(
        source_path=str(product),
        campaign_name="Placeholder Test",
        llm_client=_StubLLMClient({}),
        foundry_client=None,
        vault_path=str(tmp_path / "vault"),
    ))
    assert result["status"] == "error"
    assert "0-byte iCloud placeholder" in result["error"]
    assert "brctl download" in result["error"]


def test_import_campaign_errors_without_extractable_text(tmp_path, monkeypatch):
    """No extractable PDF text → clean error, no LLM calls."""
    product = tmp_path / "product"
    product.mkdir()
    (product / "adventure.pdf").write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "campaign.importer.extract_pdf_text",
        lambda pdf_path, min_chars_per_page=50: [],
    )
    stub = _StubLLMClient({})
    orch = CampaignOrchestrator()
    result = asyncio.run(orch.import_campaign(
        source_path=str(product),
        campaign_name="Empty Test",
        llm_client=stub,
        foundry_client=None,
        vault_path=str(tmp_path / "vault"),
    ))
    assert result["status"] == "error"
    assert "No text could be extracted" in result["error"]
    assert stub.pass1_calls == 0 and stub.pass2_calls == 0 and stub.pass3_calls == 0


# ─── Pre-placed asset upload gating in build_campaign ─────────────────────


def _run_build_with_premade_map(premade: bool):
    """Drive build_campaign with a scene that may carry a pre-placed map_file.

    Returns (upload_maps_awaited, generate_awaited). generate_assets is stubbed
    to report zero generated maps — the import-mode case where every scene map
    came from the product folder.
    """
    orch = CampaignOrchestrator()
    scene = {"name": "Tavern", "map_needed": not premade}
    if premade:
        scene["map_file"] = "map_tavern.jpg"
    data = {"campaign": {"name": "Gate Test"}, "scenes": [scene], "npcs": []}

    foundry = AsyncMock()
    foundry.is_connected = True

    with patch.object(orch, "scan_foundry_world", new_callable=AsyncMock, return_value={}), \
         patch.object(orch, "save_to_vault", new_callable=AsyncMock, return_value={}), \
         patch.object(orch, "generate_assets", new_callable=AsyncMock,
                      return_value={"maps": [], "portraits": [], "status": "completed",
                                    "total_maps": 0, "total_portraits": 0,
                                    "total_prologue_panels": 0}) as m_gen, \
         patch.object(orch, "upload_maps_to_foundry", new_callable=AsyncMock,
                      return_value={"uploaded": 0}) as m_upload, \
         patch.object(orch, "deploy_to_foundry", new_callable=AsyncMock, return_value={}), \
         patch.object(orch, "enrich_scenes", new_callable=AsyncMock, return_value={}):
        asyncio.run(orch.build_campaign(
            prompt="irrelevant", campaign_name="Gate Test",
            foundry_client=foundry, campaign_data=data,
        ))
    return m_upload.await_count > 0, m_gen.await_count > 0


def test_premade_map_uploads_even_when_nothing_generated():
    """Import mode: all maps pre-placed (total_maps == 0) must still upload."""
    upload_awaited, gen_awaited = _run_build_with_premade_map(premade=True)
    assert upload_awaited is True
    assert gen_awaited is True  # generate_assets still runs (portraits/prologue)


def test_no_maps_no_upload():
    """Generated path unchanged: no map_file anywhere → upload stays skipped."""
    upload_awaited, _ = _run_build_with_premade_map(premade=False)
    assert upload_awaited is False
