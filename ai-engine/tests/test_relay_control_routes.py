#!/usr/bin/env python3
"""The relay controls in the admin panel: start, stop, restart, headless.

I claimed in #190 that admin-panel/src calls only /api/setup/* and /api/ws.
That was wrong. The panel builds every other URL from API_BASE plus a
relative path, so a grep for a literal "/api/" found only the setup calls.
It actually calls 35 endpoints, these among them, and api/routes/system.py
was at 26%.

relay_headless_start indexes status()["running"], the same KeyError shape
fixed in /start-wizard in #190 — a status dict without that key is a 500
rather than a refusal.

Run:
    cd ai-engine && python -m pytest tests/test_relay_control_routes.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.deps import AppState, get_app_state
from api.routes import system as system_routes
from config import settings


@pytest.fixture(autouse=True)
def managed(monkeypatch):
    monkeypatch.setattr(settings, "relay_managed", True)
    monkeypatch.setattr(settings, "relay_allow_headless", True)


@pytest.fixture
def state():
    s = AppState()
    s.relay_manager = MagicMock()
    s.relay_manager.status = MagicMock(return_value={"managed": True, "running": True})
    s.relay_manager.start = AsyncMock()
    s.relay_manager.stop = AsyncMock()
    s.relay_manager.restart = AsyncMock()
    s.relay_manager.ensure_api_key = AsyncMock()
    s.relay_manager._ensure_foundry_started = AsyncMock()
    s.relay_manager.ensure_headless_session = AsyncMock(return_value="client-abc")
    return s


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(system_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


# ── start / stop / restart ────────────────────────────────────────────────

@pytest.mark.parametrize("path,method_name", [
    ("/api/relay/start", "start"),
    ("/api/relay/stop", "stop"),
    ("/api/relay/restart", "restart"),
])
class TestLifecycle:
    def test_the_action_reaches_the_manager(self, client, state, path, method_name):
        resp = client.post(path)

        assert resp.status_code == 200
        getattr(state.relay_manager, method_name).assert_awaited_once()

    def test_no_relay_manager_is_refused(self, client, state, path, method_name):
        state.relay_manager = None

        assert client.post(path).status_code == 503

    def test_an_externally_run_relay_is_not_ours_to_control(
        self, client, state, monkeypatch, path, method_name
    ):
        monkeypatch.setattr(settings, "relay_managed", False)

        resp = client.post(path)

        assert resp.status_code == 400
        getattr(state.relay_manager, method_name).assert_not_awaited()

    def test_a_failure_is_reported_without_leaking_the_detail(
        self, client, state, path, method_name
    ):
        setattr(state.relay_manager, method_name,
                AsyncMock(side_effect=RuntimeError("/home/x/.relay/secret.db locked")))

        resp = client.post(path)

        assert resp.status_code == 500
        assert "secret.db" not in resp.text


def test_starting_also_provisions_the_master_key(client, state):
    """Without it the engine has no key to authenticate its WebSocket with."""
    client.post("/api/relay/start")

    state.relay_manager.ensure_api_key.assert_awaited_once()


# ── headless Foundry ──────────────────────────────────────────────────────

class TestHeadless:
    def test_a_session_is_launched_and_its_client_id_recorded(self, client, state):
        resp = client.post("/api/relay/headless/start")

        assert resp.status_code == 200
        assert resp.json()["client_id"] == "client-abc"
        assert settings.relay_headless_client_id == "client-abc"

    def test_foundry_is_started_before_the_browser_navigates_to_it(self, client, state):
        client.post("/api/relay/headless/start")

        state.relay_manager._ensure_foundry_started.assert_awaited_once()

    def test_it_is_refused_when_headless_is_disabled(self, client, state, monkeypatch):
        monkeypatch.setattr(settings, "relay_allow_headless", False)

        assert client.post("/api/relay/headless/start").status_code == 400

    def test_it_is_refused_while_the_relay_is_down(self, client, state):
        state.relay_manager.status = MagicMock(return_value={"running": False})

        assert client.post("/api/relay/headless/start").status_code == 409

    def test_a_status_without_the_running_key_is_a_refusal_not_a_crash(self, client, state):
        state.relay_manager.status = MagicMock(return_value={})

        resp = client.post("/api/relay/headless/start")

        assert resp.status_code == 409, f"got {resp.status_code}"

    def test_a_session_that_never_came_up_says_where_to_look(self, client, state):
        state.relay_manager.ensure_headless_session = AsyncMock(return_value=None)

        resp = client.post("/api/relay/headless/start")

        assert resp.status_code == 502
        assert "relay log" in resp.text.lower()


# ── status ────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_survives_a_bare_app_state(self, client, state):
        """Every field is read off a collaborator that may not exist yet."""
        resp = client.get("/api/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is False
        assert body["relay"]["running"] is True

    def test_relay_status_without_a_manager_is_still_answerable(self, client, state):
        state.relay_manager = None

        body = client.get("/api/relay/status").json()

        assert body["running"] is False
        assert body.get("error")
