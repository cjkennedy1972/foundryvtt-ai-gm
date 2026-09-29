#!/usr/bin/env python3
"""The setup wizard, which is the only route module the shipped UI calls.

admin-panel/src references exactly /api/setup/* and /api/ws — nothing else.
So these five endpoints are the whole first-run experience, and setup.py sat
at 64% with the endpoints the UI actually calls among the uncovered lines.

Two of them get an externally managed relay wrong. relay_managed is a mode
flag ("false = connect to an externally run relay", config.py:28), handled in
three places in api/routes/system.py, but:

  /status folds relay_managed into all(checks.values()), so with an external
  relay setup can never report complete — the wizard tells the user they are
  not finished, forever.

  /start-wizard tries to start a relay regardless, so it reaches for a
  subprocess this deployment does not own.

Run:
    cd ai-engine && python -m pytest tests/test_setup_wizard_routes.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.deps import AppState, get_app_state
from api.routes import setup as setup_routes
from config import settings


@pytest.fixture
def state():
    s = AppState()
    s.relay_manager = MagicMock()
    s.relay_manager.status = MagicMock(return_value={"running": True})
    s.relay_manager.start = AsyncMock()
    s.relay_manager.dashboard_url = "http://localhost:3010"
    s.relay_manager._load_credentials = MagicMock(
        return_value={"api_key": "pair-123", "email": "aigm@local.host"}
    )
    s.relay_manager.ensure_rest_scoped_key = AsyncMock()
    return s


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(setup_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def configured(monkeypatch):
    for name, value in (
        ("llm_api_key", "sk-test"), ("model", "test-model"),
        ("relay_api_key", "master-key"), ("relay_scoped_key", "scoped-key"),
        ("relay_managed", True),
    ):
        monkeypatch.setattr(settings, name, value)


# ── /status ───────────────────────────────────────────────────────────────

class TestStatus:
    def test_a_fully_configured_install_is_complete(self, client, configured):
        body = client.get("/api/setup/status").json()

        assert body["complete"] is True

    def test_a_missing_llm_key_is_incomplete(self, client, configured, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", "")

        body = client.get("/api/setup/status").json()

        assert body["complete"] is False
        assert body["checks"]["llm_api_key"] is False

    def test_an_external_relay_can_still_be_complete(self, client, configured, monkeypatch):
        """relay_managed is which mode you are in, not something to finish."""
        monkeypatch.setattr(settings, "relay_managed", False)

        body = client.get("/api/setup/status").json()

        assert body["complete"] is True, body

    def test_a_paired_managed_relay_is_complete_before_the_relay_starts(self, client, state, configured, monkeypatch):
        """The relay is deferred until campaign start, so at boot its key is
        only in the stored credentials. A finished install was being sent
        back through the wizard."""
        monkeypatch.setattr(settings, "relay_api_key", "")
        monkeypatch.setattr(settings, "relay_scoped_key", "")   # provisioned per connection
        state.relay_manager.has_stored_api_key = MagicMock(return_value=True)

        body = client.get("/api/setup/status").json()

        assert body["complete"] is True, body
        assert body["checks"]["relay_scoped_key"] is False     # still reported

    def test_an_unpaired_managed_relay_is_incomplete(self, client, state, configured, monkeypatch):
        monkeypatch.setattr(settings, "relay_api_key", "")
        state.relay_manager.has_stored_api_key = MagicMock(return_value=False)

        assert client.get("/api/setup/status").json()["complete"] is False

    def test_an_external_relay_needs_its_key_in_the_env(self, client, state, configured, monkeypatch):
        monkeypatch.setattr(settings, "relay_managed", False)
        monkeypatch.setattr(settings, "relay_api_key", "")
        state.relay_manager.has_stored_api_key = MagicMock(return_value=True)  # not ours to use

        assert client.get("/api/setup/status").json()["complete"] is False

    def test_the_mode_is_still_reported(self, client, configured, monkeypatch):
        monkeypatch.setattr(settings, "relay_managed", False)

        body = client.get("/api/setup/status").json()

        assert body["relay_managed"] is False


# ── /start-wizard ─────────────────────────────────────────────────────────

class TestStartWizard:
    def test_a_stopped_managed_relay_is_started(self, client, state, configured):
        state.relay_manager.status = MagicMock(return_value={"running": False})

        resp = client.post("/api/setup/start-wizard")

        assert resp.status_code == 200
        state.relay_manager.start.assert_awaited_once()

    def test_a_running_relay_is_left_alone(self, client, state, configured):
        resp = client.post("/api/setup/start-wizard")

        assert resp.status_code == 200
        state.relay_manager.start.assert_not_awaited()

    def test_an_external_relay_is_not_started_for_us(self, client, state, configured, monkeypatch):
        """We do not own that process."""
        monkeypatch.setattr(settings, "relay_managed", False)
        state.relay_manager.status = MagicMock(return_value={"running": False})

        resp = client.post("/api/setup/start-wizard")

        assert resp.status_code == 200
        state.relay_manager.start.assert_not_awaited()

    def test_no_relay_manager_is_a_clear_refusal(self, client, state, configured):
        """The sibling endpoints answer 503 for this; it answered 500."""
        state.relay_manager = None

        resp = client.post("/api/setup/start-wizard")

        assert resp.status_code == 503

    def test_a_status_without_the_running_key_does_not_crash(self, client, state, configured):
        state.relay_manager.status = MagicMock(return_value={})

        resp = client.post("/api/setup/start-wizard")

        assert resp.status_code == 200


# ── /pairing-code ─────────────────────────────────────────────────────────

class TestPairingCode:
    def test_the_code_and_instructions_are_returned(self, client):
        body = client.get("/api/setup/pairing-code").json()

        assert body["code"] == "pair-123"
        assert "localhost:3010" in body["instructions"]
        assert body["foundry_module_field"] == "aigm-config.pairingCode"

    def test_no_relay_manager_is_a_clear_refusal(self, client, state):
        state.relay_manager = None

        assert client.get("/api/setup/pairing-code").status_code == 503

    def test_unreadable_credentials_do_not_leak_the_reason(self, client, state):
        state.relay_manager._load_credentials = MagicMock(
            side_effect=PermissionError("/home/x/.relay/creds.json denied")
        )

        resp = client.get("/api/setup/pairing-code")

        assert resp.status_code == 500
        assert "creds.json" not in resp.text


# ── /provision-relay-scoped-key ───────────────────────────────────────────

class TestScopedKey:
    def test_provisioning_reports_the_key_is_set(self, client, configured):
        body = client.post("/api/setup/provision-relay-scoped-key").json()

        assert body["status"] == "ok"
        assert body["key_set"] is True

    def test_provisioning_before_the_master_key_is_refused(self, client, configured, monkeypatch):
        monkeypatch.setattr(settings, "relay_api_key", "")

        resp = client.post("/api/setup/provision-relay-scoped-key")

        assert resp.status_code == 400

    def test_a_silent_failure_to_create_the_key_is_reported(self, client, configured, monkeypatch):
        monkeypatch.setattr(settings, "relay_scoped_key", "")

        resp = client.post("/api/setup/provision-relay-scoped-key")

        assert resp.status_code == 500
        assert "SCOPED_KEY_FAILED" in resp.text


# ── stored key check and panel caching ─────────────────────────────────────

def test_checking_for_a_stored_key_never_creates_the_credentials_file(tmp_path, monkeypatch):
    from relay_proc.manager import RelayManager
    monkeypatch.setattr(settings, "relay_data_dir", str(tmp_path))
    manager = RelayManager()
    assert manager.has_stored_api_key() is False
    assert not (tmp_path / "aigm-credentials.json").exists()

    (tmp_path / "aigm-credentials.json").write_text('{"email": "a", "password": "b", "api_key": "k"}')
    assert manager.has_stored_api_key() is True


def test_the_panel_html_is_revalidated_but_bundles_cache(tmp_path):
    from api.static_files import PanelStaticFiles
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc.js").write_text("x")
    app = FastAPI()
    app.mount("/admin", PanelStaticFiles(directory=str(tmp_path), html=True))
    client = TestClient(app)
    assert client.get("/admin/").headers.get("cache-control") == "no-cache"
    assert client.get("/admin/index.html").headers.get("cache-control") == "no-cache"
    assert "cache-control" not in client.get("/admin/assets/index-abc.js").headers
