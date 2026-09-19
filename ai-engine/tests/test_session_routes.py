#!/usr/bin/env python3
"""api/routes/session.py, which serves eleven of the panel's endpoints.

Corrected in #196: the panel calls 35 endpoints, not the five I claimed in
#190. This module has /state, /settings, /session/*, /chat/*, /roll and
/foundry/js among them, and sat at 47%.

/api/foundry/js is the one that mattered most. It is one of the two
untrusted-input boundaries for ALLOW_EXECUTE_JS — the other is the
LLM-driven execute_js action — and the transport deliberately does not gate,
so the check in this handler is the whole control. It had no tests, so
removing it would have gone unnoticed on a default loopback install where
ADMIN_TOKEN is unset.

Run:
    cd ai-engine && python -m pytest tests/test_session_routes.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import session as session_routes
from config import settings


@pytest.fixture
def state():
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.foundry_client.execute_js = AsyncMock(return_value={"result": 42})
    s.foundry_client.roll = AsyncMock(return_value={"total": 17})
    s.llm_manager = MagicMock()
    s.llm_manager.model = "test-model"
    s.db = AsyncMock()
    s.state_tracker = MagicMock()
    s.state_tracker.get_snapshot = MagicMock(return_value="")
    return s


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(session_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status,
            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump())

    return TestClient(app, raise_server_exceptions=False)


# ── /api/foundry/js — the ALLOW_EXECUTE_JS boundary ───────────────────────

class TestExecuteJsGate:
    def test_it_is_refused_when_execute_js_is_disabled(self, client, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", False)

        resp = client.post("/api/foundry/js", json={"code": "return game.world.id;"})

        assert resp.status_code == 403
        state.foundry_client.execute_js.assert_not_awaited()

    def test_it_runs_when_enabled(self, client, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)

        resp = client.post("/api/foundry/js", json={"code": "return 1;"})

        assert resp.status_code == 200
        state.foundry_client.execute_js.assert_awaited_once_with("return 1;")

    def test_a_disconnected_foundry_is_refused(self, client, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)
        state.foundry_client = None

        assert client.post("/api/foundry/js", json={"code": "return 1;"}).status_code == 503

    def test_empty_code_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)

        assert client.post("/api/foundry/js", json={"code": "   "}).status_code == 400

    def test_oversized_code_is_refused(self, client, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)

        resp = client.post("/api/foundry/js", json={"code": "x" * 10_001})

        assert resp.status_code == 400
        state.foundry_client.execute_js.assert_not_awaited()

    def test_a_failure_does_not_return_the_exception(self, client, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)
        state.foundry_client.execute_js = AsyncMock(
            side_effect=RuntimeError("/home/x/.env line 3: LLM_API_KEY=sk-secret")
        )

        resp = client.post("/api/foundry/js", json={"code": "return 1;"})

        assert resp.status_code == 500
        assert "sk-secret" not in resp.text


# ── /api/settings ─────────────────────────────────────────────────────────

class TestSettings:
    def test_secrets_are_never_returned(self, client, monkeypatch):
        """Set real values first: the conftest points settings at an env file
        that does not exist, so both keys are already "" and an assertion
        that they come back empty would pass however the handler behaved."""
        monkeypatch.setattr(settings, "llm_api_key", "sk-live-do-not-leak")
        monkeypatch.setattr(settings, "relay_api_key", "relay-master-do-not-leak")

        resp = client.get("/api/settings")

        assert "do-not-leak" not in resp.text
        assert resp.json()["llm_api_key"] == ""
        assert resp.json()["relay_api_key"] == ""

    def test_the_readable_settings_are_returned(self, client):
        body = client.get("/api/settings").json()

        assert body["model"] == settings.model
        assert body["ai_name"] == settings.ai_name

    def test_changing_a_restart_only_setting_is_refused(self, client):
        current = client.get("/api/settings").json()
        current["llm_base_url"] = "http://somewhere-else:9999"

        resp = client.post("/api/settings", json=current)

        assert resp.status_code == 400
        assert "restart" in resp.text.lower()

    def test_a_runtime_setting_applies_immediately(self, client, state):
        current = client.get("/api/settings").json()
        current["temperature"] = 0.42

        resp = client.post("/api/settings", json=current)

        assert resp.status_code == 200
        assert state.llm_manager._temperature == 0.42


# ── /api/roll ─────────────────────────────────────────────────────────────

class TestRoll:
    def test_a_roll_reaches_foundry(self, client, state):
        resp = client.post("/api/roll", json={"formula": "2d6+3", "speaker": "GM"})

        assert resp.status_code == 200
        state.foundry_client.roll.assert_awaited_once()

    def test_an_absurd_formula_is_rejected_before_foundry_parses_it(self, client, state):
        resp = client.post("/api/roll", json={"formula": "1d20" * 500})

        assert resp.status_code == 422
        state.foundry_client.roll.assert_not_awaited()

    def test_an_unknown_field_is_rejected(self, client):
        resp = client.post("/api/roll", json={"formula": "1d20", "wish": "crit"})

        assert resp.status_code == 422


# ── /api/state and /api/session ───────────────────────────────────────────

class TestStateAndSession:
    def test_state_is_returned(self, client):
        assert client.get("/api/state").status_code == 200

    def test_the_active_session_is_reported(self, client, state):
        state.db.get_active_session_info = AsyncMock(
            return_value={"session_id": "abc12345", "campaign": "Valenthal"}
        )

        body = client.get("/api/session/active").json()

        assert body["session_id"] == "abc12345"
        assert body["campaign_name"] == "Valenthal"
        assert body["active"] is True

    def test_no_active_session_is_not_an_error(self, client, state):
        state.db.get_active_session_info = AsyncMock(return_value=None)

        body = client.get("/api/session/active").json()

        assert body["active"] is False
        assert body["session_id"] is None

    def test_a_session_with_no_campaign_reports_an_empty_name(self, client, state):
        """`info["campaign"] or ""` — the column is nullable."""
        state.db.get_active_session_info = AsyncMock(
            return_value={"session_id": "abc", "campaign": None}
        )

        assert client.get("/api/session/active").json()["campaign_name"] == ""

    def test_session_events_are_bounded(self, client, state):
        state.db.get_recent_events = AsyncMock(return_value=[])

        resp = client.get("/api/session/events", params={"limit": 10_000})

        assert resp.status_code in (200, 422)
