"""HTTP-level checks for the LAN boundary (without starting the lifespan)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from api.deps import AppState
from campaign.vault import CampaignNotFound
from config import settings
from main import app


@pytest.fixture(autouse=True)
def isolated_app_state():
    app.state = AppState()
    yield


async def request(method: str, path: str, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers)


@pytest.mark.anyio
async def test_localhost_mode_needs_no_token():
    """With no ADMIN_TOKEN configured (loopback binding), everything is open."""
    health = await request("GET", "/health")
    assert health.status_code == 200

    api_response = await request("GET", "/api/rules/spell?name=fireball")
    assert api_response.status_code == 200

    readiness = await request("GET", "/ready")
    assert readiness.status_code == 503
    assert readiness.json()["detail"]["status"] == "not_ready"


@pytest.mark.anyio
async def test_admin_token_gates_api_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "lan-secret")

    missing = await request("GET", "/api/rules/spell?name=fireball")
    assert missing.status_code == 401

    wrong = await request(
        "GET", "/api/rules/spell?name=fireball", {"Authorization": "Bearer nope"}
    )
    assert wrong.status_code == 401

    ok = await request(
        "GET", "/api/rules/spell?name=fireball", {"Authorization": "Bearer lan-secret"}
    )
    assert ok.status_code == 200

    # Health (outside /api/) stays public for probes even with a token configured.
    assert (await request("GET", "/health")).status_code == 200


def test_admin_websocket_accepts_connections_without_token_configured():
    client = TestClient(app)
    try:
        with client.websocket_connect("/api/ws") as websocket:
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}
    finally:
        client.close()


def test_admin_websocket_requires_in_band_token_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "lan-secret")
    client = TestClient(app)
    try:
        with client.websocket_connect("/api/ws") as websocket:
            websocket.send_json({"type": "auth", "token": "lan-secret"})
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}

        # Wrong token → closed before joining the broadcast list.
        with pytest.raises(Exception):
            with client.websocket_connect("/api/ws") as websocket:
                websocket.send_json({"type": "auth", "token": "wrong"})
                websocket.receive_json()

        # Query-string tokens are never accepted as authentication.
        with pytest.raises(Exception):
            with client.websocket_connect("/api/ws?token=lan-secret") as websocket:
                websocket.send_json({"type": "ping"})
                websocket.receive_json()
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


@pytest.mark.anyio
async def test_oversized_body_rejected_via_content_length(monkeypatch):
    monkeypatch.setattr(settings, "max_request_body_bytes", 1024)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/chat/test", content=b"x" * 2048)
    assert response.status_code == 413


@pytest.mark.anyio
async def test_oversized_chunked_body_rejected(monkeypatch):
    """A streamed body carries no Content-Length — it must still be capped.

    httpx sends Transfer-Encoding: chunked for an iterator body, so this
    exercises the streaming path in protect_api_resources rather than the
    header check.
    """
    monkeypatch.setattr(settings, "max_request_body_bytes", 1024)

    async def oversized_stream():
        for _ in range(8):
            yield b"x" * 512

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/chat/test", content=oversized_stream())
    assert response.status_code == 413


@pytest.mark.anyio
async def test_chunked_body_within_limit_reaches_the_route(monkeypatch):
    """The buffered body must be replayable — the route still has to read it."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 1024)

    async def small_stream():
        yield b'{"message": "hello", "speaker": "Tester"}'

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/chat/test",
            content=small_stream(),
            headers={"Content-Type": "application/json"},
        )
    # 503 = engine not initialized (no lifespan in this test), which proves the
    # request body parsed and the handler ran; 413/422 would mean it did not.
    assert response.status_code == 503
