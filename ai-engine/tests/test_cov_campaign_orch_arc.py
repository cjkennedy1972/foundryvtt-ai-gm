"""Coverage-gap tests for CampaignOrchestrator: extend_campaign_arc load/save
fallbacks, lore injection, map-generator init failure, and enrich_scenes
existing-placeable handling."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.orchestrator import CampaignOrchestrator

ARC_JSON = json.dumps({
    "campaign": {"arc_title": "Second Dawn", "arc_level_range": "5-10"},
    "scenes": [{"name": "New Scene"}], "npcs": [], "encounters": [], "quest_logs": [],
    "locations": [], "story_arcs": [],
})


def _llm():
    r = MagicMock(status_code=200, text="")
    r.json = MagicMock(return_value={"choices": [{"message": {"content": ARC_JSON}}]})
    c = MagicMock()
    c.post = AsyncMock(return_value=r)
    return c


class _MapGen:
    fail = False

    def __init__(self, **kw):
        if _MapGen.fail:
            raise RuntimeError("comfy down")
        self.closed = False

    async def close(self):
        self.closed = True


def _orch():
    o = CampaignOrchestrator()
    o.generate_assets = AsyncMock(return_value={"total_maps": 0, "total_portraits": 0})
    return o


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _MapGen.fail = False
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _MapGen)


def _vault_campaign(tmp_path, name="Oakhaven", data=None):
    from campaign.obsidian_sync import get_campaign_folder

    vault = tmp_path / "vault"
    folder = get_campaign_folder(vault, name)
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps(data or {"campaign": {"name": name}, "scenes": []}))
    return vault, folder


def _extend(orch, **kw):
    msgs = []
    kw.setdefault("on_progress", lambda m, s="", d="": msgs.append(m))
    res = asyncio.run(orch.extend_campaign_arc("Oakhaven", current_level=5, llm_client=_llm(), **kw))
    return res, msgs


def test_extend_loads_from_vault_when_no_state_file_and_unreadable_state_is_skipped(tmp_path):
    vault, folder = _vault_campaign(tmp_path, data={"campaign": {"name": "Oakhaven"}, "scenes": [{"name": "Old"}]})
    state = tmp_path / "campaign_assets" / "oakhaven" / "deployment_state.json"
    state.mkdir(parents=True)            # a directory: exists() but cannot be read as a file
    orch = _orch()
    orch.save_to_vault = AsyncMock()
    res, msgs = _extend(orch, vault_path=str(vault))
    assert any(m.startswith("⚠️ Could not read") and "deployment_state.json" in m for m in msgs)
    assert any(m.startswith("✅ Loaded campaign from") and "campaign.json" in m for m in msgs)
    assert res["status"] == "complete" and res["arc_number"] == 2
    # the vault file is flat (no campaign_data wrapper) and is what got extended
    saved = orch.save_to_vault.await_args.args[0]
    assert [s["name"] for s in saved["scenes"]] == ["Old", "New Scene"]
    # state file is a directory, so the final state save fails without failing the run
    assert any(m.startswith("⚠️ State save failed") for m in msgs)


def test_extend_falls_back_to_legacy_vault_path_when_store_cannot_be_built(tmp_path, monkeypatch):
    vault, folder = _vault_campaign(tmp_path)

    def boom(name, path):
        raise RuntimeError("store broken")

    monkeypatch.setattr("campaign.vault.CampaignStore", boom)
    orch = _orch()
    orch.save_to_vault = AsyncMock()
    res, msgs = _extend(orch, vault_path=str(vault))
    # legacy path is <vault>/Campaigns/<name>/campaign.json, same place the fixture wrote it
    assert res["status"] == "complete"
    assert any("✅ Loaded campaign from" in m for m in msgs)


def test_extend_reports_vault_save_failure_and_map_generator_init_failure(tmp_path):
    vault, _ = _vault_campaign(tmp_path)
    _MapGen.fail = True
    orch = _orch()
    orch.save_to_vault = AsyncMock(side_effect=RuntimeError("disk full"))
    res, msgs = _extend(orch, vault_path=str(vault))
    assert "⚠️ Vault save failed: disk full" in msgs
    assert "⚠️ Map generator init failed: comfy down" in msgs
    assert res["status"] == "complete" and res["assets"] == {}


def _loader(chunks, monkeypatch, desc_len=0):
    class _Loader:
        def __init__(self, vault_path=None):
            pass

        async def load(self, name):
            return None

        def search_vault(self, query, max_results):
            _Loader.query = query
            return chunks

    monkeypatch.setattr("context.loader.CampaignLoader", _Loader)
    return _Loader


def test_arc_lore_injection_joins_truncates_and_survives_a_raising_progress(tmp_path, monkeypatch):
    L = _loader(["a" * 6000, "b" * 6000], monkeypatch)
    existing = {"campaign": {"description": "Desc", "theme": "Doom"}, "story_arcs": [{"name": "Arc One"}]}
    seen = []

    def progress(msg, step="", detail=""):
        seen.append(msg)
        if "Injected" in msg:
            raise RuntimeError("callback bug")

    ctx = asyncio.run(_orch()._arc_save_to_vault("Oakhaven", str(tmp_path), existing, progress))
    assert len(ctx) == 8000 + len("\n...(truncated)") and ctx.endswith("\n...(truncated)")
    assert L.query == "Desc Doom Arc One"
    assert seen == ["📚 Injected 2 lore chunk(s) for consistency"]
    # no vault -> no lookup; loader failure -> empty context
    assert asyncio.run(_orch()._arc_save_to_vault("O", None, existing, progress)) == ""
    _loader([], monkeypatch)
    assert asyncio.run(_orch()._arc_save_to_vault("O", str(tmp_path), existing, progress)) == ""

    def failing(vault_path=None):
        raise RuntimeError("no vault")

    monkeypatch.setattr("context.loader.CampaignLoader", failing)
    assert asyncio.run(_orch()._arc_save_to_vault("O", str(tmp_path), existing, progress)) == ""


def test_extend_on_progress_callback_errors_do_not_abort(tmp_path):
    vault, _ = _vault_campaign(tmp_path)
    orch = _orch()
    orch.save_to_vault = AsyncMock()

    def bad(msg, step="", detail=""):
        raise RuntimeError("cb")

    res = asyncio.run(orch.extend_campaign_arc("Oakhaven", 5, _llm(), vault_path=str(vault), on_progress=bad))
    assert res["status"] == "complete"


# ─── enrich_scenes: existing placeables are not duplicated ────────────────


def _enrich_client(counts):
    c = MagicMock()
    c.is_connected = True
    c.activate_scene_and_wait = AsyncMock()
    c.configure_scene = AsyncMock()
    c.canvas_create = AsyncMock(return_value={"success": True})
    c.execute_js = AsyncMock(return_value=counts)
    return c


def _enrich(counts):
    orch = CampaignOrchestrator()
    orch._scene_setup_to_canvas = lambda setup, grid_size: {
        "walls": [{"w": 1}], "lights": [{"l": 1}], "sounds": [{"s": 1}], "scene_config": {"darkness": 0.5}}
    client = _enrich_client(counts)
    data = {"scenes": [{"name": "Crypt", "scene_setup": {"walls": []}}]}
    dep = {"scenes": [{"name": "Crypt", "status": "created"}]}
    summary = asyncio.run(orch.enrich_scenes(data, client, dep))
    return summary, client


def _kinds(client):
    return [c.args[0] for c in client.canvas_create.await_args_list]


def test_enrich_scenes_skips_categories_scene_already_has():
    summary, client = _enrich({"result": {"walls": 3, "lights": 0, "sounds": 2}})
    assert _kinds(client) == ["lights"]
    client.configure_scene.assert_awaited_once_with({"darkness": 0.5})
    assert summary == {"enriched": 1, "skipped": 0, "errors": []}


def test_enrich_scenes_places_everything_when_counts_are_unreadable():
    for counts in ({"result": "nope"}, "nope", {"result": {"walls": 0, "lights": 0, "sounds": 0}}):
        summary, client = _enrich(counts)
        assert _kinds(client) == ["walls", "lights", "sounds"]
        assert summary["errors"] == []


def test_enrich_scenes_count_lookup_failure_is_ignored():
    orch = CampaignOrchestrator()
    orch._scene_setup_to_canvas = lambda setup, grid_size: {"walls": [{"w": 1}], "lights": [], "sounds": [],
                                                          "scene_config": {}}
    client = _enrich_client(None)
    client.execute_js = AsyncMock(side_effect=RuntimeError("js"))
    out = asyncio.run(orch.enrich_scenes({"scenes": [{"name": "C", "scene_setup": {"x": 1}}]}, client,
                                         {"scenes": [{"name": "C", "status": "created"}]}))
    assert out["enriched"] == 1 and _kinds(client) == ["walls"]
