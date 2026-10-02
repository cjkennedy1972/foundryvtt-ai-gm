"""Behavioural coverage for api/routes/{camera,downtime,setup}.py and api/deps.py."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api import deps
from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import camera as camera_routes
from api.routes import downtime as downtime_routes
from api.routes import setup as setup_routes
from config import settings


def _client(state, *routers):
    app = FastAPI()
    for r in routers:
        app.include_router(r.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _h(request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump())

    return TestClient(app, raise_server_exceptions=False)


# ----------------------------------------------------------------- camera

def _cam_state(execute_js=None, **kw):
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    if execute_js is not None:
        s.foundry_client.execute_js = execute_js
    s.state_tracker = SimpleNamespace(state=SimpleNamespace(
        mode=kw.get("mode", "combat"),
        combat=SimpleNamespace(turn=kw.get("turn", 1), turn_order=kw.get("order", ["t1", "t2"]))))
    return s


class TestCameraJsResult:
    PAN = ("/api/camera/pan", {"x": 1, "y": 2})

    def test_foundry_error_payload_is_reported_not_swallowed(self):
        s = _cam_state(AsyncMock(return_value={"result": {"ok": False, "error": "no active scene"}}))
        r = _client(s, camera_routes).post(self.PAN[0], json=self.PAN[1])
        assert r.json() == {"ok": False, "error": "no active scene"}

    @pytest.mark.parametrize("res", [{"result": {"ok": False}}, {"result": None}, "weird", {}])
    def test_unusable_payload_has_an_error_message(self, res):
        s = _cam_state(AsyncMock(return_value=res))
        b = _client(s, camera_routes).post(self.PAN[0], json=self.PAN[1]).json()
        assert b["ok"] is False and b["error"]

    def test_relay_failure_returns_error_json_not_null(self):
        """The helper's contract is {ok, error}; an exception used to fall off the end and answer `null`."""
        s = _cam_state(AsyncMock(side_effect=TimeoutError("relay at http://internal:3010 timed out")))
        r = _client(s, camera_routes).post(self.PAN[0], json=self.PAN[1])
        assert r.status_code == 200
        b = r.json()
        assert b is not None and b["ok"] is False and b["error"]
        assert "internal" not in r.text

    def test_disconnected_is_503(self):
        s = _cam_state()
        s.foundry_client.is_connected = False
        assert _client(s, camera_routes).post(self.PAN[0], json=self.PAN[1]).status_code == 503

    @pytest.mark.parametrize("path,body", [
        ("/api/camera/pan", {"x": "NaN", "y": 1}),
        ("/api/camera/pan", {"x": 1, "y": 1, "duration": 60001}),
        ("/api/camera/zoom", {"scale": 0}),
        ("/api/camera/zoom", {"scale": 11}),
        ("/api/camera/pull-back", {"duration": -1}),
    ])
    def test_out_of_range_input_never_reaches_foundry(self, path, body):
        s = _cam_state()
        assert _client(s, camera_routes).post(path, json=body).status_code == 422
        s.foundry_client.execute_js.assert_not_awaited()

    def test_token_id_is_json_escaped_in_the_script(self):
        s = _cam_state(AsyncMock(return_value={"result": {"ok": True}}))
        evil = "x';alert(1);//\""
        _client(s, camera_routes).post("/api/camera/pan-to-token", json={"token_id": evil})
        js = s.foundry_client.execute_js.await_args.args[0]
        assert "const want=" + __import__("json").dumps(evil) + ";" in js


