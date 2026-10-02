"""Coverage for CampaignOrchestrator's scan / generate / vault / enrich-scenes
and master build_campaign pipeline — the parts of orchestrator.py with the
largest real behavior gaps.

Mocks sit at the real seams (foundry_client, llm_client, the mixin methods
build_campaign calls into, and the handful of modules it imports locally) so
these exercise the orchestrator's own control-flow and error-handling, not a
no-op echo of its inputs.
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import campaign.orchestrator as orchestrator_module
from campaign.orchestrator import CampaignOrchestrator


# ─── scan_foundry_world: partial failures are reported, not fatal ──────────


def _foundry_client(**overrides):
    client = MagicMock()
    client.get_world_info = AsyncMock(return_value={"modules": []})
    client.get_scenes = AsyncMock(return_value=[])
    client.get_actors = AsyncMock(return_value=[])
    client.get_users = AsyncMock(return_value=[])
    client.get_rooms = AsyncMock(return_value=[])
    for k, v in overrides.items():
        setattr(client, k, v)
    return client


def test_scan_reports_each_sub_scan_failure_independently():
    client = _foundry_client(
        get_actors=AsyncMock(side_effect=RuntimeError("actors down")),
        get_users=AsyncMock(side_effect=RuntimeError("users down")),
        get_rooms=AsyncMock(side_effect=RuntimeError("rooms down")),
        get_scenes=AsyncMock(return_value=[{"name": "Crypt"}]),
    )

    result = asyncio.run(CampaignOrchestrator().scan_foundry_world(client))

    # Scenes succeeded despite the other three failing — one bad sub-scan
    # does not blank out the others.
    assert result["scenes"] == [{"name": "Crypt"}]
    assert result["actors"] == [] and result["users"] == [] and result["rooms"] == []
    assert result["capabilities"]["has_scenes"] is True
    assert result["capabilities"]["has_actors"] is False


def test_scan_detects_active_modules_and_derives_capabilities():
    client = _foundry_client(get_world_info=AsyncMock(return_value={"modules": [
        {"id": "midi-qol", "active": True, "title": "Midi QOL", "version": "1.0"},
        {"id": "dae", "active": False, "title": "DAE"},   # inactive — must be excluded
    ]}))

    result = asyncio.run(CampaignOrchestrator().scan_foundry_world(client))

    assert result["active_modules"] == {"midi-qol": {"title": "Midi QOL", "version": "1.0"}}
    assert result["capabilities"]["spell_automation"] is True
    assert result["capabilities"]["active_effects"] is False


def test_scan_scenes_failure_leaves_scenes_empty_but_scan_continues():
    client = _foundry_client(get_scenes=AsyncMock(side_effect=RuntimeError("scenes down")))

    result = asyncio.run(CampaignOrchestrator().scan_foundry_world(client))

    assert result["scenes"] == []
    assert result["capabilities"]["has_scenes"] is False


def test_scan_world_info_failure_leaves_modules_empty_but_scan_continues():
    client = _foundry_client(get_world_info=AsyncMock(side_effect=RuntimeError("world info down")))

    result = asyncio.run(CampaignOrchestrator().scan_foundry_world(client))

    assert result["active_modules"] == {}
    assert result["capabilities"]["spell_automation"] is False


# ─── _post_and_parse_campaign_json: non-200 retries then gives up/succeeds ──


def _resp(status_code, text="", content=None):
    r = MagicMock(status_code=status_code, text=text)
    if content is not None:
        r.json = MagicMock(return_value={"choices": [{"message": {"content": content}}]})
    return r


def test_non_200_is_retried_and_succeeds_on_a_later_attempt():
    ok_json = json.dumps({"campaign": {"name": "X"}})
    client = MagicMock()
    client.post = AsyncMock(side_effect=[_resp(500, text="server exploded"), _resp(200, content=ok_json)])
    orch = CampaignOrchestrator()

    data = asyncio.run(orch._post_and_parse_campaign_json(client, "http://fake", {}, {}))

    assert data["campaign"]["name"] == "X"
    assert client.post.await_count == 2


def test_non_200_on_every_attempt_raises_with_the_server_body():
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(503, text="overloaded"))
    orch = CampaignOrchestrator()

    with pytest.raises(Exception, match="503"):
        asyncio.run(orch._post_and_parse_campaign_json(client, "http://fake", {}, {}, max_attempts=2))

    assert client.post.await_count == 2


def test_finish_reason_length_with_malformed_json_still_retries_and_logs():
    """A length cutoff with bad JSON hits the diagnostic branch, not just the
    generic parse-failure path, and still recovers on a later attempt."""
    cut_off = MagicMock(status_code=200, text="")
    cut_off.json = MagicMock(return_value={
        "choices": [{"message": {"content": "{\"campaign\": {"}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 999},
    })
    ok = _resp(200, content=json.dumps({"campaign": {"name": "Recovered"}}))
    client = MagicMock()
    client.post = AsyncMock(side_effect=[cut_off, ok])

    data = asyncio.run(CampaignOrchestrator()._post_and_parse_campaign_json(client, "http://fake", {}, {}))

    assert data["campaign"]["name"] == "Recovered"
    assert client.post.await_count == 2


# ─── _settlement_llm: adapts the HTTP client to SettlementGenerator's API ──


def test_settlement_llm_adapter_posts_and_unwraps_content():
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200, content="Settlement lore here"))
    orch = CampaignOrchestrator()

    text = asyncio.run(orch._settlement_llm(client).generate("describe a village", temperature=0.9))

    assert text == "Settlement lore here"
    sent = client.post.await_args.kwargs["json"]
    assert sent["temperature"] == 0.9 and sent["max_tokens"] == 16384
    assert sent["messages"][0]["content"].startswith("/no_think\n")


def test_settlement_llm_adapter_raises_on_http_error():
    client = MagicMock()
    bad = MagicMock(status_code=500)
    bad.raise_for_status = MagicMock(side_effect=RuntimeError("500"))
    client.post = AsyncMock(return_value=bad)

    with pytest.raises(RuntimeError):
        asyncio.run(CampaignOrchestrator()._settlement_llm(client).generate("p"))


# ─── generate_campaign_data: end-to-end against a stub LLM ─────────────────


def _campaign_json(**arrays):
    base = {"campaign": {"name": "Oakhaven", "theme": "gothic", "description": "d"}}
    base.update(arrays)
    return json.dumps(base)


def test_generate_campaign_data_includes_scan_context_and_validates(monkeypatch):
    """With scan_result scenes/actors present, the prompt gets the
    "Current FoundryVTT World Context" block, and a campaign already at
    the minimum counts doesn't trigger any refill call."""
    full = _campaign_json(
        scenes=[{"name": f"S{i}"} for i in range(6)],
        npcs=[{"name": f"N{i}"} for i in range(6)],
        locations=[{"name": f"L{i}"} for i in range(13)],
        quest_logs=[{"title": f"Q{i}"} for i in range(4)],
        encounters=[{"name": f"E{i}"} for i in range(5)],
        loot_tables=[{"name": f"T{i}"} for i in range(3)],
        factions=[{"name": f"F{i}"} for i in range(2)],
        artifacts=[{"name": f"A{i}"} for i in range(2)],
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200, content=full))
    scan_result = {"scenes": [{"name": "Old Scene"}], "actors": [{"name": "Old Actor"}], "users": ["gm"]}

    orch = CampaignOrchestrator()
    data = asyncio.run(orch.generate_campaign_data("build me a campaign", client, scan_result, level_range="1-5"))

    assert data["campaign"]["name"] == "Oakhaven"
    assert data["generated_prompt"] == "build me a campaign"
    assert "generated_at" in data
    assert data["validation_warnings"] == [] or isinstance(data["validation_warnings"], list)
    # Only the one generation call — nothing was short, so no refill request.
    assert client.post.await_count == 1
    sent_system_prompt = client.post.await_args_list[0].kwargs["json"]["messages"][0]["content"]
    assert "Old Scene" in sent_system_prompt and "Old Actor" in sent_system_prompt


