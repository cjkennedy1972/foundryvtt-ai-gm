"""HTTP-level checks for the LAN boundary (without starting the lifespan)."""

import httpx
import pytest

from api.deps import AppState
from config import settings
from main import app


@pytest.fixture(autouse=True)
def isolated_app_state(monkeypatch):
    app.state = AppState()
    monkeypatch.setattr(settings, "api_auth_required", True)
    monkeypatch.setattr(settings, "gm_api_token", "gm-http-secret")
    monkeypatch.setattr(settings, "player_api_token", "player-http-secret")
    yield


async def request(method: str, path: str, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers)


@pytest.mark.anyio
async def test_health_is_public_but_api_requires_auth():
    health = await request("GET", "/health")
    assert health.status_code == 200

    unauthenticated = await request("GET", "/api/rules/spell?name=fireball")
    assert unauthenticated.status_code == 401

    authenticated = await request(
        "GET", "/api/rules/spell?name=fireball", {"Authorization": "Bearer gm-http-secret"}
    )
    assert authenticated.status_code == 200


@pytest.mark.anyio
async def test_player_token_is_limited_to_safe_reads():
    allowed = await request(
        "GET", "/api/rules/spell?name=fireball", {"Authorization": "Bearer player-http-secret"}
    )
    assert allowed.status_code == 200

    forbidden = await request(
        "GET", "/api/status", {"Authorization": "Bearer player-http-secret"}
    )
    assert forbidden.status_code == 403
