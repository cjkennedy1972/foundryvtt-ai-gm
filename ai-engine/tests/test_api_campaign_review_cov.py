"""Behavioural coverage for api/routes/campaign.py, part 1: world selection,
build, import, extend, teardown."""

import os
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
    s.foundry_client.connect = AsyncMock(return_value=True)
    s.relay_manager = AsyncMock()
    s.relay_manager.status = MagicMock(return_value={"running": True})
    s.db = AsyncMock()
    s.campaign_loader = MagicMock()
    return s


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "relay_managed", True)
    monkeypatch.setattr(settings, "foundry_world", "")
    monkeypatch.setattr(settings, "relay_headless_client_id", "")


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


def _orch(**methods):
    orch = MagicMock()
    for name, rv in methods.items():
        setattr(orch, name, AsyncMock(return_value=rv) if not isinstance(rv, Exception)
                else AsyncMock(side_effect=rv))
    return orch


def _world(title="W", wid="w1"):
    return {"result": {"title": title, "id": wid}}


# ------------------------------------------------- _select_campaign_world

class TestSelectCampaignWorld:
    @pytest.mark.asyncio
    async def test_needs_relay_and_client(self, state):
        state.relay_manager = None
        assert "unavailable" in await routes._select_campaign_world("C", state)

    @pytest.mark.asyncio

    async def test_relay_start_failure_is_reported(self, state):
        state.relay_manager.status.return_value = {"running": False}
        state.relay_manager.start.side_effect = RuntimeError("port busy")
        assert "Could not start the relay" in await routes._select_campaign_world("C", state)

    @pytest.mark.asyncio

    async def test_relay_started_when_down_and_managed(self, state):
        state.relay_manager.status.return_value = {"running": False}
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "W"}):
            state.foundry_client.execute_js.return_value = _world("W")
            assert await routes._select_campaign_world("C", state) is None
        state.relay_manager.start.assert_awaited_once()

    @pytest.mark.asyncio

    async def test_unlinked_campaign_with_nothing_paired_gives_setup_guidance(self, state):
        state.foundry_client.connect.return_value = False
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None):
            err = await routes._select_campaign_world("C", state)
        assert "not linked to a Foundry world" in err
        state.relay_manager.restart_headless_session.assert_not_awaited()

    @pytest.mark.asyncio

    async def test_unlinked_campaign_adopts_and_links_paired_world(self, state):
        state.foundry_client.execute_js.return_value = _world("Krynn", "k1")
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None), \
             patch("campaign.obsidian_sync.link_world_to_campaign") as link:
            assert await routes._select_campaign_world("C", state) is None
        link.assert_called_once_with("C", "Krynn", "k1")

    @pytest.mark.parametrize("js", [RuntimeError("x"), {"result": {}}, {"result": None}])
    @pytest.mark.asyncio
    async def test_unlinked_campaign_with_unreadable_world_is_an_error(self, state, js):
        if isinstance(js, Exception):
            state.foundry_client.execute_js.side_effect = js
        else:
            state.foundry_client.execute_js.return_value = js
        with patch("campaign.obsidian_sync.get_campaign_world", return_value=None), \
             patch("campaign.obsidian_sync.link_world_to_campaign") as link:
            err = await routes._select_campaign_world("C", state)
        assert "did not report an active world" in err
        link.assert_not_called()

    @pytest.mark.asyncio

    async def test_already_in_linked_world_is_left_alone(self, state):
        state.foundry_client.execute_js.return_value = _world("W", "w1")
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "Other", "world_id": "w1"}):
            assert await routes._select_campaign_world("C", state) is None
        state.relay_manager.restart_headless_session.assert_not_awaited()

    @pytest.mark.asyncio

    async def test_wrong_world_relaunches_headless_into_linked_world(self, state):
        state.foundry_client.execute_js.return_value = _world("Elsewhere", "e1")
        state.relay_manager.restart_headless_session.return_value = "client-9"
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "W", "world_id": "w1"}):
            assert await routes._select_campaign_world("C", state) is None
        state.relay_manager.restart_headless_session.assert_awaited_once_with(world_name="W")
        assert settings.relay_headless_client_id == "client-9"
        state.foundry_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio

    async def test_inspection_failure_still_selects_the_world(self, state):
        state.foundry_client.execute_js.side_effect = RuntimeError("x")
        state.relay_manager.restart_headless_session.return_value = "c"
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "W"}):
            assert await routes._select_campaign_world("C", state) is None
        state.relay_manager.restart_headless_session.assert_awaited_once()

    @pytest.mark.asyncio

    async def test_launch_and_connect_failures(self, state):
        state.foundry_client.is_connected = False
        with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "W"}):
            state.relay_manager.restart_headless_session.return_value = None
            assert "Could not launch" in await routes._select_campaign_world("C", state)
            state.relay_manager.restart_headless_session.return_value = "c"
            state.foundry_client.connect.return_value = False
            assert "could not connect" in await routes._select_campaign_world("C", state)