def test_generate_campaign_data_without_scan_result_omits_world_context():
    full = _campaign_json(
        scenes=[{"name": f"S{i}"} for i in range(6)], npcs=[{"name": f"N{i}"} for i in range(6)],
        locations=[{"name": f"L{i}"} for i in range(13)], quest_logs=[{"title": f"Q{i}"} for i in range(4)],
        encounters=[{"name": f"E{i}"} for i in range(5)], loot_tables=[{"name": f"T{i}"} for i in range(3)],
        factions=[{"name": f"F{i}"} for i in range(2)], artifacts=[{"name": f"A{i}"} for i in range(2)],
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200, content=full))

    data = asyncio.run(CampaignOrchestrator().generate_campaign_data("a prompt", client, None))

    sent = client.post.await_args_list[0].kwargs["json"]["messages"][0]["content"]
    assert "Current FoundryVTT World Context" not in sent
    assert data["campaign"]["name"] == "Oakhaven"


def test_generate_campaign_data_triggers_a_refill_when_short():
    """Only 1 scene generated (min is 3 for a 1-5 campaign) — a refill round
    must run and its result gets merged in. Every other array is pre-filled to
    its minimum so this isolates the scenes shortfall specifically."""
    thin = _campaign_json(
        scenes=[{"name": "Only Scene"}],
        npcs=[{"name": f"N{i}"} for i in range(6)],
        locations=[{"name": f"L{i}"} for i in range(13)],
        quest_logs=[{"title": f"Q{i}"} for i in range(4)],
        encounters=[{"name": f"E{i}"} for i in range(5)],
        loot_tables=[{"name": f"T{i}"} for i in range(3)],
        factions=[{"name": f"F{i}"} for i in range(2)],
        artifacts=[{"name": f"A{i}"} for i in range(2)],
    )
    refill = json.dumps({"scenes": [{"name": "Second Scene"}, {"name": "Third Scene"}]})
    client = MagicMock()
    client.post = AsyncMock(side_effect=[_resp(200, content=thin), _resp(200, content=refill)])

    data = asyncio.run(CampaignOrchestrator().generate_campaign_data("p", client, None))

    assert len(data["scenes"]) == 3
    assert client.post.await_count == 2


# ─── _refill_short_arrays: the non-200 branch ──────────────────────────────


def test_refill_identity_of_a_non_dict_item_is_empty_and_never_treated_as_a_duplicate():
    """A stray scalar in a returned array (not a dict) must not crash
    _identity's dict-only field lookups, and is never deduped against."""
    data = {"campaign": {"name": "X"}, "scenes": [{"name": "Only One"}]}
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200, content=json.dumps(
        {"scenes": [{"name": "Second Scene"}, "not-a-dict"]}
    )))

    out = asyncio.run(
        CampaignOrchestrator()._refill_short_arrays(data, client, "http://fake", {}, "1-5", max_rounds=1)
    )

    assert "not-a-dict" in out["scenes"]
    assert any(isinstance(s, dict) and s.get("name") == "Second Scene" for s in out["scenes"])


def test_refill_aborts_cleanly_on_a_non_200_top_up_response():
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(500, text="down"))
    data = {"campaign": {"name": "X"}, "scenes": [{"name": "Only One"}]}

    out = asyncio.run(
        CampaignOrchestrator()._refill_short_arrays(data, client, "http://fake", {}, "1-5", max_rounds=2)
    )

    assert out["scenes"] == [{"name": "Only One"}]      # untouched, not crashed
    assert client.post.await_count == 1                  # no further rounds bought


# ─── save_to_vault: thin wrapper, but a real behavioral delegation ─────────