class TestIsPlayerTurn:
    def _get(self, s):
        return _client(s, camera_routes).get("/api/camera/is-player-turn")

    def test_no_tracker_is_500(self):
        s = _cam_state()
        s.state_tracker = None
        assert self._get(s).status_code == 500

    def test_outside_combat_and_no_turn_are_player_turns(self):
        assert self._get(_cam_state(mode="exploration")).json()["data"]["player_turn"] is True
        assert self._get(_cam_state(turn=0)).json()["data"]["player_turn"] is True
        assert self._get(_cam_state(order=[])).json()["data"]["player_turn"] is True

    def test_turn_counter_wraps_over_initiative_order(self):
        # turn 3 over two combatants is index 0 ("t1"), the player; turn 4 is the NPC.
        mapping = {"actor_uuids": {"Actor.pc": "Ann"}}
        tokens = [{"id": "t1", "actorUuid": "Actor.pc"}, {"id": "t2", "actorUuid": "Actor.orc"}]
        for turn, expected in ((1, True), (2, False), (3, True), (4, False)):
            s = _cam_state(turn=turn)
            s.foundry_client.get_scene_tokens.return_value = tokens
            s.foundry_client.get_player_actor_mapping.return_value = mapping
            assert self._get(s).json()["data"]["player_turn"] is expected, turn

    def test_bare_actor_id_matches_full_uuid(self):
        s = _cam_state(turn=1)
        s.foundry_client.get_scene_tokens.return_value = [{"id": "t1", "actorUuid": "pc"}]
        s.foundry_client.get_player_actor_mapping.return_value = {"actor_uuids": {"Actor.pc": "Ann"}}
        assert self._get(s).json()["data"]["player_turn"] is True

    def test_missing_token_or_actor_defaults_to_player_turn_and_foundry_down_is_503(self):
        s = _cam_state(turn=1)
        s.foundry_client.get_scene_tokens.return_value = []
        assert self._get(s).json()["data"]["player_turn"] is True
        s = _cam_state(turn=2)
        s.foundry_client.get_scene_tokens.return_value = [{"id": "t2"}]  # no actorUuid
        s.foundry_client.get_player_actor_mapping.return_value = {"actor_uuids": {}}
        assert self._get(s).json()["data"]["player_turn"] is False
        s = _cam_state(turn=1)
        s.foundry_client.is_connected = False
        assert self._get(s).status_code == 503

    def test_unexpected_failure_is_generic_500(self):
        s = _cam_state(turn=1)
        s.foundry_client.get_scene_tokens.side_effect = RuntimeError("/secret")
        r = self._get(s)
        assert r.status_code == 500 and "secret" not in r.text


# --------------------------------------------------------------- downtime

class TestDowntime:
    def _state(self):
        s = AppState()
        s.db = AsyncMock()
        s.db.get_active_session_info.return_value = {"campaign": "Live"}
        s.llm_manager = MagicMock()
        s.chat_listener = SimpleNamespace(downtime=MagicMock())
        return s

    def test_not_ready_without_db_or_llm(self):
        s = self._state()
        s.llm_manager = None
        r = _client(s, downtime_routes).post("/api/downtime", json={"player": "a", "action": "b"})
        assert r.status_code == 503 and r.json()["code"] == "NOT_READY"

    def test_missing_fields_are_422(self):
        assert _client(self._state(), downtime_routes).post("/api/downtime", json={"player": "a"}).status_code == 422

    def test_receipt_never_contains_the_outcome_and_campaign_defaults_to_active(self):
        s = self._state()
        s.chat_listener.downtime.resolve = AsyncMock(return_value={
            "campaign": "Live", "player": "Ann", "action": "track", "resolved": True,
            "stopped_reason": None, "outcome": "SECRET OUTCOME"})
        r = _client(s, downtime_routes).post("/api/downtime", json={"player": "Ann", "action": "track"})
        assert r.json() == {"status": "ok", "campaign": "Live", "player": "Ann", "action": "track",
                            "resolved": True, "stopped_reason": None}
        assert "SECRET" not in r.text
        s.chat_listener.downtime.resolve.assert_awaited_once_with("Live", "Ann", "track")

    def test_unresolved_turn_is_an_error_status_and_explicit_campaign_wins(self):
        s = self._state()
        s.chat_listener.downtime.resolve = AsyncMock(return_value={
            "campaign": "X", "player": "a", "action": "b", "resolved": False, "stopped_reason": "budget"})
        b = _client(s, downtime_routes).post(
            "/api/downtime", json={"player": "a", "action": "b", "campaign": " X "}).json()
        assert b["status"] == "error" and b["stopped_reason"] == "budget"
        assert s.chat_listener.downtime.resolve.await_args.args[0] == "X"
        s.db.get_active_session_info.assert_not_awaited()

    def test_campaign_falls_back_to_default_when_no_session(self, monkeypatch):
        monkeypatch.setattr(settings, "default_campaign", "Fallback")
        s = self._state()
        s.db.get_active_session_info.return_value = None
        s.chat_listener.downtime.pending = AsyncMock(return_value=[])
        b = _client(s, downtime_routes).get("/api/downtime/pending").json()
        assert b == {"campaign": "Fallback", "pending": []}

    def test_pending_lists_player_and_action_only(self):
        s = self._state()
        s.chat_listener.downtime.pending = AsyncMock(return_value=[
            {"player": "Ann", "action": "track", "outcome": "SECRET"}])
        r = _client(s, downtime_routes).get("/api/downtime/pending", params={"campaign": "C"})
        assert r.json()["pending"] == [{"player": "Ann", "action": "track"}] and "SECRET" not in r.text

    def test_pending_without_db(self):
        s = self._state()
        s.db = None
        assert _client(s, downtime_routes).get("/api/downtime/pending").json() == {"campaign": "", "pending": []}

    def test_resolver_is_standalone_when_no_live_listener(self):
        s = self._state()
        s.chat_listener = None
        with patch.object(downtime_routes, "DowntimeResolver") as cls, \
             patch.object(downtime_routes, "EventStore"), patch.object(downtime_routes, "ModelRouter"), \
             patch.object(downtime_routes, "RefereeAgent"):
            cls.return_value.pending = AsyncMock(return_value=[])
            _client(s, downtime_routes).get("/api/downtime/pending")
        cls.assert_called_once()


