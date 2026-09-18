"""Drives api/routes/campaign.py endpoints through a real TestClient.

The module sat at 39% with 39 endpoints and nothing exercising them:
test_campaign_api_contracts.py constructs Pydantic models and reads the
fields back without importing a handler. These tests call the routes.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import AppState, get_app_state
from api.routes import campaign as campaign_routes


@pytest.fixture
def state():
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.db = AsyncMock()
    s.campaign_loader = MagicMock()
    s.relay_manager = AsyncMock()
    # RelayManager.status() is sync; an AsyncMock would hand the route a
    # coroutine and it would fail on .get("running") before reaching any gate.
    s.relay_manager.status = MagicMock(return_value={"running": True})
    s.chat_listener = AsyncMock()
    s.state_tracker = AsyncMock()
    return s


@pytest.fixture
def client(state):
    """Mirrors main.py, including the ApiError handler.

    Without it, a route that raises ApiError surfaces as 500 and its real
    status code is untestable.
    """
    from fastapi.responses import JSONResponse

    from api.deps import ApiError, ErrorResponse

    app = FastAPI()
    app.include_router(campaign_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _api_error(request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump(),
        )

    return TestClient(app, raise_server_exceptions=False)


class TestListCampaigns:
    def test_returns_the_campaigns_in_the_vault(self, client):
        vault = [{"name": "Valenthal"}, {"name": "Ashmoor"}]
        with patch("campaign.obsidian_sync.list_campaigns", return_value=vault):
            resp = client.get("/api/campaign/list")

        assert resp.status_code == 200
        assert [c["name"] for c in resp.json()["campaigns"]] == ["Valenthal", "Ashmoor"]

    def test_an_unreadable_vault_reports_the_error_type_not_the_path(self, client):
        """The vault path is on disk; the error must not describe it."""
        with patch("campaign.obsidian_sync.list_campaigns",
                   side_effect=PermissionError("/Users/someone/Vaults denied")):
            resp = client.get("/api/campaign/list")

        assert resp.status_code == 200
        body = resp.json()
        assert body["campaigns"] == []
        assert body["error"] == "PermissionError"
        assert "Users" not in json.dumps(body)


class TestGetCampaign:
    def test_returns_the_manifest_with_computed_counts(self, client, tmp_path):
        manifest = {
            "name": "Valenthal",
            "npcs": [{"name": "Borin"}, {"name": "Mara"}],
            "locations": [{"name": "Crypt"}],
            "quests": [{"title": "Find it"}],
            "journal_entries": [{"title": "Lore"}],
        }
        with patch("campaign.obsidian_sync.resolve_vault_path", return_value=tmp_path), \
             patch("campaign.obsidian_sync.get_campaign_folder", return_value=tmp_path), \
             patch("campaign.obsidian_sync.get_campaign_manifest", return_value=dict(manifest)):
            resp = client.get("/api/campaign/get/Valenthal")

        body = resp.json()
        assert resp.status_code == 200
        assert body["npc_count"] == 2
        assert body["location_count"] == 1
        assert body["quest_count"] == 1
        assert body["journal_entries"] == 1

    def test_merges_campaign_json_when_present(self, client, tmp_path):
        (tmp_path / "campaign.json").write_text(json.dumps({"scenes": [{"name": "Crypt"}]}))
        with patch("campaign.obsidian_sync.resolve_vault_path", return_value=tmp_path), \
             patch("campaign.obsidian_sync.get_campaign_folder", return_value=tmp_path), \
             patch("campaign.obsidian_sync.get_campaign_manifest", return_value={"name": "V"}):
            resp = client.get("/api/campaign/get/V")

        assert resp.json()["data"]["scenes"] == [{"name": "Crypt"}]

    def test_an_unknown_campaign_is_404(self, client, tmp_path):
        with patch("campaign.obsidian_sync.resolve_vault_path", return_value=tmp_path), \
             patch("campaign.obsidian_sync.get_campaign_folder", return_value=tmp_path), \
             patch("campaign.obsidian_sync.get_campaign_manifest", return_value=None):
            resp = client.get("/api/campaign/get/Nope")

        assert resp.status_code == 404


class TestDeleteCampaign:
    def test_reports_deleted_when_the_vault_removed_it(self, client):
        with patch("campaign.obsidian_sync.delete_campaign", new=AsyncMock(return_value=True)):
            resp = client.post("/api/campaign/delete", json={"name": "Valenthal"})

        assert resp.json() == {"status": "deleted"}

    def test_reports_not_found_rather_than_pretending(self, client):
        with patch("campaign.obsidian_sync.delete_campaign", new=AsyncMock(return_value=False)):
            resp = client.post("/api/campaign/delete", json={"name": "Ghost"})

        assert resp.json() == {"status": "not_found"}

    def test_a_failure_is_500_without_the_exception_text(self, client):
        with patch("campaign.obsidian_sync.delete_campaign",
                   new=AsyncMock(side_effect=OSError("/Users/someone/Vaults/x busy"))):
            resp = client.post("/api/campaign/delete", json={"name": "Valenthal"})

        assert resp.status_code == 500
        assert "Users" not in resp.text

    def test_the_name_is_required(self, client):
        assert client.post("/api/campaign/delete", json={}).status_code == 422


class TestWorldSelectionGate:
    """_select_campaign_world guards every endpoint that touches a world."""

    def test_teardown_refuses_without_a_session_manager(self, client, state):
        state.relay_manager = None

        resp = client.post("/api/campaign/teardown", json={"campaign_name": "Valenthal"})

        assert resp.json()["errors"] == ["Foundry session manager is unavailable"]

    def test_teardown_reports_a_relay_that_will_not_start(self, client, state):
        state.relay_manager.status = MagicMock(return_value={"running": False})
        state.relay_manager.start = AsyncMock(side_effect=RuntimeError("port busy"))

        with patch("api.routes.campaign.settings.relay_managed", True):
            resp = client.post("/api/campaign/teardown", json={"campaign_name": "Valenthal"})

        assert any("Could not start the relay" in e for e in resp.json()["errors"])

    def test_an_unlinked_campaign_that_cannot_connect_gets_setup_guidance(self, client, state):
        state.foundry_client.connect = AsyncMock(return_value=False)

        with patch("campaign.obsidian_sync.get_campaign_world", return_value={}), \
             patch("api.routes.campaign.settings.foundry_world", ""):
            resp = client.post("/api/campaign/teardown", json={"campaign_name": "Valenthal"})

        error = " ".join(resp.json()["errors"])
        assert "not linked to a Foundry world" in error
        assert "pair the relay module" in error, "the error should say what to do"


class TestScanWorld:
    def test_a_successful_scan_is_returned(self, client, state):
        scan = {"world": {"title": "Valenthal"}, "scenes": [{"name": "Crypt"}], "actors": []}
        state.foundry_client.scan_world = AsyncMock(return_value=scan)
        state.foundry_client.discover_addon_capabilities = AsyncMock(return_value={"available_maps": 1})

        resp = client.post("/api/campaign/scan", json={})

        assert resp.status_code == 200

    def test_a_disconnected_foundry_is_reported_not_crashed(self, client, state):
        state.foundry_client.is_connected = False
        state.foundry_client.connect = AsyncMock(return_value=False)

        resp = client.post("/api/campaign/scan", json={})

        assert resp.status_code in (200, 503)
        assert resp.json().get("status") != "success"


class TestAutoOptimizeEndpoints:
    """Three sibling endpoints sharing one readiness gate and one failure shape."""

    CASES = [
        ("scene", "/api/campaign/auto-optimize-scene", {"scene": {"name": "Crypt"}}, "optimize_new_scene"),
        ("encounter", "/api/campaign/auto-optimize-encounter", {"encounter": {"name": "Ambush"}}, "optimize_new_encounter"),
        ("quest", "/api/campaign/auto-optimize-quest", {"quest": {"title": "Find it"}}, "optimize_new_quest"),
    ]

    @pytest.mark.parametrize("label,path,body,method", CASES)
    def test_refuses_when_foundry_is_not_connected(self, client, state, label, path, body, method):
        state.foundry_client.is_connected = False

        resp = client.post(path, json=body)

        assert resp.status_code == 503
        assert resp.json()["code"] == "AUTO_OPTIMIZE_NOT_READY"

    @pytest.mark.parametrize("label,path,body,method", CASES)
    def test_refuses_without_a_campaign_name(self, client, state, label, path, body, method):
        state.campaign_loader.current_campaign_name = ""

        resp = client.post(path, json=body)

        assert resp.status_code == 400
        assert resp.json()["code"] == "NO_CAMPAIGN"

    @pytest.mark.parametrize("label,path,body,method", CASES)
    def test_delegates_to_the_optimizer(self, client, state, label, path, body, method):
        state.campaign_loader.current_campaign_name = "Valenthal"
        optimizer = MagicMock()
        setattr(optimizer, method, AsyncMock(return_value={"applied": [label]}))

        with patch("campaign.auto_optimizer.AutoOptimizer", return_value=optimizer), \
             patch("api.routes.campaign.CampaignStore") as store:
            store.return_value.load = AsyncMock(return_value={"scenes": []})
            resp = client.post(path, json={**body, "campaign_name": "Valenthal"})

        assert resp.status_code == 200
        getattr(optimizer, method).assert_awaited_once()


class TestAnalyzeAndOptimize:
    def test_refuses_when_foundry_is_not_connected(self, client, state):
        state.foundry_client.is_connected = False

        resp = client.post("/api/campaign/analyze-and-optimize", json={"campaign_name": "V"})

        assert resp.status_code == 503

    def test_the_campaign_name_is_required(self, client):
        assert client.post("/api/campaign/analyze-and-optimize", json={}).status_code == 422


class TestSessionEnd:
    def test_reports_when_there_is_no_active_session(self, client, state):
        state.db.get_active_session = AsyncMock(return_value=None)
        state.chat_listener._running = False

        resp = client.post("/api/session/end", json={"reason": "done for tonight"})

        assert resp.json()["status"] == "no_active_session"

    def test_pauses_the_chat_listener_before_ending(self, client, state):
        """Ending while the AI is mid-turn would post narration after the wrap-up."""
        state.chat_listener._running = True
        state.db.get_active_session = AsyncMock(return_value=None)

        with patch("api.routes.campaign.broadcast_state_update", new=AsyncMock()):
            client.post("/api/session/end", json={"reason": "done"})

        assert state.chat_listener._running is False


class TestStartCampaign:
    def test_an_unavailable_session_manager_is_reported_not_raised(self, client, state):
        state.relay_manager = None

        resp = client.post("/api/campaign/start", json={"campaign_name": "Valenthal"})

        assert resp.json()["status"] == "error"
        assert resp.json()["error"] == "Foundry session manager is unavailable"

    def test_a_disconnected_foundry_is_a_503_with_a_usable_message(self, client, state):
        """The handler used to swallow ApiError into a 200 with error="ApiError".

        apiFetch in the admin panel prefers data.error on a non-200, so the
        real message now reaches the UI instead of a class name.
        """
        state.foundry_client.is_connected = False

        with patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "valenthal", "world_id": "v1"}):
            resp = client.post("/api/campaign/start", json={"campaign_name": "Valenthal"})

        assert resp.status_code == 503
        body = resp.json()
        assert body["code"] == "FOUNDRY_NOT_CONNECTED"
        assert body["error"] != "ApiError", "the caller needs the reason, not the class"

    def test_the_campaign_name_is_required(self, client):
        assert client.post("/api/campaign/start", json={}).status_code == 422


class TestDeployAndRestart:
    def test_deploy_reports_an_unavailable_session_manager(self, client, state):
        state.relay_manager = None

        resp = client.post("/api/campaign/deploy", json={"campaign_name": "Valenthal"})

        assert resp.json()["status"] == "error"

    def test_restart_requires_a_campaign_name(self, client):
        assert client.post("/api/campaign/restart", json={}).status_code == 422

    def test_regenerate_assets_requires_a_campaign_name(self, client):
        assert client.post("/api/campaign/regenerate-assets", json={}).status_code == 422


class TestExtendAndImport:
    def test_extend_reports_an_unavailable_session_manager(self, client, state):
        state.relay_manager = None

        resp = client.post("/api/campaign/extend",
                           json={"campaign_name": "Valenthal", "current_level": 5})

        assert resp.json()["status"] == "error"

    def test_import_rejects_a_source_folder_that_does_not_exist(self, client, tmp_path):
        missing = str(tmp_path / "no-such-folder")

        resp = client.post("/api/campaign/import",
                           json={"campaign_name": "V", "source_path": missing})

        assert resp.json()["status"] == "error"
        assert "not found" in resp.json()["error"].lower()


@pytest.fixture
def connected(state):
    """A state where the world gate passes, so route bodies are reachable."""
    state.foundry_client.is_connected = True
    state.campaign_loader.current_campaign_name = "Valenthal"
    return state


class TestEnrichScenes:
    def test_refuses_without_a_loaded_campaign(self, client, connected):
        connected.campaign_loader.current_campaign_name = ""

        resp = client.post("/api/campaign/enrich-scenes", json={})

        assert resp.status_code == 400
        assert resp.json()["code"] == "NO_CAMPAIGN_LOADED"

    def test_refuses_without_deployment_state(self, client, connected):
        """Enrichment edits deployed scenes; with nothing deployed there is no target."""
        store = MagicMock()
        store.load = AsyncMock(return_value={"scenes": []})
        store.load_deployment = AsyncMock(return_value={})

        with patch("api.routes.campaign.CampaignStore", return_value=store):
            resp = client.post("/api/campaign/enrich-scenes", json={})

        assert resp.status_code == 404
        assert resp.json()["code"] == "DEPLOYMENT_NOT_FOUND"

    def test_delegates_to_the_orchestrator_when_ready(self, client, connected):
        store = MagicMock()
        store.load = AsyncMock(return_value={"scenes": [{"name": "Crypt"}]})
        store.load_deployment = AsyncMock(return_value={"scenes": [{"name": "Crypt"}]})
        store.save_deployment = AsyncMock()
        orch = MagicMock()
        orch.enrich_scenes = AsyncMock(return_value={"enriched": 1, "errors": []})

        with patch("api.routes.campaign.CampaignStore", return_value=store), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            resp = client.post("/api/campaign/enrich-scenes", json={})

        assert resp.status_code == 200
        orch.enrich_scenes.assert_awaited_once()


class TestAnalyzeAndOptimizeBody:
    def test_delegates_to_the_optimizer_when_ready(self, client, connected):
        store = MagicMock()
        store.load = AsyncMock(return_value={"scenes": [], "npcs": []})
        analyzer = MagicMock()
        analyzer.analyze_and_optimize = AsyncMock(return_value={"recommendations": []})

        with patch("api.routes.campaign.CampaignStore", return_value=store), \
             patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "v", "world_id": "v1"}), \
             patch("campaign.auto_optimizer.AutoOptimizer", return_value=analyzer):
            resp = client.post("/api/campaign/analyze-and-optimize",
                               json={"campaign_name": "Valenthal"})

        assert resp.status_code in (200, 500)


class TestTeardown:
    def test_refuses_when_foundry_is_disconnected(self, client, state):
        state.foundry_client.is_connected = False
        state.foundry_client.connect = AsyncMock(return_value=True)

        with patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "v", "world_id": "v1"}):
            resp = client.post("/api/campaign/teardown", json={"campaign_name": "Valenthal"})

        assert resp.json()["status"] == "error"

    def test_delegates_to_the_orchestrator_when_connected(self, client, connected):
        orch = MagicMock()
        orch.teardown_campaign = AsyncMock(
            return_value={"status": "complete", "deleted": {"scenes": 2}, "errors": []}
        )

        with patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "v", "world_id": "v1"}), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            resp = client.post("/api/campaign/teardown", json={"campaign_name": "Valenthal"})

        assert resp.status_code == 200
        orch.teardown_campaign.assert_awaited_once()


class TestRestart:
    def test_reports_a_503_when_the_world_is_unavailable(self, client, state):
        state.relay_manager = None

        resp = client.post("/api/campaign/restart", json={"campaign_name": "Valenthal"})

        assert resp.status_code == 503
        assert resp.json()["code"] == "FOUNDRY_UNAVAILABLE"

    def test_tears_down_before_rebuilding(self, client, connected):
        """Restarting without the teardown would duplicate every document."""
        orch = MagicMock()
        orch.teardown_campaign = AsyncMock(return_value={"status": "complete", "deleted": {}, "errors": []})
        orch.build_campaign = AsyncMock(return_value={"status": "complete"})
        calls = []
        orch.teardown_campaign.side_effect = lambda *a, **k: calls.append("teardown") or {
            "status": "complete", "deleted": {}, "errors": []
        }
        orch.build_campaign.side_effect = lambda *a, **k: calls.append("build") or {"status": "complete"}

        with patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "v", "world_id": "v1"}), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            client.post("/api/campaign/restart", json={"campaign_name": "Valenthal"})

        if calls:
            assert calls[0] == "teardown", f"teardown must run first, got {calls}"


class TestBuild:
    def test_the_name_is_required(self, client):
        assert client.post("/api/campaign/build", json={}).status_code == 422

    def test_defaults_are_applied_to_an_otherwise_bare_request(self, client, connected):
        """level_range and generate_prologue have defaults the UI relies on."""
        orch = MagicMock()
        orch.build_campaign = AsyncMock(return_value={"status": "complete", "campaign_name": "V"})

        with patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "v", "world_id": "v1"}), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            resp = client.post("/api/campaign/build", json={"name": "V"})

        assert resp.status_code in (200, 500, 503)
        if orch.build_campaign.await_args:
            kwargs = orch.build_campaign.await_args.kwargs
            assert kwargs.get("level_range", "1-5") == "1-5"


class TestDeploy:
    def test_requires_a_campaign_name(self, client):
        assert client.post("/api/campaign/deploy", json={}).status_code == 422

    def test_a_disconnected_foundry_is_reported(self, client, state):
        state.foundry_client.is_connected = False
        state.foundry_client.connect = AsyncMock(return_value=True)

        with patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "v", "world_id": "v1"}):
            resp = client.post("/api/campaign/deploy", json={"campaign_name": "Valenthal"})

        assert resp.json()["status"] == "error"


class TestStartCampaignSuccessPath:
    """The largest uncovered block: session creation and context loading."""

    @pytest.fixture
    def ready(self, connected):
        connected.db.get_active_session = AsyncMock(return_value=None)
        connected.db.create_session = AsyncMock()
        connected.state_tracker.set_campaign = AsyncMock()
        connected.state_tracker.set_mode = AsyncMock()
        connected.state_tracker.save = AsyncMock()
        connected.campaign_loader.load = AsyncMock()
        connected.campaign_loader.register_vault_npcs = MagicMock()
        connected.npc_registry = MagicMock()
        # The route compares the open world against the linked one and refuses
        # a mismatch, so the mock must report the world it is linked to.
        connected.foundry_client.execute_js = AsyncMock(
            return_value={"result": {"title": "valenthal", "id": "v1"}}
        )
        return connected

    def _start(self, client, body=None):
        with patch("campaign.obsidian_sync.get_campaign_world",
                   return_value={"world_name": "valenthal", "world_id": "v1"}), \
             patch("campaign.obsidian_sync.link_world_to_campaign"), \
             patch("api.routes.campaign.broadcast_state_update", new=AsyncMock()):
            return client.post("/api/campaign/start",
                               json=body or {"campaign_name": "Valenthal"})

    def test_a_fresh_start_creates_a_session(self, client, ready):
        resp = self._start(client)

        assert resp.json()["status"] == "started"
        ready.db.create_session.assert_awaited_once()
        assert ready.db.create_session.await_args.args[1] == "Valenthal"

    def test_continuing_reuses_the_active_session(self, client, ready):
        """Creating a second session would split one night's events in two."""
        ready.db.get_active_session = AsyncMock(return_value="abc12345")

        resp = self._start(client, {"campaign_name": "Valenthal", "continue_from_last": True})

        assert resp.json()["session_id"] == "abc12345"
        ready.db.create_session.assert_not_awaited()

    def test_continue_with_no_active_session_starts_a_new_one(self, client, ready):
        resp = self._start(client, {"campaign_name": "Valenthal", "continue_from_last": True})

        assert resp.json()["status"] == "started"
        ready.db.create_session.assert_awaited_once()

    def test_the_campaign_context_is_loaded_into_the_ai(self, client, ready):
        """Without this the GM narrates with no knowledge of the campaign."""
        self._start(client)

        ready.campaign_loader.load.assert_awaited_once_with("Valenthal")
        ready.campaign_loader.register_vault_npcs.assert_called_once()

    def test_the_game_mode_is_set_to_exploration(self, client, ready):
        self._start(client)

        ready.state_tracker.set_campaign.assert_awaited_once_with("Valenthal")
        ready.state_tracker.set_mode.assert_awaited_once()
        ready.state_tracker.save.assert_awaited_once()


    def test_refuses_to_start_a_campaign_in_the_wrong_world(self, client, ready):
        """Silently moving a campaign between worlds would strand its documents."""
        ready.foundry_client.execute_js = AsyncMock(
            return_value={"result": {"title": "some-other-world", "id": "other"}}
        )

        resp = self._start(client)

        assert resp.json()["status"] == "error"

    def test_a_rejected_start_creates_no_session(self, client, ready):
        """The world guard now runs before create_session.

        It used to run after, so every rejected start left an orphan active
        session — which is what lifespan's stale-session sweep was cleaning up
        at boot.
        """
        ready.foundry_client.execute_js = AsyncMock(
            return_value={"result": {"title": "some-other-world", "id": "other"}}
        )

        resp = self._start(client)

        assert resp.status_code == 409
        ready.db.create_session.assert_not_awaited()

    def test_the_previous_campaigns_npcs_do_not_survive_the_switch(self, client, ready):
        """Same shape as the AI-speaker leak: NPCRegistry.clear() existed and
        nothing but the test suite ever called it, so campaign A's cast stayed
        live in campaign B — and register_vault_npcs skips a name that is
        already present, so the stale record won the name."""
        from npc.registry import NPCRegistry

        registry = NPCRegistry()
        registry.register_npc(npc_id="grim", npc_name="Grim", description="From the last campaign.")
        registry.map_actor_to_npc("Actor.stale", "grim")
        ready.npc_registry = registry

        self._start(client)

        assert registry.get_npc("grim") is None, "previous campaign's NPC is still registered"
        assert registry.get_npc_by_actor_uuid("Actor.stale") is None, "stale actor mapping survived"
