"""Branches of api/routes/campaign.py not reached by test_campaign_routes.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import campaign as cr

WORLD = {"title": "valenthal", "id": "v1"}


def _state(**kw):
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.foundry_client._last_connect_error = "bad password"
    s.foundry_client.execute_js = AsyncMock(return_value={"result": dict(WORLD)})
    s.db = AsyncMock()
    s.db.get_active_session = AsyncMock(return_value=None)
    s.state_tracker = AsyncMock()
    s.campaign_loader = None
    s.npc_registry = None
    s.llm_manager = None
    s.chat_listener = None
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _client(state):
    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status,
                            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump())
    return TestClient(app, raise_server_exceptions=False)


# ------------------------------------------------------- world selection helpers

@pytest.mark.asyncio
async def test_select_world_reports_missing_world_and_failed_login():
    s = _state()
    s.foundry_client.execute_js = AsyncMock(return_value={"result": {}})
    assert "did not report an active Foundry world" in await cr._select_playwright_world("C", s)
    s = _state()
    s.foundry_client.is_connected = False
    s.foundry_client.connect = AsyncMock(return_value=False)
    msg = await cr._select_playwright_world("C", s)
    assert "bad password" in msg and "'C'" in msg


@pytest.mark.asyncio
async def test_select_world_links_first_world_and_matches_by_name_or_id():
    s = _state()
    with patch("campaign.obsidian_sync.get_campaign_world", return_value=None), \
         patch("campaign.obsidian_sync.link_world_to_campaign") as link:
        assert await cr._select_playwright_world("C", s) is None
    link.assert_called_once_with("C", "valenthal", "v1")
    with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "valenthal", "world_id": ""}):
        assert await cr._select_playwright_world("C", s) is None
    with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "other", "world_id": "v1"}):
        assert await cr._select_playwright_world("C", s) is None
    with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "other", "world_id": "zz"}):
        msg = await cr._select_playwright_world("C", s)
    assert "linked to 'other'" in msg


@pytest.mark.asyncio
async def test_select_campaign_world_without_a_session_manager():
    assert await cr._select_campaign_world("C", _state(foundry_client=None)) == "Foundry session manager is unavailable"


@pytest.mark.asyncio
async def test_attach_world_error_paths_and_success():
    world, err = await cr._attach_world(_state(foundry_client=None), campaign_name="C")
    assert world is None and err.error == "Foundry session manager is unavailable"

    s = _state()
    s.foundry_client.is_connected = False
    s.foundry_client.connect = AsyncMock(return_value=False)
    _, err = await cr._attach_world(s, campaign_name="C")
    assert err.error == "Could not log into Foundry: bad password"

    s.foundry_client.connect = AsyncMock(return_value=True)           # login works, world answers
    world, err = await cr._attach_world(s, campaign_name="C", foundry_world_name="v1")
    assert err is None and world == WORLD

    s.foundry_client.execute_js = AsyncMock(return_value={"result": {}})
    _, err = await cr._attach_world(s, campaign_name="C")
    assert "did not report an active world" in err.error

    s.foundry_client.execute_js = AsyncMock(return_value={"result": dict(WORLD)})
    _, err = await cr._attach_world(s, campaign_name="C", foundry_world_name="elsewhere")
    assert "not 'elsewhere'" in err.error


# ------------------------------------------------------------------------ build

def test_build_prompt_carries_every_user_input():
    s = _state()
    orch = MagicMock()
    orch.build_campaign = AsyncMock(return_value={"status": "complete"})
    body = {"name": "Krynn", "description": "Gloom", "theme": "Gothic", "seed_ideas": "a lich",
            "scale": "epic", "level_range": "5-10"}
    with patch.object(cr, "_attach_world", AsyncMock(return_value=(None, None))), \
         patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch):
        resp = _client(s).post("/api/campaign/build", json=body)
    assert resp.json()["status"] == "complete"
    kw = orch.build_campaign.await_args.kwargs
    for needle in ("'Krynn'", "Theme: Gloom", "Theme setting: Gothic", "Seed ideas from user: a lich",
                   "Campaign scale: epic", "Level range: 5-10"):
        assert needle in kw["prompt"]
    assert kw["level_range"] == "5-10"


# ---------------------------------------------------------------------- restart

def test_restart_without_active_session_or_connected_foundry_still_resets():
    s = _state()
    s.foundry_client.is_connected = True
    s.chat_listener = MagicMock(_running=False)
    orch = MagicMock()
    orch.teardown_campaign = AsyncMock(return_value={"ok": True})
    s.db.delete_campaign_history = AsyncMock(return_value=2)
    s.state_tracker.reset = AsyncMock()

    async def fake_deploy(name, st):
        st.foundry_client.is_connected = False        # Foundry drops during the redeploy
        return {"status": "deployed"}

    with patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch), \
         patch.object(cr, "_deploy_campaign_to_world", fake_deploy), \
         patch("api.routes.campaign.broadcast_state_update", new=AsyncMock()):
        resp = _client(s).post("/api/campaign/restart", json={"campaign_name": "C"})
    assert resp.json()["status"] == "restarted"
    s.db.close_session.assert_not_called()             # no active session to close
    s.state_tracker.reset.assert_awaited_once_with(campaign="C")
    assert s.chat_listener._running is False


# ------------------------------------------------------------------------ start

def _start(state, body=None, linked=None, broadcast=None):
    linked = {"world_name": "valenthal", "world_id": "v1"} if linked is None else linked
    with patch("campaign.obsidian_sync.get_campaign_world", return_value=linked or None), \
         patch("campaign.obsidian_sync.link_world_to_campaign") as link, \
         patch("api.routes.campaign.broadcast_state_update", new=broadcast or AsyncMock()), \
         patch("api.routes.campaign.spawn") as spawn:
        resp = _client(state).post("/api/campaign/start", json=body or {"campaign_name": "C"})
    return resp, link, spawn


def test_start_bare_engine_still_opens_a_session():
    s = _state()
    resp, link, _ = _start(s)
    assert resp.json()["status"] == "started" and resp.json()["session_id"]
    s.db.create_session.assert_awaited_once()
    link.assert_not_called()


def test_start_world_mismatch_is_409_and_creates_no_session():
    s = _state()
    s.foundry_client.execute_js = AsyncMock(return_value={"result": {"title": "valenthal", "id": "other"}})
    # first (select) check passes by name; second (session) check compares ids
    with patch("campaign.obsidian_sync.get_campaign_world", return_value={"world_name": "valenthal", "world_id": "v1"}):
        resp = _client(s).post("/api/campaign/start", json={"campaign_name": "C"})
    assert resp.status_code == 409 and "CAMPAIGN_WORLD_MISMATCH" in resp.text
    s.db.create_session.assert_not_called()


def test_start_world_link_lookup_failure_is_not_fatal():
    s = _state()
    calls = {"n": 0}

    async def js(script):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("relay hiccup")
        return {"result": dict(WORLD)}
    s.foundry_client.execute_js = AsyncMock(side_effect=js)
    resp, _, _ = _start(s)
    assert resp.json()["status"] == "started"


def test_start_module_refresh_and_prompt_invalidation():
    s = _state()
    s.llm_manager = MagicMock()
    s.foundry_client.scan_world = AsyncMock(return_value={"modules": [
        {"title": "Midi QOL", "active": True}, {"id": "x", "enabled": True},
        {"title": "Off", "active": False}, {"active": True}]})
    resp, _, _ = _start(s)
    assert resp.json()["status"] == "started"
    s.llm_manager.set_active_modules.assert_called_once_with(["Midi QOL", "x"])
    s.llm_manager.invalidate_system_prompt.assert_called_once()


def test_start_module_refresh_failure_is_tolerated():
    s = _state()
    s.llm_manager = MagicMock()
    s.foundry_client.scan_world = AsyncMock(side_effect=RuntimeError("scan"))
    resp, _, _ = _start(s)
    assert resp.json()["status"] == "started"
    s.llm_manager.set_active_modules.assert_not_called()
    s.llm_manager.invalidate_system_prompt.assert_called_once()


def test_start_unpause_failure_is_tolerated_and_listener_is_started():
    s = _state()
    real = s.foundry_client.execute_js

    async def js(script):
        if "togglePause" in script:
            raise RuntimeError("no")
        return await real(script)
    s.foundry_client.execute_js = AsyncMock(side_effect=js)
    listener = MagicMock(_running=False)
    for n in ("start", "_update_player_actors", "load_campaign_settlements", "sync_active_scene"):
        setattr(listener, n, AsyncMock())
    listener._process_proactive_action = MagicMock()
    s.chat_listener = listener
    resp, _, spawn = _start(s)
    assert resp.json()["status"] == "started"
    listener.start.assert_awaited_once()
    assert listener._running is True
    listener.load_campaign_settlements.assert_awaited_once_with("C")
    listener._reset_idle_timer.assert_called_once()
    spawn.assert_called_once()


def test_start_loader_without_registry_or_listener():
    s = _state()
    s.campaign_loader = MagicMock()
    s.campaign_loader.load = AsyncMock()
    resp, _, _ = _start(s)
    assert resp.json()["status"] == "started"
    s.campaign_loader.load.assert_awaited_once_with("C")
    s.campaign_loader.register_vault_npcs.assert_not_called()


def test_start_links_world_on_first_association():
    s = _state()
    resp, link, _ = _start(s, linked=False)
    assert resp.json()["status"] == "started"
    assert link.call_args_list[-1].args == ("C", "valenthal", "v1")


# ------------------------------------------------------------------- end/enrich

def test_end_session_without_llm_has_empty_summary():
    s = _state()
    s.db.get_active_session = AsyncMock(return_value="sid")
    s.state_tracker = MagicMock()
    s.state_tracker.state.campaign = "C"
    s.state_tracker.state.model_dump = MagicMock(return_value={"mode": "exploration"})
    with patch("api.routes.campaign.broadcast_state_update", new=AsyncMock()):
        resp = _client(s).post("/api/session/end", json={})
    body = resp.json()
    assert body["status"] == "ended" and body["campaign_name"] == "C"
    assert body["summary"] == ""                          # no LLM, no summary
    s.db.close_session.assert_awaited_once_with("sid")


def test_enrich_reloads_loader_without_llm_manager():
    s = _state()
    s.campaign_loader = MagicMock(current_campaign_name="C")
    s.campaign_loader.reload = AsyncMock()
    orch = MagicMock()
    orch.enrich_campaign = AsyncMock(return_value={"status": "ok", "sources": [{"name": "a"}]})
    with patch("campaign.orchestrator.CampaignOrchestrator", return_value=orch), \
         patch("api.routes.campaign.resolve_within_roots", return_value=__file__):
        resp = _client(s).post("/api/campaign/enrich", json={"campaign_name": "C", "source_path": __file__})
    assert resp.json()["reloaded"] is True
    s.campaign_loader.reload.assert_awaited_once()