def test_save_to_vault_delegates_to_obsidian_sync(monkeypatch, tmp_path):
    sentinel = {"campaign_folder": "whatever"}
    called = {}

    async def fake_sync(campaign_data, vault_path):
        called["campaign_data"] = campaign_data
        called["vault_path"] = vault_path
        return sentinel

    monkeypatch.setattr("campaign.obsidian_sync.sync_campaign_to_vault", fake_sync)

    result = asyncio.run(CampaignOrchestrator().save_to_vault({"campaign": {"name": "X"}}, str(tmp_path)))

    assert result is sentinel
    assert called["vault_path"] == str(tmp_path)
    assert called["campaign_data"]["campaign"]["name"] == "X"


def test_save_to_vault_falls_back_to_settings_path_when_none_given(monkeypatch):
    seen = {}

    async def fake_sync(campaign_data, vault_path):
        seen["vault_path"] = vault_path
        return {}

    monkeypatch.setattr("campaign.obsidian_sync.sync_campaign_to_vault", fake_sync)
    monkeypatch.setattr(orchestrator_module.settings, "campaign_vault_path", "/configured/vault")

    asyncio.run(CampaignOrchestrator().save_to_vault({"campaign": {}}, vault_path=None))

    assert seen["vault_path"] == "/configured/vault"


# ─── enrich_scenes ──────────────────────────────────────────────────────────


def _setup_scene(name="Crypt", **extra):
    setup = {"walls": [[0, 0, 1, 0]], "lights": [{"x": 1, "y": 1}], "sounds": [{"path": "a.ogg", "x": 0, "y": 0}]}
    setup.update(extra)
    return {"name": name, "scene_setup": setup}


def _deploy_client():
    client = MagicMock(is_connected=True)
    client.activate_scene_and_wait = AsyncMock(return_value=True)
    client.execute_js = AsyncMock(return_value={"result": {"walls": 0, "lights": 0, "sounds": 0}})
    client.configure_scene = AsyncMock(return_value=True)
    client.canvas_create = AsyncMock(return_value={"success": True})
    return client


def test_enrich_scenes_reports_not_connected_and_does_nothing_else():
    client = MagicMock(is_connected=False)
    summary = asyncio.run(CampaignOrchestrator().enrich_scenes({"scenes": []}, client, {}))
    assert summary["errors"] == ["Foundry not connected — scene enrichment skipped"]
    assert summary["enriched"] == 0


def test_enrich_scenes_skips_a_scene_with_no_setup_block():
    client = _deploy_client()
    campaign_data = {"scenes": [{"name": "Bare Scene"}]}
    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, {"scenes": []}))
    assert summary == {"enriched": 0, "skipped": 1, "errors": []}
    client.activate_scene_and_wait.assert_not_awaited()


def test_enrich_scenes_skips_a_linked_scene_without_touching_its_real_map():
    client = _deploy_client()
    campaign_data = {"scenes": [_setup_scene("Imported Map")]}
    deployment = {"scenes": [{"name": "Imported Map", "status": "linked"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert summary["skipped"] == 1 and summary["enriched"] == 0
    client.activate_scene_and_wait.assert_not_awaited()


def test_enrich_scenes_skips_an_undeployed_scene_and_records_a_warning_free_skip():
    client = _deploy_client()
    campaign_data = {"scenes": [_setup_scene("Ghost Scene")]}
    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, {"scenes": []}))
    assert summary["skipped"] == 1 and summary["errors"] == []


def test_enrich_scenes_places_walls_lights_sounds_and_counts_as_enriched():
    client = _deploy_client()
    campaign_data = {"scenes": [_setup_scene("Crypt")]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}
    progress_calls = []

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(
        campaign_data, client, deployment, on_progress=lambda *a, **k: progress_calls.append(a)
    ))

    assert summary["enriched"] == 1 and summary["errors"] == []
    client.activate_scene_and_wait.assert_awaited_once_with("Crypt", timeout=7)
    client.configure_scene.assert_not_awaited()   # no scene_config in this setup
    assert client.canvas_create.await_count == 3  # walls, lights, sounds
    assert progress_calls, "on_progress should have been called"


