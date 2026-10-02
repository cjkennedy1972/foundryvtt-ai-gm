"""Status-code behaviour of api/routes/session_control.py.

The 400 refusals (no listener, no session, no settlement system) are raised
inside `try: ... except Exception`, which swallows HTTPException and re-raises
it as a 500 named "HTTPException". The client was told the server broke when
it had in fact sent a request the engine could not act on.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.session_control import create_session_control_router


def _app_state(**kw):
    s = SimpleNamespace(db=AsyncMock(), chat_listener=None, campaign_memory=None, foundry_client=None)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _client(app_state):
    app = FastAPI()
    app.include_router(create_session_control_router(app_state))
    return TestClient(app, raise_server_exceptions=False)


def _listener(**kw):
    l = MagicMock()
    l._running = True
    l._run_proactive_action = AsyncMock()
    for k, v in kw.items():
        setattr(l, k, v)
    return l


class TestRefusalsAre400Not500:
    @pytest.mark.parametrize("method,path", [
        ("post", "/api/session/pause"),
        ("post", "/api/session/resume"),
        ("post", "/api/session/idle-beat"),
        ("get", "/api/session/settlements/town"),
    ])
    def test_missing_listener_is_400(self, method, path):
        r = getattr(_client(_app_state()), method)(path)
        assert r.status_code == 400

    def test_idle_beat_without_active_session_is_400_and_does_not_fire(self):
        listener = _listener()
        st = _app_state(chat_listener=listener)
        st.db.get_active_session.return_value = None
        r = _client(st).post("/api/session/idle-beat")
        assert r.status_code == 400 and r.json()["detail"] == "No active session"
        listener._run_proactive_action.assert_not_awaited()

    def test_settlement_query_without_world_clock_is_400(self):
        st = _app_state(chat_listener=SimpleNamespace())  # no _world_clock attribute
        assert _client(st).get("/api/session/settlements/town").status_code == 400


class TestHappyPaths:
    def test_pause_and_resume_flip_the_listener(self):
        listener = _listener()
        c = _client(_app_state(chat_listener=listener))
        assert c.post("/api/session/pause").json() == {"status": "paused"}
        assert listener._running is False
        assert c.post("/api/session/resume").json() == {"status": "running"}
        assert listener._running is True

    def test_idle_beat_fires_proactive_action(self):
        listener = _listener()
        st = _app_state(chat_listener=listener)
        st.db.get_active_session.return_value = "s1"
        assert _client(st).post("/api/session/idle-beat").json() == {"status": "idle-beat-triggered"}
        listener._run_proactive_action.assert_awaited_once_with(reason="gm-triggered-idle-beat")

    def test_idle_beat_failure_is_500_with_type_only(self):
        listener = _listener(_run_proactive_action=AsyncMock(side_effect=RuntimeError("/secret")))
        st = _app_state(chat_listener=listener)
        st.db.get_active_session.return_value = "s1"
        r = _client(st).post("/api/session/idle-beat")
        assert r.status_code == 500 and r.json()["detail"] == "RuntimeError"

    def test_status_failure_is_500_with_type_only(self):
        st = _app_state()
        st.db.get_active_session_info.side_effect = OSError("/secret")
        r = _client(st).get("/api/session/status")
        assert r.status_code == 500 and "secret" not in r.text

    def test_status_without_listener_defaults(self):
        st = _app_state()
        st.db.get_active_session_info.return_value = {"session_id": "s", "campaign": "C"}
        body = _client(st).get("/api/session/status").json()
        assert body["is_running"] is False and body["current_time"] is None and body["turn_count"] == 0


class TestSettlements:
    def _settlement(self, **kw):
        base = dict(id="t1", name="Town", region="North", population=100, npcs=[1, 2], buildings=[1])
        base.update(kw)
        return SimpleNamespace(**base)

    def test_list_empty_without_world_clock(self):
        assert _client(_app_state()).get("/api/session/settlements").json() == []
        st = _app_state(chat_listener=SimpleNamespace())
        assert _client(st).get("/api/session/settlements").json() == []

    def test_list_counts_npcs_and_buildings(self):
        clock = MagicMock()
        clock.list_settlements.return_value = [self._settlement()]
        st = _app_state(chat_listener=_listener(_world_clock=clock))
        assert _client(st).get("/api/session/settlements").json() == [{
            "id": "t1", "name": "Town", "region": "North", "population": 100,
            "npc_count": 2, "building_count": 1}]

    def test_list_failure_is_500(self):
        clock = MagicMock()
        clock.list_settlements.side_effect = RuntimeError("x")
        st = _app_state(chat_listener=_listener(_world_clock=clock))
        assert _client(st).get("/api/session/settlements").status_code == 500

    def test_query_uses_requested_or_current_time(self):
        clock = MagicMock()
        clock.query_location_at_time = AsyncMock(return_value={"tavern": ["n1"]})
        clock.get_current_time.return_value = "dusk"
        c = _client(_app_state(chat_listener=_listener(_world_clock=clock)))
        assert c.get("/api/session/settlements/t1").json() == {
            "settlement_id": "t1", "time_of_day": "dusk", "locations": {"tavern": ["n1"]}}
        assert c.get("/api/session/settlements/t1", params={"time_of_day": "noon"}).json()["time_of_day"] == "noon"
        clock.query_location_at_time.assert_awaited_with("t1", "noon")


class TestExportRecap:
    def _state(self, **kw):
        st = _app_state(
            campaign_memory=AsyncMock(), foundry_client=AsyncMock(), **kw)
        st.db.get_active_session_info.return_value = {"session_id": 7, "campaign": "C"}
        st.campaign_memory.session_notes.return_value = "Recap"
        st.foundry_client.execute_js.return_value = {"created": True, "journal_id": "J1"}
        return st

    def test_no_session_memory_or_foundry_or_recap_are_each_400(self):
        st = self._state()
        st.db.get_active_session_info.return_value = None
        assert _client(st).post("/api/session/export-recap").status_code == 400
        st = self._state()
        st.campaign_memory = None
        assert _client(st).post("/api/session/export-recap").status_code == 400
        st = self._state()
        st.foundry_client = None
        assert _client(st).post("/api/session/export-recap").status_code == 400
        st = self._state()
        st.campaign_memory.session_notes.return_value = ""
        r = _client(st).post("/api/session/export-recap")
        assert r.status_code == 400
        st.foundry_client.execute_js.assert_not_awaited()

    def test_creates_journal_with_json_escaped_content(self):
        st = self._state()
        st.campaign_memory.session_notes.return_value = 'He said "}); drop();//'
        r = _client(st).post("/api/session/export-recap")
        assert r.json()["journal_id"] == "J1"
        js = st.foundry_client.execute_js.await_args.args[0]
        assert json.dumps('He said "}); drop();//') in js

    def test_journal_creation_failure_is_500(self):
        st = self._state()
        st.foundry_client.execute_js.return_value = {"created": False}
        assert _client(st).post("/api/session/export-recap").status_code == 500

    def test_unexpected_error_is_500_with_type_only(self):
        st = self._state()
        st.campaign_memory.session_notes.side_effect = ValueError("/secret")
        r = _client(st).post("/api/session/export-recap")
        assert r.status_code == 500 and r.json()["detail"] == "ValueError"
