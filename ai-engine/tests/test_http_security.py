"""HTTP-level checks for the LAN boundary (without starting the lifespan)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from api.deps import AppState
from campaign.vault import CampaignNotFound
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

    readiness = await request("GET", "/ready")
    assert readiness.status_code == 503
    assert readiness.json()["detail"]["status"] == "not_ready"


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


def test_admin_websocket_authenticates_in_band():
    client = TestClient(app)
    try:
        with client.websocket_connect("/api/ws") as websocket:
            websocket.send_json({"type": "auth", "token": "gm-http-secret"})
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}
    finally:
        client.close()


def test_admin_websocket_rejects_query_string_token():
    client = TestClient(app)
    try:
        with pytest.raises(Exception):
            with client.websocket_connect("/api/ws?token=gm-http-secret") as websocket:
                websocket.receive_text()
    finally:
        client.close()


def test_campaign_errors_do_not_disclose_internal_names():
    from main import campaign_not_found_handler

    response = __import__("asyncio").run(
        campaign_not_found_handler(None, CampaignNotFound("/private/vault/secret-campaign"))
    )
    assert response.body == b'{"status":"error","error":"Campaign not found","code":"CAMPAIGN_NOT_FOUND","details":null}'


def test_api_rate_buckets_have_a_bounded_cleanup_threshold():
    from main import _API_RATE_MAX_CLIENTS, _api_rate

    assert _API_RATE_MAX_CLIENTS == 10_000
    _api_rate.clear()


def test_headless_credential_errors_are_classified_as_permanent():
    from relay_proc.manager import _is_permanent_headless_error

    assert _is_permanent_headless_error("configured Foundry user not found on this server")
    assert _is_permanent_headless_error("configured Foundry user is already logged in")
    assert not _is_permanent_headless_error("start browser: context canceled")


def test_headless_self_heal_skips_permanent_credential_failures():
    from relay_proc.manager import RelayManager
    import asyncio

    manager = RelayManager()
    manager._headless_blocked = True
    assert asyncio.run(manager.restart_headless_session()) is None