def test_enrich_scenes_skips_categories_the_scene_already_has():
    client = _deploy_client()
    client.execute_js = AsyncMock(return_value={"result": {"walls": 5, "lights": 2, "sounds": 1}})
    campaign_data = {"scenes": [_setup_scene("Crypt")]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert summary["enriched"] == 1
    client.canvas_create.assert_not_awaited()   # all three categories already present


def test_enrich_scenes_applies_scene_config_when_present():
    client = _deploy_client()
    campaign_data = {"scenes": [_setup_scene("Crypt", globalLight=False, darkness=0.8)]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    client.configure_scene.assert_awaited_once()


def test_enrich_scenes_a_placement_failure_is_recorded_but_still_counts_enriched():
    client = _deploy_client()
    client.canvas_create = AsyncMock(side_effect=RuntimeError("relay timeout"))
    campaign_data = {"scenes": [_setup_scene("Crypt")]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert summary["enriched"] == 1
    assert any("walls" in e for e in summary["errors"])
    assert any("lights" in e for e in summary["errors"])
    assert any("sounds" in e for e in summary["errors"])


def test_enrich_scenes_canvas_create_success_false_is_treated_as_a_failure():
    """canvas_create can reply success:False without raising — same silent-
    failure shape as the token-move bug; this must still be caught."""
    client = _deploy_client()
    client.canvas_create = AsyncMock(return_value={"success": False, "error": "bad coords"})
    campaign_data = {"scenes": [_setup_scene("Crypt")]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert any("bad coords" in e for e in summary["errors"])


def test_enrich_scenes_a_failed_scene_switch_does_not_abort_enrichment():
    client = _deploy_client()
    client.activate_scene_and_wait = AsyncMock(side_effect=RuntimeError("switch failed"))
    campaign_data = {"scenes": [_setup_scene("Crypt")]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert any("scene switch" in e for e in summary["errors"])
    # Placement is still attempted even though the switch failed.
    assert client.canvas_create.await_count == 3


def test_enrich_scenes_on_progress_exception_is_swallowed():
    client = _deploy_client()
    campaign_data = {"scenes": [_setup_scene("Crypt")]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    def exploding_progress(*a, **k):
        raise RuntimeError("UI gone")

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(
        campaign_data, client, deployment, on_progress=exploding_progress
    ))
    assert summary["enriched"] == 1   # the pipeline kept going


def test_enrich_scenes_counts_check_failure_does_not_abort_placement():
    client = _deploy_client()
    client.execute_js = AsyncMock(side_effect=RuntimeError("js bridge down"))
    campaign_data = {"scenes": [_setup_scene("Crypt")]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert summary["enriched"] == 1 and summary["errors"] == []
    assert client.canvas_create.await_count == 3   # still placed despite the counts check failing


def test_enrich_scenes_scene_config_failure_is_recorded():
    client = _deploy_client()
    client.configure_scene = AsyncMock(side_effect=RuntimeError("config rejected"))
    campaign_data = {"scenes": [_setup_scene("Crypt", darkness=0.5)]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert any("scene config" in e for e in summary["errors"])
    assert summary["enriched"] == 1


def test_enrich_scenes_trap_tile_failure_is_recorded_without_aborting():
    client = _deploy_client()

    async def flaky_canvas_create(kind, docs):
        if kind == "tiles":
            raise RuntimeError("tile placement rejected")
        return {"success": True}
    client.canvas_create = AsyncMock(side_effect=flaky_canvas_create)
    campaign_data = {"scenes": [_setup_scene("Crypt", trap_tiles=[
        {"x": 1, "y": 1, "width": 1, "height": 1, "save_dc": 13, "save_type": "dex", "damage": "2d6"},
    ])]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert any("trap tiles" in e for e in summary["errors"])
    assert summary["enriched"] == 1


def test_enrich_scenes_trap_tile_success_false_is_treated_as_a_failure():
    client = _deploy_client()

    async def flaky_canvas_create(kind, docs):
        if kind == "tiles":
            return {"success": False, "error": "bad tile coords"}
        return {"success": True}
    client.canvas_create = AsyncMock(side_effect=flaky_canvas_create)
    campaign_data = {"scenes": [_setup_scene("Crypt", trap_tiles=[
        {"x": 1, "y": 1, "width": 1, "height": 1, "save_dc": 13, "save_type": "dex", "damage": "2d6"},
    ])]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert any("bad tile coords" in e for e in summary["errors"])


def test_enrich_scenes_places_trap_tiles_and_clears_old_ones_first():
    client = _deploy_client()
    campaign_data = {"scenes": [_setup_scene("Crypt", trap_tiles=[
        {"x": 1, "y": 1, "width": 1, "height": 1, "save_dc": 13, "save_type": "dex", "damage": "2d6"},
    ])]}
    deployment = {"scenes": [{"name": "Crypt", "status": "created"}]}

    summary = asyncio.run(CampaignOrchestrator().enrich_scenes(campaign_data, client, deployment))

    assert summary["enriched"] == 1
    assert client.canvas_create.await_count == 4   # walls, lights, sounds, tiles
    # execute_js was called for the counts check AND the clear-old-tiles js.
    assert client.execute_js.await_count == 2


# ─── build_campaign: the master pipeline ───────────────────────────────────


VALID_CAMPAIGN_DATA = {
    "campaign": {"name": "Oakhaven", "description": "d"},
    "scenes": [], "npcs": [], "locations": [], "quest_logs": [],
}


def _fake_settlement(name):
    from world.settlement import Settlement
    return Settlement(id=name.lower(), name=name, region="the valley", population=200, character="quiet")


class _FakeSettlementIntegration:
    """Stands in for campaign.settlement_integration.SettlementIntegration."""
    result = {}
    fail = False

    def __init__(self, llm):
        self.llm = llm

    async def generate_settlements_from_campaign(self, campaign_data, context, max_settlements=3):
        if self.fail:
            raise RuntimeError("settlement LLM exploded")
        return self.result


class _FakeMapGenerator:
    init_fail = False
    instances = []

    def __init__(self, **kwargs):
        if _FakeMapGenerator.init_fail:
            raise RuntimeError("comfyui unreachable")
        self.closed = False
        _FakeMapGenerator.instances.append(self)

    async def close(self):
        self.closed = True


class _FakeCampaignStore:
    fail = False
    saved = []
    deployments_saved = []

    def __init__(self, name, vault_path):
        self.name = name
        self.campaign_file = "campaign.json"
        self.deployment_file = "deployment_state.json"

    async def save(self, data):
        if _FakeCampaignStore.fail:
            raise RuntimeError("store save failed")
        _FakeCampaignStore.saved.append(data)

    async def save_deployment(self, deployment):
        if _FakeCampaignStore.fail:
            raise RuntimeError("store save failed")
        _FakeCampaignStore.deployments_saved.append(deployment)


def _reset_fakes():
    _FakeSettlementIntegration.result = {}
    _FakeSettlementIntegration.fail = False
    _FakeMapGenerator.init_fail = False
    _FakeMapGenerator.instances = []
    _FakeCampaignStore.fail = False
    _FakeCampaignStore.saved = []
    _FakeCampaignStore.deployments_saved = []


def _patch_build_campaign_seams(monkeypatch, tmp_path, *, sync_assets_fails=False):
    """Patch every module build_campaign imports locally, plus chdir so the
    checkpoint/campaign_assets files it writes land in tmp_path."""
    monkeypatch.chdir(tmp_path)
    _reset_fakes()
    monkeypatch.setattr("campaign.settlement_integration.SettlementIntegration", _FakeSettlementIntegration)
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _FakeMapGenerator)
    monkeypatch.setattr("campaign.vault.CampaignStore", _FakeCampaignStore)

    async def fake_sync_assets(campaign_name, campaign_data, asset_output_dir, vault_path):
        if sync_assets_fails:
            raise RuntimeError("vault image copy failed")
        return {"copied": 0}

    monkeypatch.setattr("campaign.obsidian_sync.sync_assets_to_vault", fake_sync_assets)


def _orch_with_mocks(**overrides):
    orch = CampaignOrchestrator()
    orch.scan_foundry_world = AsyncMock(return_value={"scenes": [], "actors": [], "users": []})
    orch.generate_campaign_data = AsyncMock(return_value=json.loads(json.dumps(VALID_CAMPAIGN_DATA)))
    orch.save_to_vault = AsyncMock(return_value={"campaign_folder": "Oakhaven"})
    orch.generate_assets = AsyncMock(return_value={"total_maps": 0, "total_portraits": 0, "maps": [], "portraits": []})
    orch.deploy_to_foundry = AsyncMock(return_value={"scenes": [], "npcs": [], "journal_entries": [],
                                                      "quest_logs": [], "loot_tables": [], "loot_piles": [],
                                                      "playlists": [], "calendar_events": [], "encounters": []})
    orch.enrich_scenes = AsyncMock(return_value={"enriched": 0, "skipped": 0, "errors": []})
    for name, value in overrides.items():
        setattr(orch, name, value)
    return orch


def test_build_campaign_happy_path_completes_and_clears_its_checkpoint(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    _FakeSettlementIntegration.result = {"oakhaven": _fake_settlement("Oakhaven")}
    orch = _orch_with_mocks()
    foundry_client = MagicMock()
    llm_client = MagicMock()

    result = asyncio.run(orch.build_campaign(
        prompt="a dark campaign", campaign_name="Oakhaven",
        llm_client=llm_client, foundry_client=foundry_client,
    ))

    assert result["status"] == "complete"
    assert result["ready_to_start"] is True and result["campaign_ready"] is True
    assert result["campaign_data"]["settlements"]
    assert _FakeCampaignStore.saved and _FakeCampaignStore.deployments_saved
    assert any("maps and portraits" in s["message"] for s in result["steps"])
    # Checkpoint written during the build must be cleared on success.
    assert not (tmp_path / "campaign_assets" / "oakhaven" / "build_checkpoint.json").exists()


def test_build_campaign_scan_failure_is_reported_but_not_fatal(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks()
    orch.scan_foundry_world = AsyncMock(side_effect=RuntimeError("relay unreachable"))

    result = asyncio.run(orch.build_campaign(
        prompt="p", campaign_name="Oakhaven", llm_client=MagicMock(), foundry_client=MagicMock(),
    ))

    assert result["status"] == "complete"
    assert any("Scan incomplete" in s["message"] for s in result["steps"])
    # generate_campaign_data still ran, with an empty scan_result.
    orch.generate_campaign_data.assert_awaited_once()
    assert orch.generate_campaign_data.await_args.args[2] == {}


def test_build_campaign_no_foundry_client_skips_scan_deploy_and_enrich(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=MagicMock()))

    assert result["status"] == "complete"
    orch.scan_foundry_world.assert_not_awaited()
    orch.deploy_to_foundry.assert_not_awaited()
    orch.enrich_scenes.assert_not_awaited()
    assert result.get("deployment") is None


def test_build_campaign_resumes_from_an_assets_checkpoint_and_skips_regeneration(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    ckpt_dir = tmp_path / "campaign_assets" / "oakhaven"
    ckpt_dir.mkdir(parents=True)
    checkpoint_data = {
        "phase": "assets", "prompt": "p", "campaign_name": "Oakhaven",
        "campaign_data": json.loads(json.dumps(VALID_CAMPAIGN_DATA)),
        "asset_info": {"total_maps": 1, "total_portraits": 0},
    }
    (ckpt_dir / "build_checkpoint.json").write_text(json.dumps(checkpoint_data))
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(
        prompt="p", campaign_name="Oakhaven", llm_client=MagicMock(), foundry_client=MagicMock(),
    ))

    assert result["status"] == "complete"
    orch.generate_campaign_data.assert_not_awaited()
    orch.generate_assets.assert_not_awaited()
    assert not any("settlements" == s["step"] for s in result["steps"])
    assert any("Resuming campaign build" in s["message"] for s in result["steps"])


def test_build_campaign_rejects_an_incomplete_prebuilt_campaign_data(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks()

    with pytest.raises(Exception, match="missing 'campaign' key"):
        asyncio.run(orch.build_campaign(
            prompt="p", campaign_name="X", llm_client=MagicMock(),
            campaign_data={"npcs": []},
        ))


def test_build_campaign_accepts_prebuilt_campaign_data_and_skips_generation(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(
        prompt="p", llm_client=MagicMock(),
        campaign_data=json.loads(json.dumps(VALID_CAMPAIGN_DATA)),
    ))

    assert result["status"] == "complete"
    assert result["campaign_data"]["campaign"]["name"] == "Oakhaven"
    orch.generate_campaign_data.assert_not_awaited()


def test_build_campaign_on_progress_exception_is_swallowed(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks()

    def exploding(*a, **k):
        raise RuntimeError("UI callback is gone")

    result = asyncio.run(orch.build_campaign(
        prompt="p", campaign_name="Oakhaven", llm_client=MagicMock(), on_progress=exploding,
    ))

    assert result["status"] == "complete"   # the pipeline kept going


def test_build_campaign_asset_generation_failure_is_non_fatal(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks(generate_assets=AsyncMock(side_effect=RuntimeError("comfyui timed out")))

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=MagicMock()))

    assert result["status"] == "complete"
    assert "comfyui timed out" in result["asset_error"]
    assert any("Asset generation failed" in s["message"] for s in result["steps"])


def test_build_campaign_llm_raising_outright_is_an_error_with_campaign_data_still_none(tmp_path, monkeypatch):
    """Distinct from the 'LLM returned malformed data' case: here
    generate_campaign_data itself raises, so campaign_data is still None at
    the except, taking the OTHER branch of the status-setting logic."""
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks(generate_campaign_data=AsyncMock(side_effect=RuntimeError("LLM host unreachable")))

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="X", llm_client=MagicMock()))

    assert result["status"] == "error"
    assert "Campaign generation failed" in result["error"]
    assert "LLM host unreachable" in result["error"]


def test_build_campaign_llm_missing_campaign_key_is_an_error_result(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks(generate_campaign_data=AsyncMock(return_value={"npcs": []}))

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="X", llm_client=MagicMock()))

    assert result["status"] == "error"
    assert "missing 'campaign' key" in result["error"]


def test_build_campaign_a_mid_pipeline_failure_is_reported_as_an_error_status(tmp_path, monkeypatch):
    """Regression: once campaign_data exists, an unguarded step raising (here,
    save_to_vault) used to leave result['status'] at its initial 'building'
    value forever — only result['error'] got set. A caller branching on
    status (not just presence of 'error') would treat a failed build as still
    in progress rather than failed."""
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks(save_to_vault=AsyncMock(side_effect=RuntimeError("disk full")))

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="X", llm_client=MagicMock()))

    assert result["status"] == "error"
    assert "disk full" in result["error"]


def test_build_campaign_settlement_generation_failure_does_not_abort_the_build(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    _FakeSettlementIntegration.fail = True
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=MagicMock()))

    assert result["status"] == "complete"
    assert any("Settlement generation failed" in s["message"] for s in result["steps"])
    assert "settlements" not in result["campaign_data"]


def test_build_campaign_no_settlement_names_found_reports_none_generated(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    _FakeSettlementIntegration.result = {}
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=MagicMock()))

    assert any("No settlements generated" in s["message"] for s in result["steps"])


def test_build_campaign_map_generator_init_failure_skips_asset_generation(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    _FakeMapGenerator.init_fail = True
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=MagicMock()))

    assert result["status"] == "complete"
    assert any("Map generator init failed" in s["message"] for s in result["steps"])
    orch.generate_assets.assert_not_awaited()


def test_build_campaign_deploy_failure_is_recorded_and_build_still_completes(tmp_path, monkeypatch):
    """Unlike the unguarded save_to_vault step, deploy_to_foundry's own
    try/except keeps the build going and the overall status is 'complete'
    with deploy_error set — this is the designed graceful-degradation path."""
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks(deploy_to_foundry=AsyncMock(side_effect=RuntimeError("foundry relay down")))

    result = asyncio.run(orch.build_campaign(
        prompt="p", campaign_name="Oakhaven", llm_client=MagicMock(), foundry_client=MagicMock(),
    ))

    assert result["status"] == "complete"
    assert "foundry relay down" in result["deploy_error"]
    orch.enrich_scenes.assert_not_awaited()   # no deployment to enrich


def test_build_campaign_enrich_scenes_failure_does_not_abort_the_build(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks(enrich_scenes=AsyncMock(side_effect=RuntimeError("enrich blew up")))

    result = asyncio.run(orch.build_campaign(
        prompt="p", campaign_name="Oakhaven", llm_client=MagicMock(), foundry_client=MagicMock(),
    ))

    assert result["status"] == "complete"
    assert any("Scene enrichment failed" in s["message"] for s in result["steps"])


def test_build_campaign_state_persist_failure_is_logged_not_fatal(tmp_path, monkeypatch, caplog):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    _FakeCampaignStore.fail = True
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=MagicMock()))

    assert result["status"] == "complete"
    assert "Could not persist campaign state" in caplog.text


def test_build_campaign_vault_asset_sync_failure_is_logged_not_fatal(tmp_path, monkeypatch, caplog):
    _patch_build_campaign_seams(monkeypatch, tmp_path, sync_assets_fails=True)
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=MagicMock()))

    assert result["status"] == "complete"
    assert "Could not copy assets into the vault" in caplog.text


