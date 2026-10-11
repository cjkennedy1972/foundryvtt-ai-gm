"""Unexpected failures in api/routes/session_control.py surface as a 500 named for the error type."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.session_control import create_session_control_router


class _BrokenListener:
    @property
    def _running(self):
        return True

    @_running.setter
    def _running(self, v):
        raise OSError("flag store gone")


def _client(listener):
    st = SimpleNamespace(db=AsyncMock(), chat_listener=listener)
    app = FastAPI()
    app.include_router(create_session_control_router(st))
    return TestClient(app, raise_server_exceptions=False)


def test_pause_and_resume_report_unexpected_failure_as_500():
    c = _client(_BrokenListener())
    for path in ("/api/session/pause", "/api/session/resume"):
        r = c.post(path)
        assert r.status_code == 500 and r.json()["detail"] == "OSError"


def test_settlement_query_failure_is_500_and_success_defaults_time():
    listener = MagicMock()
    listener._world_clock.query_location_at_time = AsyncMock(side_effect=KeyError("town"))
    r = _client(listener).get("/api/session/settlements/town")
    assert r.status_code == 500 and r.json()["detail"] == "KeyError"

    listener._world_clock.query_location_at_time = AsyncMock(return_value={"inn": ["n1"]})
    listener._world_clock.get_current_time.return_value = "evening"
    r = _client(listener).get("/api/session/settlements/town")
    assert r.status_code == 200
    assert r.json() == {"settlement_id": "town", "time_of_day": "evening", "locations": {"inn": ["n1"]}}
    listener._world_clock.query_location_at_time.assert_awaited_with("town", None)
