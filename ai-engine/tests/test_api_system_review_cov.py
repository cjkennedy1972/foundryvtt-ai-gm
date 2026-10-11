"""Behavioural coverage for api/routes/system.py (status, logs, probes, memory, ComfyUI)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import AppState, get_app_state
from api.routes import system as system_routes


@pytest.fixture
def state():
    return AppState()


def _client(state):
    app = FastAPI()
    app.include_router(system_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


class TestRootAndStatus:
    def test_root_redirects_to_admin_panel(self, state):
        r = _client(state).get("/", follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/admin/index.html"

    def test_status_with_nothing_initialised_uses_safe_defaults(self, state):
        body = _client(state).get("/api/status").json()
        assert body["connected"] is False and body["ai_running"] is False
        assert body["mode"] == "exploration" and body["session"] == 0

    def test_status_reads_live_components(self, state):
        state.foundry_client = SimpleNamespace(is_connected=True)
        state.chat_listener = SimpleNamespace(_running=True)
        state.llm_manager = SimpleNamespace(model="m-1", conversation_history=[1, 2, 3])
        state.state_tracker = SimpleNamespace(state=SimpleNamespace(
            campaign="Krynn", session_number=4, current_scene="Crypt", mode=SimpleNamespace(value="combat")))
        state.reinforcement_mgr = SimpleNamespace(_turn_count=7, _running=True)
        body = _client(state).get("/api/status").json()
        assert body["connected"] and body["ai_running"] and body["model"] == "m-1"
        assert (body["campaign"], body["session"], body["scene"], body["mode"]) == ("Krynn", 4, "Crypt", "combat")
        assert body["conversation_length"] == 3 and body["reinforcement_turns"] == 7


class _FakeHttpx:
    """Stands in for httpx.AsyncClient with scripted login/list responses."""

    def __init__(self, login, listing=None, raise_on_post=None):
        self.login, self.listing, self.raise_on_post = login, listing, raise_on_post
        self.posted = None

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        if self.raise_on_post:
            raise self.raise_on_post
        self.posted = (url, json)
        return self.login

    async def get(self, url):
        return self.listing


def _resp(status, body=None):
    return SimpleNamespace(status_code=status, json=lambda: body)


class TestHealthAndReady:
    def test_health_reports_each_component(self, state):
        state.db = object()
        state.chat_listener = SimpleNamespace(_running=False)
        c = _client(state).get("/health").json()["components"]
        assert c["state.db"] is True and c["state.chat_listener"] is False
        assert c["foundry"] is False and c["llm"] is False and c["state.combat_loop"] is False

    def test_ready_503_names_failing_component(self, state):
        state.db = object()
        r = _client(state).get("/ready")
        assert r.status_code == 503
        comps = r.json()["detail"]["components"]
        assert comps == {"database": True, "llm": False, "foundry": False}

    def test_ready_200_when_all_up(self, state):
        state.db, state.llm_manager = object(), object()
        state.foundry_client = SimpleNamespace(is_connected=True)
        assert _client(state).get("/ready").json()["status"] == "ready"


class TestContextAndMemory:
    def test_reinforcement_status_variants(self, state):
        c = _client(state)
        assert c.get("/api/context/reinforcement").json()["active"] is False
        state.reinforcement_mgr = SimpleNamespace(
            _running=True, _turn_count=3, _message_count=9, _last_reinforcement_time=5.0,
            _status="idle", _world_summary="ws", _get_anchor_facts=lambda: ["a"])
        body = c.get("/api/context/reinforcement").json()
        assert body["turns"] == 3 and body["anchors"] == ["a"] and body["world_summary"] == "ws"

    def test_reinforce_variants(self, state):
        c = _client(state)
        assert c.post("/api/context/reinforce").json()["code"] == "REINFORCEMENT_NOT_READY"
        state.reinforcement_mgr = MagicMock()
        state.reinforcement_mgr.reinforce_context = AsyncMock(return_value="abcd")
        assert c.post("/api/context/reinforce").json()["summary_length"] == 4
        state.reinforcement_mgr.reinforce_context = AsyncMock(return_value=None)
        assert c.post("/api/context/reinforce").json()["summary_length"] == 0
        state.reinforcement_mgr.reinforce_context = AsyncMock(side_effect=RuntimeError("/x"))
        r = c.post("/api/context/reinforce")
        assert r.status_code == 500 and r.json()["code"] == "REINFORCEMENT_FAILED" and "/x" not in r.text

    def test_summarize_needs_memory_and_active_session(self, state):
        c = _client(state)
        assert c.post("/api/context/summarize").status_code == 503
        state.campaign_memory = AsyncMock()
        state.db = AsyncMock()
        state.db.get_active_session_info.return_value = None
        assert c.post("/api/context/summarize").json()["code"] == "MEMORY_NOT_READY"
        state.campaign_memory.maybe_compact.assert_not_awaited()

    def test_summarize_forces_compaction_of_active_session(self, state):
        state.campaign_memory = AsyncMock()
        state.campaign_memory.maybe_compact.return_value = 2
        state.db = AsyncMock()
        state.db.get_active_session_info.return_value = {"campaign": "K", "session_id": 11}
        r = _client(state).post("/api/context/summarize")
        assert r.json() == {"status": "ok", "nodes_written": 2}
        state.campaign_memory.maybe_compact.assert_awaited_once_with(
            "K", 11, include_partial=True, force=True)

    def test_summarize_failure_hides_message(self, state):
        state.campaign_memory = AsyncMock()
        state.campaign_memory.maybe_compact.side_effect = RuntimeError("/db/path")
        state.db = AsyncMock()
        state.db.get_active_session_info.return_value = {"campaign": None, "session_id": 1}
        r = _client(state).post("/api/context/summarize")
        assert r.status_code == 500 and r.json()["code"] == "SUMMARIZATION_FAILED" and "/db" not in r.text

    def test_rebuild_memory(self, state):
        c = _client(state)
        assert c.post("/api/memory/rebuild", params={"campaign": "K"}).status_code == 503
        assert c.post("/api/memory/rebuild").status_code == 422
        state.campaign_memory = AsyncMock()
        state.campaign_memory.rebuild.return_value = 6
        r = c.post("/api/memory/rebuild", params={"campaign": "K"})
        assert r.json() == {"status": "ok", "campaign": "K", "nodes": 6}
        state.campaign_memory.rebuild.assert_awaited_once_with("K")

    def test_world_summary_updates_from_tracker_and_scene(self, state):
        c = _client(state)
        assert c.post("/api/context/world_summary").status_code == 503
        state.reinforcement_mgr = AsyncMock()
        state.state_tracker = MagicMock()
        state.state_tracker.state.model_dump.return_value = {"mode": "x"}
        state.scene_awareness = MagicMock()
        state.scene_awareness.get_context_summary.return_value = "dark room"
        assert c.post("/api/context/world_summary").json()["status"] == "ok"
        state.reinforcement_mgr.update_world_summary.assert_awaited_once_with({"mode": "x"}, "dark room")

    def test_world_summary_without_tracker_passes_empty(self, state):
        state.reinforcement_mgr = AsyncMock()
        _client(state).post("/api/context/world_summary")
        state.reinforcement_mgr.update_world_summary.assert_awaited_once_with({}, "")

    def test_world_summary_failure_code(self, state):
        state.reinforcement_mgr = AsyncMock()
        state.reinforcement_mgr.update_world_summary.side_effect = RuntimeError("x")
        r = _client(state).post("/api/context/world_summary")
        assert r.status_code == 500 and r.json()["code"] == "WORLD_SUMMARY_UPDATE_FAILED"


class TestComfyUI:
    def _mg(self, **kw):
        mg = MagicMock()
        mg.health_check = AsyncMock(**kw) if kw else AsyncMock(return_value=True)
        mg.get_models = AsyncMock(return_value=["sd15"])
        mg.close = AsyncMock()
        return mg

    def test_health_and_models_close_the_client(self, state):
        mg = self._mg()
        with patch("campaign.map_generator.MapGenerator", return_value=mg):
            c = _client(state)
            assert c.get("/api/comfyui/health").json()["healthy"] is True
            assert c.get("/api/comfyui/models").json() == {"models": ["sd15"]}
        assert mg.close.await_count == 2

    def test_unreachable_comfyui_is_503(self, state):
        mg = self._mg(side_effect=ConnectionError("http://internal:8188"))
        mg.get_models = AsyncMock(side_effect=ConnectionError("http://internal:8188"))
        with patch("campaign.map_generator.MapGenerator", return_value=mg):
            c = _client(state)
            h, m = c.get("/api/comfyui/health"), c.get("/api/comfyui/models")
        assert h.status_code == 503 and h.json()["code"] == "COMFYUI_HEALTH_CHECK_FAILED"
        assert m.status_code == 503 and m.json()["code"] == "COMFYUI_MODELS_FAILED"
        assert "internal" not in h.text + m.text