def test_build_campaign_owns_and_closes_a_client_it_creates_itself(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    fake_client = MagicMock()
    fake_client.aclose = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=fake_client))
    orch = _orch_with_mocks()

    result = asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven"))

    assert result["status"] == "complete"
    fake_client.aclose.assert_awaited_once()


def test_build_campaign_does_not_close_a_caller_supplied_client(tmp_path, monkeypatch):
    _patch_build_campaign_seams(monkeypatch, tmp_path)
    orch = _orch_with_mocks()
    caller_client = MagicMock()
    caller_client.aclose = AsyncMock()

    asyncio.run(orch.build_campaign(prompt="p", campaign_name="Oakhaven", llm_client=caller_client))

    caller_client.aclose.assert_not_awaited()


# ─── extend_campaign_arc ────────────────────────────────────────────────────


ARC_JSON = json.dumps({
    "campaign": {"arc_title": "The Second Dawn", "arc_level_range": "5-10"},
    "scenes": [{"name": "New Scene"}], "npcs": [{"name": "New NPC"}],
    "encounters": [], "quest_logs": [], "locations": [], "story_arcs": [],
})


def _arc_llm_client(content=ARC_JSON):
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200, content=content))
    return client


def test_extend_campaign_arc_requires_a_caller_supplied_llm_client():
    with pytest.raises(ValueError, match="requires an llm_client"):
        asyncio.run(CampaignOrchestrator().extend_campaign_arc("X", current_level=5, llm_client=None))