# ------------------------------------------------------------ _attach_world

class TestAttachWorld:
    async def _attach(self, state, **kw):
        return await routes._attach_world(state, campaign_name="C", **kw)

    @pytest.mark.asyncio

    async def test_requires_relay_and_client(self, state):
        state.foundry_client = None
        world, err = await self._attach(state)
        assert world is None and "Start the relay" in err.error

    @pytest.mark.asyncio

    async def test_relay_start_failure(self, state):
        state.relay_manager.status.return_value = {"running": False}
        state.relay_manager.start.side_effect = RuntimeError("busy")
        _, err = await self._attach(state)
        assert "Could not start the relay" in err.error
        state.relay_manager.start.assert_awaited_once_with(start_foundry=False)

    @pytest.mark.asyncio

    async def test_disconnected_without_a_named_world_is_refused(self, state):
        state.foundry_client.is_connected = False
        _, err = await self._attach(state)
        assert "does not name one" in err.error
        state.relay_manager.ensure_headless_session.assert_not_awaited()

    @pytest.mark.asyncio

    async def test_disconnected_named_world_is_launched_and_connected(self, state):
        state.foundry_client.is_connected = False
        state.relay_manager.ensure_headless_session.return_value = "cid"
        state.foundry_client.execute_js.return_value = _world("Krynn", "k")
        world, err = await self._attach(state, foundry_world_name="Krynn")
        assert err is None and world == {"title": "Krynn", "id": "k"}
        state.relay_manager.ensure_headless_session.assert_awaited_once_with(world_name="Krynn")
        assert settings.relay_headless_client_id == "cid"

    @pytest.mark.asyncio

    async def test_connect_failure(self, state, monkeypatch):
        monkeypatch.setattr(settings, "foundry_world", "Env")
        state.foundry_client.is_connected = False
        state.foundry_client.connect.return_value = False
        _, err = await self._attach(state)
        assert "could not connect to Foundry world 'Env'" in err.error

    @pytest.mark.asyncio

    async def test_no_active_world_is_an_error(self, state):
        state.foundry_client.execute_js.return_value = {"result": {}}
        _, err = await self._attach(state)
        assert "did not report an active world" in err.error

    @pytest.mark.asyncio

    async def test_connected_world_is_attached(self, state):
        state.foundry_client.execute_js.return_value = _world("W", "")
        world, err = await self._attach(state)
        assert err is None and world == {"title": "W", "id": ""}


# ------------------------------------------------------------ load / scan

class TestLoadAndScan:
    def test_load_without_loader(self, client, state):
        state.campaign_loader = None
        body = client.post("/api/campaign/load", json={"name": "C"}).json()
        assert body["status"] == "error" and body["loaded_files"] == []

    def test_load_custom_campaign(self, client, state):
        state.campaign_loader.load_custom_campaign = AsyncMock(
            return_value={"folder": "/v/C", "linked_files": ["a.md"]})
        body = client.post("/api/campaign/load", json={"name": "C", "vault_files": ["a.md"]}).json()
        assert body == {"status": "ok", "name": "C", "folder": "/v/C", "loaded_files": ["a.md"]}
        state.campaign_loader.load_custom_campaign.assert_awaited_once_with("C", ["a.md"])

    def test_scan_requires_connection(self, client, state):
        state.foundry_client.is_connected = False
        assert client.post("/api/campaign/scan", json={}).json()["error"] == "Not connected to FoundryVTT"

    def test_scan_collects_world_and_capabilities(self, client, state):
        state.foundry_client.scan_world.return_value = {"world": {"id": "w"}, "scenes": [{"n": 1}]}
        state.foundry_client.discover_addon_capabilities.return_value = {"fog": True}
        body = client.post("/api/campaign/scan", json={"world_name": "W"}).json()
        assert body["status"] == "ok" and body["world"] == {"id": "w"}
        assert body["scenes"] == [{"n": 1}] and body["capabilities"] == {"fog": True}
        assert body["actors"] == []

    def test_scan_failure_reports_type_only(self, client, state):
        state.foundry_client.scan_world.side_effect = RuntimeError("/secret")
        body = client.post("/api/campaign/scan", json={}).json()
        assert body["status"] == "error" and body["error"] == "RuntimeError"


# ------------------------------------------------------------------ build