# ------------------------------------------------------------------ setup

class TestSetup:
    def _state(self):
        s = AppState()
        s.relay_manager = MagicMock()
        s.relay_manager.status.return_value = {"running": True}
        s.relay_manager.dashboard_url = "http://dash"
        return s

    def test_probe_without_key_reports_unhealthy_without_calling_out(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "llm_base_url", "http://llm.local/v1/")
        with patch("api.routes.setup.httpx.AsyncClient") as http:
            b = _client(self._state(), setup_routes).post("/api/setup/probe-llm").json()
        assert b["healthy"] is False and b["endpoint"] == "http://llm.local/v1"
        http.assert_not_called()

    def test_probe_sends_only_the_supplied_key_to_the_supplied_host(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", "stored-secret")
        calls = []

        class _HTTP:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None):
                calls.append((url, headers))
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"data": [{"id": "m1", "owned_by": "x"}, "junk", {"name": "n"}]})

        with patch("api.routes.setup.httpx.AsyncClient", _HTTP):
            r = _client(self._state(), setup_routes).post(
                "/api/setup/probe-llm", params={"base_url": "https://other.example/v1", "api_key": "mine"})
        assert calls == [("https://other.example/v1/models", {"Authorization": "Bearer mine"})]
        b = r.json()
        assert b["healthy"] is True and [m["id"] for m in b["models"]] == ["m1", ""]
        assert "stored-secret" not in r.text

    def test_probe_http_and_unexpected_errors_are_reported_unhealthy(self, monkeypatch):
        import httpx
        monkeypatch.setattr(settings, "llm_api_key", "k")
        for exc, expect in ((httpx.ConnectError("refused"), "HTTP error"), (ValueError("/secret"), "ValueError")):
            class _HTTP:
                def __init__(self, *a, **k): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return False
                async def get(self, *a, **k): raise exc
            with patch("api.routes.setup.httpx.AsyncClient", _HTTP):
                b = _client(self._state(), setup_routes).post("/api/setup/probe-llm").json()
            assert b["healthy"] is False and expect in b["message"] and "secret" not in b["message"]

    def test_scoped_key_provisioning(self, monkeypatch):
        c = lambda s: _client(s, setup_routes)  # noqa: E731
        s = self._state()
        s.relay_manager = None
        assert c(s).post("/api/setup/provision-relay-scoped-key").status_code == 503
        s = self._state()
        monkeypatch.setattr(settings, "relay_api_key", "")
        assert c(s).post("/api/setup/provision-relay-scoped-key").status_code == 400
        monkeypatch.setattr(settings, "relay_api_key", "master")
        s.relay_manager.ensure_rest_scoped_key = AsyncMock()
        monkeypatch.setattr(settings, "relay_scoped_key", "")
        r = c(s).post("/api/setup/provision-relay-scoped-key", params={"client_id": "c1"})
        assert r.status_code == 500 and r.json()["code"] == "SCOPED_KEY_FAILED"
        s.relay_manager.ensure_rest_scoped_key.assert_awaited_once_with(client_id="c1")
        monkeypatch.setattr(settings, "relay_scoped_key", "scoped")
        assert c(s).post("/api/setup/provision-relay-scoped-key").json()["key_set"] is True
        s.relay_manager.ensure_rest_scoped_key = AsyncMock(side_effect=RuntimeError("/secret"))
        r = c(s).post("/api/setup/provision-relay-scoped-key")
        assert r.status_code == 500 and r.json()["detail"] == "RuntimeError"

    def test_pairing_code_failure_and_missing_manager(self):
        s = self._state()
        s.relay_manager._load_credentials.side_effect = OSError("/secret")
        r = _client(s, setup_routes).get("/api/setup/pairing-code")
        assert r.status_code == 500 and "secret" not in r.text
        s.relay_manager = None
        assert _client(s, setup_routes).get("/api/setup/pairing-code").status_code == 503

    def test_start_wizard_failure_and_unmanaged_relay(self, monkeypatch):
        monkeypatch.setattr(settings, "relay_managed", True)
        s = self._state()
        s.relay_manager.status.return_value = {"running": False}
        s.relay_manager.start = AsyncMock(side_effect=RuntimeError("/secret"))
        r = _client(s, setup_routes).post("/api/setup/start-wizard")
        assert r.status_code == 500 and r.json()["detail"] == "RuntimeError"

    def test_write_env_failure_is_type_only(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        body = {"llm_api_key": "k", "llm_base_url": "http://x/v1", "model": "m",
                "campaign_vault_path": str(tmp_path), "ai_name": "Sage", "ai_tone": "calm"}
        with patch("api.routes.setup.os.open", side_effect=PermissionError("/secret")):
            r = _client(self._state(), setup_routes).post("/api/setup/write-env", json=body)
        assert r.status_code == 500 and "secret" not in r.text

    def test_write_env_is_owner_only_and_backs_up_the_old_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("OLD=1")
        body = {"llm_api_key": "k", "llm_base_url": "http://x/v1", "model": "m",
                "campaign_vault_path": str(tmp_path), "ai_name": "Sage", "ai_tone": "calm"}
        r = _client(self._state(), setup_routes).post("/api/setup/write-env", json=body)
        assert r.status_code == 200
        assert oct(os.stat(tmp_path / ".env").st_mode & 0o777) == "0o600"
        assert "LLM_API_KEY=k" in (tmp_path / ".env").read_text()
        backups = [p for p in os.listdir(tmp_path) if p.startswith(".env.bak.")]
        assert len(backups) == 1 and (tmp_path / backups[0]).read_text() == "OLD=1"

    @pytest.mark.parametrize("field", ["llm_api_key", "model", "ai_name"])
    def test_write_env_rejects_line_breaks_that_would_inject_settings(self, tmp_path, monkeypatch, field):
        monkeypatch.chdir(tmp_path)
        body = {"llm_api_key": "k", "llm_base_url": "http://x/v1", "model": "m",
                "campaign_vault_path": str(tmp_path), "ai_name": "Sage", "ai_tone": "calm"}
        body[field] = "x\nADMIN_HOST=0.0.0.0"
        r = _client(self._state(), setup_routes).post("/api/setup/write-env", json=body)
        assert r.status_code == 422 and not (tmp_path / ".env").exists()


# ------------------------------------------------------------------- deps

class TestDeps:
    @pytest.mark.asyncio
    async def test_broadcast_prunes_dead_sockets_and_reaches_live_ones(self, monkeypatch):
        live, dead = AsyncMock(), AsyncMock()
        dead.send_text.side_effect = RuntimeError("closed")
        monkeypatch.setattr(deps, "websocket_clients", [dead, live])
        await deps.broadcast_state_update({"type": "x"})
        live.send_text.assert_awaited_once_with('{"type": "x"}')
        assert deps.websocket_clients == [live]

    @pytest.mark.asyncio
    async def test_broadcast_tolerates_a_socket_pruned_concurrently(self, monkeypatch):
        dead = AsyncMock()
        clients = [dead]

        async def boom(_):
            clients.remove(dead)  # another broadcast pruned it first
            raise RuntimeError("closed")

        dead.send_text = boom
        monkeypatch.setattr(deps, "websocket_clients", clients)
        await deps.broadcast_state_update({"a": 1})  # must not raise ValueError
        assert clients == []

    def test_internal_error_hides_message_but_keeps_type(self):
        r = deps.internal_error("Reading failed", OSError("/etc/secret"), extra=1)
        body = __import__("json").loads(r.body)
        assert r.status_code == 500 and "OSError" in body["error"] and "secret" not in body["error"]
        assert body["extra"] == 1