def test_extend_campaign_arc_reports_a_missing_campaign_as_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = asyncio.run(CampaignOrchestrator().extend_campaign_arc(
        "Nonexistent", current_level=5, llm_client=_arc_llm_client(),
    ))
    assert result["status"] == "error"
    assert "not found" in result["error"]


def _write_state(tmp_path, safe_name="oakhaven", **existing_data):
    state_dir = tmp_path / "campaign_assets" / safe_name
    state_dir.mkdir(parents=True)
    (state_dir / "deployment_state.json").write_text(json.dumps({"campaign_data": existing_data}))
    return state_dir / "deployment_state.json"


def test_extend_campaign_arc_generates_merges_and_saves_state_without_foundry(tmp_path, monkeypatch):
    _write_state(tmp_path, **{"campaign": {"name": "Oakhaven"}, "scenes": [{"name": "Old Scene"}],
                              "story_arcs": [{"arc_number": 1, "name": "Arc 1"}]})
    monkeypatch.chdir(tmp_path)
    _reset_fakes()
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _FakeMapGenerator)
    client = _arc_llm_client()

    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(return_value={"total_maps": 0, "total_portraits": 0, "maps": [], "portraits": []})

    result = asyncio.run(orch.extend_campaign_arc(
        "Oakhaven", current_level=5, llm_client=client,
    ))

    assert result["status"] == "complete"
    # One prior real arc (arc_number=1) plus 2 → this is Arc 3.
    assert result["arc_number"] == 3
    assert result["arc_title"] == "The Second Dawn"
    scene_names = {s["name"] for s in result["arc_data"]["scenes"]}
    assert scene_names == {"New Scene"}
    assert result["arc_data"]["scenes"][0]["arc_number"] == 3

    saved = json.loads((tmp_path / "campaign_assets" / "oakhaven" / "deployment_state.json").read_text())
    merged_names = {s["name"] for s in saved["campaign_data"]["scenes"]}
    assert merged_names == {"Old Scene", "New Scene"}      # merged in, not replaced
    assert saved["last_arc"] == 3


