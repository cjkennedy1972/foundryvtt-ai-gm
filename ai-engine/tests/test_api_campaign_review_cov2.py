"""Behavioural coverage for api/routes/campaign.py, part 2: deploy, restart,
start/end session, vault CRUD, enrichment and optimisation endpoints."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import campaign as routes


@pytest.fixture
def state():
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.relay_manager = AsyncMock()
    s.db = AsyncMock()
    s.db.get_active_session.return_value = None
    s.campaign_loader = MagicMock()
    s.campaign_loader.load = AsyncMock()
    s.state_tracker = AsyncMock()
    return s


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _h(request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump())

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def world_ok():
    with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=None)) as m:
        yield m


@pytest.fixture
def no_broadcast():
    with patch.object(routes, "broadcast_state_update", AsyncMock()) as m:
        yield m


def _orch(**methods):
    orch = MagicMock()
    for name, rv in methods.items():
        setattr(orch, name, AsyncMock(side_effect=rv) if isinstance(rv, Exception) else AsyncMock(return_value=rv))
    return orch


def _store(tmp_path, data=None, deployment=None):
    store = MagicMock()
    store.load = AsyncMock(return_value=data if data is not None else {"scenes": []})
    store.load_deployment = AsyncMock(return_value=deployment)
    store.save_deployment = AsyncMock()
    store.save = AsyncMock()
    store.safe_name = "c"
    store.maps_dir = tmp_path / "maps"
    return store


# ------------------------------------------------- _deploy_campaign_to_world

class TestDeployPipeline:
    async def _run(self, state, tmp_path, orch, store=None, maps=False):
        store = store or _store(tmp_path)
        if maps:
            store.maps_dir.mkdir()
        with patch.object(routes, "CampaignStore", return_value=store), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            return await routes._deploy_campaign_to_world("C", state), store

    def _orch(self, **over):
        base = dict(
            upload_maps_to_foundry={"uploaded": 1}, upload_portraits_to_foundry={"uploaded": 2},
            scan_foundry_world={"modules": ["x"]}, deploy_to_foundry={"status": "ok", "scenes": [1]},
            enrich_scenes={"enriched": 1})
        base.update(over)
        return _orch(**base)

    @pytest.mark.asyncio
    async def test_full_pipeline_order_and_persistence(self, state, tmp_path):
        orch = self._orch()
        result, store = await self._run(state, tmp_path, orch, maps=True)
        orch.upload_maps_to_foundry.assert_awaited_once()
        orch.upload_portraits_to_foundry.assert_awaited_once()
        assert orch.deploy_to_foundry.await_args.kwargs["scan_result"] == {"modules": ["x"]}
        assert result["scene_enrichment"] == {"enriched": 1}
        store.save_deployment.assert_awaited_once_with(result)
        store.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_maps_dir_skips_asset_upload(self, state, tmp_path):
        orch = self._orch()
        await self._run(state, tmp_path, orch)
        orch.upload_maps_to_foundry.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scan_enrich_and_persist_failures_are_non_fatal(self, state, tmp_path):
        orch = self._orch(scan_foundry_world=RuntimeError("x"), enrich_scenes=RuntimeError("y"))
        store = _store(tmp_path)
        store.save_deployment.side_effect = OSError("disk")
        result, _ = await self._run(state, tmp_path, orch, store=store)
        assert result["status"] == "ok" and "scene_enrichment" not in result
        assert orch.deploy_to_foundry.await_args.kwargs["scan_result"] is None


class TestDeployEndpoint:
    def test_world_error_and_disconnected(self, client, state):
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value="no world")):
            assert client.post("/api/campaign/deploy", json={"campaign_name": "C"}).json()["error"] == "no world"
        state.foundry_client.is_connected = False
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=None)):
            assert client.post("/api/campaign/deploy", json={"campaign_name": "C"}).json()["error"] == (
                "Not connected to FoundryVTT")

    def test_counts_deployed_entities(self, client, world_ok):
        dep = {"status": "ok", "scenes": [1, 2], "npcs": [1], "journal_entries": [1, 2, 3],
               "quest_logs": [1], "loot_tables": []}
        with patch.object(routes, "_deploy_campaign_to_world", AsyncMock(return_value=dep)):
            b = client.post("/api/campaign/deploy", json={"campaign_name": "C"}).json()
        assert (b["scenes_deployed"], b["npcs_deployed"], b["journal_entries_deployed"],
                b["quest_logs_deployed"], b["loot_tables_deployed"]) == (2, 1, 3, 1, 0)

    @pytest.mark.parametrize("exc", [FileNotFoundError("/vault/secret"), RuntimeError("/vault/secret")])
    def test_failures_report_type_only(self, client, world_ok, exc):
        with patch.object(routes, "_deploy_campaign_to_world", AsyncMock(side_effect=exc)):
            r = client.post("/api/campaign/deploy", json={"campaign_name": "C"})
        assert r.json()["status"] == "error" and r.json()["error"] == type(exc).__name__
        assert "secret" not in r.text


class TestRegenerateAssets:
    def _post(self, client, orch, attach=True, world=None):
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=world)) as sel, \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            r = client.post("/api/campaign/regenerate-assets",
                            json={"campaign_name": "C", "attach_to_foundry": attach})
        return r, sel

    def test_success_maps_counts(self, client, state):
        orch = _orch(regenerate_assets_for_campaign={"status": "ok", "maps_generated": 3, "portraits_generated": 2,
                                                     "scenes_attached": 1, "portraits_attached": 2, "errors": ["e"]})
        r, _ = self._post(client, orch)
        assert r.json()["maps_generated"] == 3 and r.json()["errors"] == ["e"]
        kw = orch.regenerate_assets_for_campaign.await_args.kwargs
        assert kw["campaign_name"] == "C" and kw["attach_to_foundry"] is True

    def test_detached_regeneration_does_not_touch_the_world(self, client):
        orch = _orch(regenerate_assets_for_campaign={"status": "ok"})
        r, sel = self._post(client, orch, attach=False)
        sel.assert_not_awaited()
        assert r.json()["status"] == "ok"

    def test_world_error_blocks_attached_run(self, client):
        orch = _orch(regenerate_assets_for_campaign={})
        r, _ = self._post(client, orch, world="no world")
        assert r.json()["error"] == "no world"
        orch.regenerate_assets_for_campaign.assert_not_awaited()

    def test_failure_is_type_only(self, client):
        r, _ = self._post(client, _orch(regenerate_assets_for_campaign=KeyError("/s")))
        assert r.json()["error"] == "KeyError" and "/s" not in r.text


# ----------------------------------------------------------------- restart

class TestRestart:
    def _post(self, client, orch=None, deploy=None):
        orch = orch or _orch(teardown_campaign={"deleted": {"scenes": 4}})
        dep = deploy if deploy is not None else {"scenes": [1], "npcs": [1, 2], "scene_enrichment": {"enriched": 1}}
        with patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch), \
             patch.object(routes, "_deploy_campaign_to_world", AsyncMock(side_effect=dep if isinstance(dep, Exception) else None,
                                                                          return_value=None if isinstance(dep, Exception) else dep)):
            return client.post("/api/campaign/restart", json={"campaign_name": "C"}), orch

    def test_world_error_is_503(self, client):
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value="no world")):
            r = client.post("/api/campaign/restart", json={"campaign_name": "C"})
        assert r.status_code == 503 and r.json()["code"] == "FOUNDRY_UNAVAILABLE"

    def test_disconnected_is_503_via_require_foundry(self, client, state, world_ok):
        state.foundry_client.is_connected = False
        assert client.post("/api/campaign/restart", json={"campaign_name": "C"}).status_code == 503

    def test_wipes_history_resets_state_and_redeploys_in_order(self, client, state, world_ok, no_broadcast):
        state.chat_listener = SimpleNamespace(_running=True)
        state.db.get_active_session.return_value = "s1"
        state.db.delete_campaign_history.return_value = 5
        with patch("campaign.prologue.reset_prologue_shown", AsyncMock()) as reset:
            r, orch = self._post(client)
        assert r.json()["status"] == "restarted" and r.json()["sessions_deleted"] == 5
        assert r.json()["teardown"] == {"scenes": 4} and r.json()["scenes_deployed"] == 1
        assert r.json()["npcs_deployed"] == 2 and r.json()["enrichment"] == {"enriched": 1}
        assert state.chat_listener._running is False
        state.db.close_session.assert_awaited_once_with("s1")
        state.db.delete_campaign_history.assert_awaited_once_with("C")
        state.state_tracker.reset.assert_awaited_once_with(campaign="C")
        orch.teardown_campaign.assert_awaited_once_with("C", state.foundry_client)
        reset.assert_awaited_once()
        no_broadcast.assert_awaited_once_with({"type": "campaign_restarted", "campaign_name": "C"})

    def test_no_active_session_closes_nothing_and_prologue_failure_is_ignored(
            self, client, state, world_ok, no_broadcast):
        with patch("campaign.prologue.reset_prologue_shown", AsyncMock(side_effect=RuntimeError("x"))):
            r, _ = self._post(client)
        assert r.json()["status"] == "restarted"
        state.db.close_session.assert_not_awaited()

    def test_missing_campaign_is_404_and_other_errors_500(self, client, state, world_ok, no_broadcast):
        r, _ = self._post(client, deploy=FileNotFoundError("/v/secret"))
        assert r.status_code == 404 and r.json()["code"] == "CAMPAIGN_NOT_FOUND" and "secret" not in r.text
        r, _ = self._post(client, deploy=RuntimeError("/v/secret"))
        assert r.status_code == 500 and r.json()["code"] == "RESTART_FAILED" and "secret" not in r.text


# --------------------------------------------------------- start / end session

class TestStartCampaign:
    def _post(self, client, **body):
        return client.post("/api/campaign/start", json={"campaign_name": "C", **body})

    def test_world_error(self, client):
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value="no world")):
            assert self._post(client).json()["error"] == "no world"

    def test_disconnected_is_503(self, client, state, world_ok):
        state.foundry_client.is_connected = False
        assert self._post(client).status_code == 503

    def test_world_mismatch_is_409_and_creates_no_session(self, client, state, world_ok):
        state.foundry_client.execute_js.return_value = {"result": {"title": "Other", "id": "o1"}}
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "W", "world_id": "w1"}):
            r = self._post(client)
        assert r.status_code == 409 and r.json()["code"] == "CAMPAIGN_WORLD_MISMATCH"
        state.db.create_session.assert_not_awaited()

    def test_name_only_link_mismatch_is_409(self, client, state, world_ok):
        state.foundry_client.execute_js.return_value = {"result": {"title": "Other", "id": ""}}
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "W"}):
            assert self._post(client).status_code == 409

    def test_first_start_links_the_current_world(self, client, state, world_ok, no_broadcast):
        state.foundry_client.execute_js.return_value = {"result": {"title": "W", "id": "w1"}}
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None), \
             patch("campaign.obsidian_sync.link_world_to_campaign") as link:
            assert self._post(client).json()["status"] == "started"
        link.assert_called_once_with("C", "W", "w1")

    def test_matching_world_is_not_relinked(self, client, state, world_ok, no_broadcast):
        state.foundry_client.execute_js.return_value = {"result": {"title": "W", "id": "w1"}}
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_id": "w1", "world_name": "W"}), \
             patch("campaign.obsidian_sync.link_world_to_campaign") as link:
            assert self._post(client).json()["status"] == "started"
        link.assert_not_called()

    def test_new_session_resets_context_and_starts_listener(self, client, state, world_ok, no_broadcast):
        state.foundry_client.execute_js.return_value = {"result": {}}
        state.foundry_client.reset_message_id = MagicMock()
        state.foundry_client.scan_world.return_value = {"modules": [
            {"title": "Midi", "active": True}, {"name": "Off", "enabled": False}, {"id": "dae", "enabled": True}]}
        state.llm_manager = MagicMock()
        state.npc_registry = MagicMock()
        listener = MagicMock(_running=False)
        for n in ("start", "_update_player_actors", "load_campaign_settlements", "sync_active_scene"):
            setattr(listener, n, AsyncMock())
        listener._process_proactive_action = MagicMock(return_value="coro")
        state.chat_listener = listener
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None), \
             patch.object(routes, "spawn") as spawn:
            r = self._post(client)
        body = r.json()
        assert body["status"] == "started" and len(body["session_id"]) == 8
        state.db.create_session.assert_awaited_once_with(body["session_id"], "C")
        state.state_tracker.set_mode.assert_awaited_once()
        state.campaign_loader.load.assert_awaited_once_with("C")
        listener.reset_ai_speakers.assert_called_once()
        state.npc_registry.clear.assert_called_once()
        state.campaign_loader.register_vault_npcs.assert_called_once_with(state.npc_registry)
        state.llm_manager.set_active_modules.assert_called_once_with(["Midi", "dae"])
        state.llm_manager.invalidate_system_prompt.assert_called_once()
        state.foundry_client.reset_message_id.assert_called_once()
        listener.start.assert_awaited_once()
        assert listener._running is True
        listener._process_proactive_action.assert_called_once_with(reason="session_start")
        spawn.assert_called_once_with("coro")

    def test_continue_reuses_the_active_session(self, client, state, world_ok, no_broadcast):
        state.foundry_client.execute_js.return_value = {"result": {}}
        state.db.get_active_session.return_value = "live1"
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None):
            r = self._post(client, continue_from_last=True)
        assert r.json()["session_id"] == "live1"
        state.db.create_session.assert_not_awaited()

    def test_continue_without_active_session_creates_one(self, client, state, world_ok, no_broadcast):
        state.foundry_client.execute_js.return_value = {"result": {}}
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None):
            self._post(client, continue_from_last=True)
        state.db.create_session.assert_awaited_once()

    def test_unexpected_failure_is_type_only(self, client, state, world_ok):
        state.foundry_client.execute_js.return_value = {"result": {}}
        state.db.get_active_session.side_effect = RuntimeError("/secret")
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None):
            r = self._post(client)
        assert r.json()["status"] == "error" and r.json()["error"] == "RuntimeError" and "secret" not in r.text


class TestEndSession:
    def _post(self, client, **body):
        return client.post("/api/session/end", json=body)

    def test_no_active_session(self, client, state, no_broadcast):
        assert self._post(client).json()["status"] == "no_active_session"
        state.db.close_session.assert_not_awaited()

    def test_pauses_listener_summarises_closes_and_broadcasts(self, client, state, no_broadcast):
        state.chat_listener = SimpleNamespace(_running=True)
        state.db.get_active_session.return_value = "s1"
        state.state_tracker = MagicMock()
        state.state_tracker.state.model_dump.return_value = {"mode": "x"}
        state.state_tracker.state.campaign = "Krynn"
        state.llm_manager = AsyncMock()
        state.llm_manager.generate.return_value = {"narration": "ok"}
        b = self._post(client, reason="tired").json()
        assert b["status"] == "ended" and b["session_id"] == "s1" and b["campaign_name"] == "Krynn"
        assert "tired" in b["message"] and json.loads(b["summary"]) == {"narration": "ok"}
        assert state.chat_listener._running is False
        state.db.close_session.assert_awaited_once_with("s1")
        types = [c.args[0]["type"] for c in no_broadcast.await_args_list]
        assert types == ["ai_paused", "session_ended"]

    def test_llm_failure_falls_back_to_state_snapshot_and_still_closes(self, client, state, no_broadcast):
        state.db.get_active_session.return_value = "s1"
        state.state_tracker = MagicMock()
        state.state_tracker.state.model_dump.return_value = {"mode": "x"}
        state.state_tracker.state.campaign = "K"
        state.llm_manager = AsyncMock()
        state.llm_manager.generate.side_effect = RuntimeError("llm")
        b = self._post(client).json()
        assert b["status"] == "ended" and json.loads(b["summary"]) == {"mode": "x"}
        state.db.close_session.assert_awaited_once_with("s1")

    def test_db_failure_is_type_only(self, client, state, no_broadcast):
        state.db.get_active_session.side_effect = RuntimeError("/secret")
        r = self._post(client)
        assert r.json()["status"] == "error" and r.json()["error"] == "RuntimeError"