class TestBuild:
    BODY = {"name": "Krynn", "description": "grim", "seed_ideas": "a lich", "level_range": "3-8"}

    def _run(self, client, orch, attach=({"title": "W", "id": "w"}, None), link=True, body=None):
        with patch.object(routes, "_attach_world", AsyncMock(return_value=attach)), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch), \
             patch("campaign.obsidian_sync.link_world_to_campaign", return_value=link) as lk:
            r = client.post("/api/campaign/build", json=body or self.BODY)
        return r, lk

    def test_world_error_is_returned_and_orchestrator_not_run(self, client):
        err = routes.CampaignBuildResponse(status="error", campaign_id="x", campaign_name="Krynn", error="no world")
        orch = _orch(build_campaign={})
        r, _ = self._run(client, orch, attach=(None, err))
        assert r.json()["error"] == "no world"
        orch.build_campaign.assert_not_awaited()

    def test_success_builds_prompt_links_world_and_maps_result(self, client):
        orch = _orch(build_campaign={"status": "success", "campaign_id": "c1", "assets": {"maps": 2},
                                     "steps_completed": [{"s": 1}], "progress": 5})
        r, lk = self._run(client, orch)
        body = r.json()
        assert body["status"] == "success" and body["campaign_id"] == "c1"
        assert body["ready_to_start"] is True and body["maps_generated"] == {"maps": 2}
        kw = orch.build_campaign.await_args.kwargs
        assert kw["campaign_name"] == "Krynn" and kw["level_range"] == "3-8"
        assert "Theme: grim" in kw["prompt"] and "Seed ideas from user: a lich" in kw["prompt"]
        assert "Level range: 3-8" in kw["prompt"]
        lk.assert_called_once_with("Krynn", "W", "w")

    def test_default_level_range_is_not_added_to_prompt(self, client):
        orch = _orch(build_campaign={"status": "success"})
        self._run(client, orch, body={"name": "K"})
        assert "Level range" not in orch.build_campaign.await_args.kwargs["prompt"]

    def test_failed_build_is_not_linked_or_ready(self, client):
        orch = _orch(build_campaign={"status": "error", "error": "llm down"})
        r, lk = self._run(client, orch)
        assert r.json()["ready_to_start"] is False and r.json()["error"] == "llm down"
        lk.assert_not_called()

    def test_link_save_failure_is_surfaced(self, client):
        orch = _orch(build_campaign={"status": "success", "campaign_id": "c1"})
        r, _ = self._run(client, orch, link=False)
        assert r.json()["status"] == "error" and "link could not be saved" in r.json()["error"]
        assert r.json()["ready_to_start"] is False

    def test_character_created_only_for_successful_build_with_concept(self, client, state):
        state.foundry_client.create_player_character.return_value = {"id": "pc"}
        orch = _orch(build_campaign={"status": "success"})
        with patch("foundry.character.character_from_concept", return_value={"spec": 1}) as cfc:
            r, _ = self._run(client, orch, body={**self.BODY, "character_concept": "a dwarf", "character_user_id": "u1"})
        assert r.json()["player_character"] == {"id": "pc"}
        cfc.assert_called_once_with("a dwarf", "Adventurer")
        state.foundry_client.create_player_character.assert_awaited_once_with({"spec": 1}, "u1")

    def test_no_character_when_build_failed_or_concept_blank(self, client, state):
        orch = _orch(build_campaign={"status": "error"})
        self._run(client, orch, body={**self.BODY, "character_concept": "a dwarf"})
        orch = _orch(build_campaign={"status": "success"})
        self._run(client, orch, body={**self.BODY, "character_concept": "   "})
        state.foundry_client.create_player_character.assert_not_awaited()

    def test_orchestrator_exception_reports_type_only(self, client):
        orch = _orch(build_campaign=RuntimeError("/secret"))
        r, _ = self._run(client, orch)
        assert r.json()["status"] == "error" and r.json()["error"] == "RuntimeError"
        assert "secret" not in r.text


# ------------------------------------------------------------------ import