def test_extend_campaign_arc_runs_deploy_and_enrich_when_foundry_is_connected(tmp_path, monkeypatch):
    _write_state(tmp_path, **{"campaign": {"name": "Oakhaven"}, "scenes": []})
    monkeypatch.chdir(tmp_path)
    _reset_fakes()
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _FakeMapGenerator)
    client = _arc_llm_client()
    foundry_client = MagicMock()

    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(return_value={"total_maps": 1, "total_portraits": 0, "maps": [], "portraits": []})
    orch.upload_maps_to_foundry = AsyncMock(return_value={"uploaded": 1, "failed": 0, "errors": []})
    orch.deploy_to_foundry = AsyncMock(return_value=None)
    orch.deploy_encounters = AsyncMock(return_value=[{"name": "E1", "status": "deployed"}])
    orch.enrich_scenes = AsyncMock(return_value={"enriched": 1, "skipped": 0, "errors": []})

    result = asyncio.run(orch.extend_campaign_arc(
        "Oakhaven", current_level=5, llm_client=client, foundry_client=foundry_client,
    ))

    assert result["status"] == "complete"
    orch.upload_maps_to_foundry.assert_awaited_once()
    orch.deploy_to_foundry.assert_awaited_once()
    orch.deploy_encounters.assert_awaited_once()
    orch.enrich_scenes.assert_awaited_once()
    assert result["deployment"]["encounters"] == [{"name": "E1", "status": "deployed"}]


def test_extend_campaign_arc_deploy_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    _write_state(tmp_path, **{"campaign": {"name": "Oakhaven"}, "scenes": []})
    monkeypatch.chdir(tmp_path)
    _reset_fakes()
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _FakeMapGenerator)
    client = _arc_llm_client()
    foundry_client = MagicMock()

    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(return_value={"total_maps": 0, "total_portraits": 0, "maps": [], "portraits": []})
    orch.deploy_to_foundry = AsyncMock(side_effect=RuntimeError("relay down"))

    result = asyncio.run(orch.extend_campaign_arc(
        "Oakhaven", current_level=5, llm_client=client, foundry_client=foundry_client,
    ))

    assert result["status"] == "complete"
    assert "relay down" in result["deploy_error"]


def test_extend_campaign_arc_module_scan_failure_is_non_fatal(tmp_path, monkeypatch):
    _write_state(tmp_path, **{"campaign": {"name": "Oakhaven"}, "scenes": []})
    monkeypatch.chdir(tmp_path)
    _reset_fakes()
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _FakeMapGenerator)
    client = _arc_llm_client()
    foundry_client = MagicMock()

    orch = CampaignOrchestrator()
    orch.scan_foundry_world = AsyncMock(side_effect=RuntimeError("scan down"))
    orch.generate_assets = AsyncMock(return_value={"total_maps": 0, "total_portraits": 0, "maps": [], "portraits": []})
    orch.deploy_to_foundry = AsyncMock(return_value=None)
    orch.deploy_encounters = AsyncMock(return_value=[])

    result = asyncio.run(orch.extend_campaign_arc(
        "Oakhaven", current_level=5, llm_client=client, foundry_client=foundry_client,
    ))

    assert result["status"] == "complete"
    assert any("Scan skipped" in s["message"] for s in result["steps"])


def test_extend_campaign_arc_map_upload_and_enrich_failures_are_recorded(tmp_path, monkeypatch):
    _write_state(tmp_path, **{"campaign": {"name": "Oakhaven"}, "scenes": []})
    monkeypatch.chdir(tmp_path)
    _reset_fakes()
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _FakeMapGenerator)
    client = _arc_llm_client()
    foundry_client = MagicMock()

    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(return_value={"total_maps": 1, "total_portraits": 0, "maps": [], "portraits": []})
    orch.upload_maps_to_foundry = AsyncMock(side_effect=RuntimeError("upload rejected"))
    orch.deploy_to_foundry = AsyncMock(return_value=None)
    orch.deploy_encounters = AsyncMock(return_value=[])
    orch.enrich_scenes = AsyncMock(side_effect=RuntimeError("enrich blew up"))

    result = asyncio.run(orch.extend_campaign_arc(
        "Oakhaven", current_level=5, llm_client=client, foundry_client=foundry_client,
    ))

    assert result["status"] == "complete"
    assert any("Map upload failed" in s["message"] for s in result["steps"])
    assert any("Scene enrichment failed" in s["message"] for s in result["steps"])


