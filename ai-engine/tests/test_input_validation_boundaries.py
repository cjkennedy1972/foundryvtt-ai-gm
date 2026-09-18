"""The two endpoints that took unvalidated bodies.

POST /api/setup/write-env interpolated raw dict values into `KEY=value` lines,
so a newline injected further settings that the engine reads on next start.
POST /api/roll was the last of 129 endpoints reading a raw Request body, so
`formula` reached Foundry's dice parser with no length bound.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from actions.schemas import MAX_FORMULA_LEN
from api.deps import AppState, get_app_state
from api.routes import session as session_routes
from api.routes import setup as setup_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    app.include_router(setup_routes.router)
    app.include_router(session_routes.router)
    state = AppState()
    state.foundry_client = AsyncMock()
    state.foundry_client.roll.return_value = {"total": 14}
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app), state, tmp_path


@pytest.mark.parametrize("injected", [
    "x\nALLOW_EXECUTE_JS=true",
    "x\r\nADMIN_HOST=0.0.0.0",
    "x\x00ADMIN_TOKEN=",
])
def test_env_values_cannot_inject_extra_settings(client, injected):
    api, _, tmp_path = client

    resp = api.post("/api/setup/write-env", json={"model": injected})

    assert resp.status_code == 422
    assert not (tmp_path / ".env").exists()


def test_env_rejects_unknown_keys(client):
    api, _, _ = client

    resp = api.post("/api/setup/write-env", json={"model": "m", "ALLOW_EXECUTE_JS": "true"})

    assert resp.status_code == 422


def test_env_writes_ordinary_values(client):
    api, _, tmp_path = client

    resp = api.post("/api/setup/write-env", json={"model": "qwen3", "ai_name": "Sage"})

    assert resp.status_code == 200
    written = (tmp_path / ".env").read_text()
    assert "MODEL=qwen3" in written
    assert "ALLOW_EXECUTE_JS" not in written


def test_roll_formula_is_length_bounded(client):
    api, state, _ = client

    resp = api.post("/api/roll", json={"formula": "1d20+" * MAX_FORMULA_LEN})

    assert resp.status_code == 422
    state.foundry_client.roll.assert_not_awaited()


def test_roll_passes_a_normal_formula_through(client):
    api, state, _ = client

    resp = api.post("/api/roll", json={"formula": "2d6+3", "speaker": "Aria"})

    assert resp.status_code == 200
    state.foundry_client.roll.assert_awaited_once()
    assert state.foundry_client.roll.await_args.args[0] == "2d6+3"