class TestImport:
    def _run(self, client, src, orch=None, attach=({"title": "W", "id": "w"}, None), link=True, extra=None):
        body = {"campaign_name": "C", "source_path": str(src), **(extra or {})}
        with patch.object(routes, "_attach_world", AsyncMock(return_value=attach)), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch or _orch()), \
             patch("campaign.obsidian_sync.link_world_to_campaign", return_value=link):
            return client.post("/api/campaign/import", json=body)

    @pytest.fixture
    def allowed(self, tmp_path, monkeypatch):
        d = tmp_path / "published"
        d.mkdir()
        monkeypatch.setattr(settings, "source_roots", [str(tmp_path)])
        return d

    def test_source_outside_allowed_roots_is_refused_without_reading_it(self, client, tmp_path, monkeypatch):
        """import reads a whole folder and sends it to the LLM: it must honour SOURCE_ROOTS like enrich does."""
        root = tmp_path / "allowed"
        outside = tmp_path / "outside"
        root.mkdir(); outside.mkdir()
        (outside / "secret.md").write_text("private")
        monkeypatch.setattr(settings, "source_roots", [str(root)])
        orch = _orch(import_campaign={"status": "success"})
        r = self._run(client, outside, orch=orch)
        assert r.json()["status"] == "error"
        orch.import_campaign.assert_not_awaited()
        assert str(outside) not in r.text  # the refusal must not echo or confirm the path

    def test_dotdot_escape_from_root_is_refused(self, client, tmp_path, monkeypatch):
        root = tmp_path / "allowed"
        (tmp_path / "outside").mkdir(); root.mkdir()
        monkeypatch.setattr(settings, "source_roots", [str(root)])
        orch = _orch(import_campaign={"status": "success"})
        r = self._run(client, f"{root}/../outside", orch=orch)
        assert r.json()["status"] == "error"
        orch.import_campaign.assert_not_awaited()

    def test_missing_folder_inside_root_is_refused(self, client, allowed):
        r = self._run(client, allowed / "nope")
        assert r.json()["status"] == "error" and "not found" in r.json()["error"].lower()

    def test_world_error_returned(self, client, allowed):
        err = routes.CampaignBuildResponse(status="error", campaign_id="x", campaign_name="C", error="no world")
        orch = _orch(import_campaign={})
        assert self._run(client, allowed, orch=orch, attach=(None, err)).json()["error"] == "no world"
        orch.import_campaign.assert_not_awaited()

    def test_success_passes_options_and_links(self, client, allowed):
        orch = _orch(import_campaign={"status": "complete", "campaign_id": "c9", "steps": [{"a": 1}],
                                      "import_summary": {"npcs": 3}})
        r = self._run(client, allowed, orch=orch, extra={"journal_pack": "world.pack", "level_range": ""})
        body = r.json()
        assert body["campaign_id"] == "c9" and body["steps_completed"] == [{"a": 1}]
        assert body["import_summary"] == {"npcs": 3} and body["ready_to_start"] is True
        kw = orch.import_campaign.await_args.kwargs
        assert kw["source_path"] == os.path.realpath(allowed) and kw["journal_pack"] == "world.pack"
        assert kw["level_range"] == "1-5"

    def test_link_failure_and_exception(self, client, allowed):
        orch = _orch(import_campaign={"status": "success"})
        assert "link could not be saved" in self._run(client, allowed, orch=orch, link=False).json()["error"]
        orch = _orch(import_campaign=ValueError("/secret"))
        r = self._run(client, allowed, orch=orch)
        assert r.json()["error"] == "ValueError" and "secret" not in r.text


# ----------------------------------------------------------- extend / teardown

class TestExtend:
    def test_world_error(self, client):
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value="no world")):
            r = client.post("/api/campaign/extend", json={"campaign_name": "C"})
        assert r.json() == {**r.json(), "status": "error", "error": "no world"}

    def test_success_maps_arc(self, client):
        orch = _orch(extend_campaign_arc={"status": "ok", "arc_number": 2, "arc_title": "T",
                                          "steps": [{"s": 1}], "assets": {"m": 1}})
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=None)), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            r = client.post("/api/campaign/extend", json={"campaign_name": "C", "current_level": 7})
        assert (r.json()["arc_number"], r.json()["arc_title"]) == (2, "T")
        assert orch.extend_campaign_arc.await_args.kwargs["current_level"] == 7

    def test_exception_is_type_only(self, client):
        orch = _orch(extend_campaign_arc=RuntimeError("/secret"))
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=None)), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
            r = client.post("/api/campaign/extend", json={"campaign_name": "C"})
        assert r.json()["error"] == "RuntimeError"


class TestTeardown:
    def _post(self, client, orch=None, world=None):
        with patch.object(routes, "_select_campaign_world", AsyncMock(return_value=world)), \
             patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch or _orch()):
            return client.post("/api/campaign/teardown", json={"campaign_name": "C"})

    def test_world_error_and_disconnected(self, client, state):
        assert self._post(client, world="nope").json()["errors"] == ["nope"]
        state.foundry_client.is_connected = False
        orch = _orch(teardown_campaign={})
        assert "Not connected" in self._post(client, orch=orch).json()["errors"][0]
        orch.teardown_campaign.assert_not_awaited()

    def test_success_and_failure(self, client, state):
        orch = _orch(teardown_campaign={"deleted": {"scenes": 2}, "errors": ["e"]})
        body = self._post(client, orch=orch).json()
        assert body["deleted"] == {"scenes": 2} and body["errors"] == ["e"]
        orch.teardown_campaign.assert_awaited_once_with(campaign_name="C", foundry_client=state.foundry_client)
        body = self._post(client, orch=_orch(teardown_campaign=RuntimeError("/s"))).json()
        assert body["status"] == "error" and body["errors"] == ["RuntimeError"]
