"""Behavioural coverage for api/routes/campaign.py, part 3: vault CRUD against a
real temporary vault, plus the enrich / analyze / auto-optimize endpoints."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import campaign as routes
from config import settings


@pytest.fixture
def state():
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.relay_manager = AsyncMock()
    s.db = AsyncMock()
    s.campaign_loader = MagicMock()
    s.campaign_loader.current_campaign_name = "Krynn"
    s.llm_manager = MagicMock()
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
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "campaign_vault_path", str(tmp_path / "vault"))
    base = tmp_path / "vault" / "Campaigns"
    base.mkdir(parents=True)
    return base


def _orch(**methods):
    orch = MagicMock()
    for name, rv in methods.items():
        setattr(orch, name, AsyncMock(side_effect=rv) if isinstance(rv, Exception) else AsyncMock(return_value=rv))
    return orch


def _store(data=None, deployment=None):
    store = MagicMock()
    store.load = AsyncMock(return_value=data if data is not None else {"scenes": []})
    store.load_deployment = AsyncMock(return_value=deployment)
    return store


class TestVaultCrud:
    def test_get_reads_the_real_manifest_and_counts(self, client, vault):
        (vault / "Krynn").mkdir()
        (vault / "Krynn" / "campaign.json").write_text(json.dumps({
            "name": "Krynn", "npcs": [{"n": 1}, {"n": 2}], "locations": [{"n": 1}],
            "quest_logs": [{"q": 1}]}))
        b = client.get("/api/campaign/get/Krynn").json()
        assert b["npc_count"] == 2 and b["location_count"] == 1 and b["quest_count"] == 1
        assert b["data"]["name"] == "Krynn"

    def test_get_unknown_and_corrupt(self, client, vault):
        assert client.get("/api/campaign/get/Nope").status_code == 404
        (vault / "Bad").mkdir()
        (vault / "Bad" / "campaign.json").write_text("{not json")
        r = client.get("/api/campaign/get/Bad")
        assert r.status_code == 500 and r.json()["code"] == "CAMPAIGN_LOAD_FAILED"

    def test_get_cannot_read_outside_the_vault(self, client, vault, tmp_path):
        (tmp_path / "vault" / "campaign.json").write_text(json.dumps({"name": "escaped"}))
        (tmp_path / "campaign.json").write_text(json.dumps({"name": "escaped"}))
        for name in ("..", "%2e%2e", "..%2f..", "....//"):
            r = client.get(f"/api/campaign/get/{name}")
            assert "escaped" not in r.text, name

    def test_delete_removes_only_the_named_campaign(self, client, vault):
        (vault / "Keep").mkdir()
        (vault / "Drop").mkdir()
        (vault / "Drop" / "campaign.json").write_text("{}")
        assert client.post("/api/campaign/delete", json={"name": "Drop"}).json() == {"status": "deleted"}
        assert not (vault / "Drop").exists() and (vault / "Keep").exists()
        assert client.post("/api/campaign/delete", json={"name": "Drop"}).json() == {"status": "not_found"}

    def test_delete_traversal_name_cannot_remove_the_vault(self, client, vault):
        (vault / "Keep").mkdir()
        for name in ("..", "../..", "../Campaigns", "/"):
            client.post("/api/campaign/delete", json={"name": name})
        assert vault.exists() and (vault / "Keep").exists()

    def test_delete_requires_name_and_hides_errors(self, client, vault):
        assert client.post("/api/campaign/delete", json={}).status_code == 422
        with patch("campaign.obsidian_sync.delete_campaign", AsyncMock(side_effect=OSError("/secret"))):
            r = client.post("/api/campaign/delete", json={"name": "x"})
        assert r.status_code == 500 and r.json()["code"] == "CAMPAIGN_DELETE_FAILED" and "secret" not in r.text

    def test_list_failure_is_type_only(self, client):
        with patch("campaign.obsidian_sync.list_campaigns", side_effect=OSError("/secret")):
            b = client.get("/api/campaign/list").json()
        assert b == {"campaigns": [], "error": "OSError"}


class TestEnrichScenes:
    def test_requires_foundry(self, client, state):
        state.foundry_client.is_connected = False
        assert client.post("/api/campaign/enrich-scenes").status_code == 503

    def test_orchestrator_failure_is_500_type_only(self, client):
        orch = _orch(enrich_scenes=RuntimeError("/secret"))
        with patch.object(routes, "CampaignStore", return_value=_store(deployment={"scenes": [1]})), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            r = client.post("/api/campaign/enrich-scenes")
        assert r.status_code == 500 and r.json()["code"] == "ENRICHMENT_FAILED" and "secret" not in r.text

    def test_reports_counts(self, client):
        orch = _orch(enrich_scenes={"enriched": 2, "skipped": 1, "errors": ["e"]})
        with patch.object(routes, "CampaignStore", return_value=_store(deployment={"scenes": [1]})), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            b = client.post("/api/campaign/enrich-scenes").json()
        assert (b["enriched"], b["skipped"], b["errors"]) == (2, 1, ["e"])


class TestAnalyzeAndOptimize:
    def _post(self, client, store, analysis=None, orch=None, world=None, body=None):
        analyzer = MagicMock()
        analyzer.analyze_campaign = AsyncMock(return_value=analysis if analysis is not None else {})
        orch = orch or _orch(scan_foundry_world={"active_modules": {"midi": {"title": "Midi"}, "dae": {}}},
                             enrich_scenes={"enriched": 2, "skipped": 1, "errors": []})
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=world)), \
             patch.object(routes, "CampaignStore", return_value=store), \
             patch("campaign.analyzer.CampaignAnalyzer", return_value=analyzer), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            return client.post("/api/campaign/analyze-and-optimize",
                               json=body or {"campaign_name": "C"}), orch

    def test_blank_name_is_400_and_world_error_503(self, client):
        r, _ = self._post(client, _store(), body={"campaign_name": ""})
        assert r.status_code == 400 and r.json()["code"] == "NO_CAMPAIGN_PROVIDED"
        r, _ = self._post(client, _store(), world="no world")
        assert r.status_code == 503 and r.json()["error"] == "no world"

    def test_disconnected_is_503(self, client, state):
        state.foundry_client.is_connected = False
        r, _ = self._post(client, _store())
        assert r.status_code == 503

    def test_applies_enrichment_and_summarises(self, client):
        analysis = {"scenes": [1, 2], "encounters": [1], "npcs": [1, 2, 3], "narrative_arcs": [1],
                    "immersion_gaps": ["no sound", {"gap": 1}, "g3", "g4"]}
        r, orch = self._post(client, _store(deployment={"scenes": [1]}), analysis=analysis)
        b = r.json()
        assert b["status"] == "complete" and b["applied"]["scenes_enriched"] == 2
        assert b["analysis"]["scene_count"] == 2 and b["analysis"]["npc_count"] == 3
        assert b["analysis"]["immersion_gaps_identified"] == 4
        assert b["modules"]["total_installed"] == 2
        assert {m["id"]: m["name"] for m in b["modules"]["modules_list"]} == {"midi": "Midi", "dae": "dae"}
        recs = b["recommendations"]
        assert recs[0]["category"] == "Applied" and recs[0]["count"] == 2
        assert [x["category"] for x in recs[1:]] == ["Immersion Gap"] * 3  # capped at 3
        orch.enrich_scenes.assert_awaited_once()

    def test_not_deployed_reports_instead_of_enriching(self, client):
        r, orch = self._post(client, _store(deployment=None))
        orch.enrich_scenes.assert_not_awaited()
        assert r.json()["applied"]["scenes_enriched"] == 0
        assert "deploy the campaign first" in r.json()["applied"]["errors"][0]

    def test_failure_is_500_type_only(self, client):
        orch = _orch(scan_foundry_world=RuntimeError("/secret"))
        r, _ = self._post(client, _store(), orch=orch)
        assert r.status_code == 500 and r.json()["code"] == "OPTIMIZATION_FAILED" and "secret" not in r.text


class TestAutoOptimize:
    CASES = [
        ("scene", "optimize_new_scene", {"name": "Crypt"}, "Crypt", "SCENE_OPTIMIZE_FAILED"),
        ("encounter", "optimize_new_encounter", {"name": "Ambush"}, "Ambush", "ENCOUNTER_OPTIMIZE_FAILED"),
        ("quest", "optimize_new_quest", {"title": "Find it"}, "Find it", "QUEST_OPTIMIZE_FAILED"),
    ]

    def _post(self, client, kind, method, payload, rv, campaign=None):
        optimizer = MagicMock()
        setattr(optimizer, method, AsyncMock(side_effect=rv) if isinstance(rv, Exception) else AsyncMock(return_value=rv))
        body = {kind: payload}
        if campaign:
            body["campaign_name"] = campaign
        store_cls = MagicMock(return_value=_store({"scenes": [1]}))
        with patch.object(routes, "CampaignStore", store_cls), \
             patch("campaign.auto_optimizer.AutoOptimizer", return_value=optimizer):
            r = client.post(f"/api/campaign/auto-optimize-{kind}", json=body)
        return r, optimizer, store_cls

    @pytest.mark.parametrize("kind,method,payload,label,code", CASES)
    def test_not_ready_503(self, client, state, kind, method, payload, label, code):
        state.foundry_client.is_connected = False
        r, opt, _ = self._post(client, kind, method, payload, {})
        assert r.status_code == 503 and r.json()["code"] == "AUTO_OPTIMIZE_NOT_READY"
        getattr(opt, method).assert_not_awaited()

    @pytest.mark.parametrize("kind,method,payload,label,code", CASES)
    def test_no_campaign_is_400(self, client, state, kind, method, payload, label, code):
        state.campaign_loader.current_campaign_name = ""
        r, opt, _ = self._post(client, kind, method, payload, {})
        assert r.status_code == 400 and r.json()["code"] == "NO_CAMPAIGN"

    @pytest.mark.parametrize("kind,method,payload,label,code", CASES)
    def test_loaded_campaign_is_the_default_and_explicit_name_wins(self, client, kind, method, payload, label, code):
        r, opt, store_cls = self._post(client, kind, method, payload, {"added": 1})
        assert r.json() == {"status": "optimized", kind: label, "enhancements": {"added": 1}}
        store_cls.assert_called_once_with("Krynn")
        getattr(opt, method).assert_awaited_once_with(payload, {"scenes": [1]})
        r, opt, store_cls = self._post(client, kind, method, payload, {}, campaign="Other")
        store_cls.assert_called_once_with("Other")

    @pytest.mark.parametrize("kind,method,payload,label,code", CASES)
    def test_failure_is_500_type_only(self, client, kind, method, payload, label, code):
        r, _, _ = self._post(client, kind, method, payload, RuntimeError("/secret"))
        assert r.status_code == 500 and r.json()["code"] == code and "secret" not in r.text

    def test_payload_is_required(self, client):
        assert client.post("/api/campaign/auto-optimize-scene", json={}).status_code == 422


class TestEnrichCampaign:
    def _post(self, client, orch=None, world=None, **body):
        body = {"campaign_name": "Krynn", **body}
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=world)) as sel, \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch or _orch(enrich_campaign={"status": "ok"})):
            return client.post("/api/campaign/enrich", json=body), sel

    def test_needs_a_source(self, client):
        r, _ = self._post(client)
        assert r.json()["status"] == "error" and "Give a source_path" in r.json()["error"]

    def test_foundry_sources_select_the_world_first(self, client):
        orch = _orch(enrich_campaign={"status": "ok"})
        r, sel = self._post(client, orch=orch, journal_pack="w.pack", world="no world")
        assert r.json()["error"] == "no world"
        orch.enrich_campaign.assert_not_awaited()

    def test_local_source_does_not_need_foundry(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "source_roots", [str(tmp_path)])
        r, sel = self._post(client, source_path=str(tmp_path))
        sel.assert_not_awaited()
        assert r.json()["status"] == "ok"

    def test_reloads_only_the_active_campaign_when_sources_were_added(self, client, state):
        loader = state.campaign_loader
        loader.reload = AsyncMock()
        orch = _orch(enrich_campaign={"status": "ok", "sources": [{"id": "a"}], "skipped": ["b"], "conflicts": 2})
        r, _ = self._post(client, orch=orch, journal_folder="Lore")
        b = r.json()
        assert b["reloaded"] is True and b["skipped"] == ["b"] and b["conflicts"] == 2
        loader.reload.assert_awaited_once()
        state.llm_manager.refresh_campaign_context.assert_called_once()

    def test_no_reload_for_another_campaign_or_no_sources(self, client, state):
        state.campaign_loader.reload = AsyncMock()
        orch = _orch(enrich_campaign={"status": "ok", "sources": [{"id": "a"}]})
        state.campaign_loader.current_campaign_name = "Other"
        assert self._post(client, orch=orch, journal_pack="p")[0].json()["reloaded"] is False
        state.campaign_loader.current_campaign_name = "Krynn"
        orch = _orch(enrich_campaign={"status": "ok", "sources": []})
        assert self._post(client, orch=orch, journal_pack="p")[0].json()["reloaded"] is False
        state.campaign_loader.reload.assert_not_awaited()

    def test_failure_is_type_only(self, client):
        r, _ = self._post(client, orch=_orch(enrich_campaign=RuntimeError("/secret")), journal_pack="p")
        assert r.json()["error"] == "RuntimeError" and "secret" not in r.text

    def test_conflict_sink_without_db_records_nothing(self, client, state):
        state.db = None
        results = {}

        async def fake(**kw):
            results["recorded"] = await kw["conflict_sink"]("claim", "why", "existing")
            return {"status": "ok"}

        orch = MagicMock()
        orch.enrich_campaign = fake
        self._post(client, orch=orch, journal_pack="p")
        assert results["recorded"] is False