def test_extend_campaign_arc_vault_save_failure_is_reported_not_fatal(tmp_path, monkeypatch):
    _write_state(tmp_path, **{"campaign": {"name": "Oakhaven"}, "scenes": []})
    monkeypatch.chdir(tmp_path)
    client = _arc_llm_client()

    orch = CampaignOrchestrator()
    orch.save_to_vault = AsyncMock(side_effect=RuntimeError("vault locked"))

    result = asyncio.run(orch.extend_campaign_arc(
        "Oakhaven", current_level=5, llm_client=client, vault_path="/some/vault",
    ))

    assert result["status"] == "complete"
    assert any("Vault save failed" in s["message"] for s in result["steps"])


# ─── _arc_save_to_vault: lore injection for consistency ────────────────────


class _FakeLoreLoader:
    def __init__(self, vault_path=None, semantic_indexer=None):
        self.vault_path = vault_path

    async def load(self, campaign_name):
        return {}

    def search_vault(self, query, max_results=5):
        return ["Oakhaven was founded on an old battlefield."] if query else []


def test_arc_save_to_vault_injects_lore_when_a_query_is_available(monkeypatch):
    monkeypatch.setattr("context.loader.CampaignLoader", _FakeLoreLoader)
    existing_data = {"campaign": {"description": "A cursed valley", "theme": "gothic"}, "story_arcs": []}
    progressed = []

    lore = asyncio.run(CampaignOrchestrator()._arc_save_to_vault(
        "Oakhaven", "/some/vault", existing_data, lambda *a, **k: progressed.append(a)
    ))

    assert "battlefield" in lore
    assert progressed


def test_arc_save_to_vault_returns_empty_without_a_vault_path():
    lore = asyncio.run(CampaignOrchestrator()._arc_save_to_vault(
        "Oakhaven", None, {"campaign": {}}, lambda *a, **k: None
    ))
    assert lore == ""


def test_arc_save_to_vault_swallows_loader_failures():
    class _BoomLoader:
        def __init__(self, vault_path=None, semantic_indexer=None):
            raise RuntimeError("vault unreadable")

    import context.loader as loader_module
    import campaign.orchestrator as orch_mod

    original = loader_module.CampaignLoader
    loader_module.CampaignLoader = _BoomLoader
    try:
        lore = asyncio.run(orch_mod.CampaignOrchestrator()._arc_save_to_vault(
            "Oakhaven", "/some/vault", {"campaign": {"description": "x"}}, lambda *a, **k: None
        ))
    finally:
        loader_module.CampaignLoader = original
    assert lore == ""


# ─── _arc_generate_maps ─────────────────────────────────────────────────────


def test_arc_generate_maps_returns_empty_without_a_map_generator():
    asset_info = asyncio.run(CampaignOrchestrator()._arc_generate_maps(
        {"scenes": []}, {}, None, "out", lambda *a, **k: None
    ))
    assert asset_info == {}


def test_arc_generate_maps_closes_the_generator_and_scopes_to_the_new_arc_only(monkeypatch, tmp_path):
    _reset_fakes()
    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(return_value={"total_maps": 1, "total_portraits": 0, "maps": [], "portraits": []})
    map_gen = _FakeMapGenerator()
    progressed = []

    asset_info = asyncio.run(orch._arc_generate_maps(
        {"scenes": [{"name": "New Scene"}], "npcs": [], "locations": []},
        {"scenes": [{"name": "Old Scene"}]},
        map_gen, "out_dir", lambda *a, **k: progressed.append(a),
    ))

    assert asset_info["total_maps"] == 1
    assert map_gen.closed is True
    passed_data = orch.generate_assets.await_args.args[0]
    assert passed_data["scenes"] == [{"name": "New Scene"}]   # only the arc's own scenes
    assert progressed


def test_arc_generate_maps_failure_is_reported_and_generator_still_closed():
    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(side_effect=RuntimeError("comfyui down"))
    map_gen = _FakeMapGenerator()
    progressed = []

    asset_info = asyncio.run(orch._arc_generate_maps(
        {"scenes": []}, {}, map_gen, "out_dir", lambda *a, **k: progressed.append(a),
    ))

    assert asset_info == {}
    assert map_gen.closed is True
    assert any("Asset generation failed" in str(p) for p in progressed)


# ─── build_campaign_convenience ─────────────────────────────────────────────


def test_build_campaign_convenience_wires_app_level_settings_into_build_campaign(monkeypatch):
    monkeypatch.setattr(orchestrator_module.settings, "campaign_vault_path", "/v")
    monkeypatch.setattr(orchestrator_module.settings, "comfyui_url", "http://comfy")
    captured = {}

    async def fake_build_campaign(self, **kwargs):
        captured.update(kwargs)
        return {"status": "complete"}

    monkeypatch.setattr(CampaignOrchestrator, "build_campaign", fake_build_campaign)

    result = asyncio.run(CampaignOrchestrator().build_campaign_convenience(
        prompt="a quest", campaign_name="Oakhaven",
    ))

    assert result == {"status": "complete"}
    assert captured["prompt"] == "a quest" and captured["campaign_name"] == "Oakhaven"
    assert captured["vault_path"] == "/v" and captured["comfyui_url"] == "http://comfy"
    assert captured["llm_client"] is not None
