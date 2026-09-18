"""Admin panel auth: HTTP middleware, startup security, and WebSocket handshake."""

import os
import re
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from api import startup
from unittest.mock import patch, MagicMock, AsyncMock

PANEL_SRC = Path(__file__).resolve().parent.parent / "admin-panel" / "src"

# `fetch(` not preceded by a word character — matches a bare call but not
# safeFetch(/apiFetch(.
_BARE_FETCH = re.compile(r"(?<![\w.])fetch\s*\(")


def test_no_bare_fetch_outside_the_wrapper():
    """Source guard: every admin-panel request uses the fetch wrapper."""
    offenders = []
    for path in PANEL_SRC.rglob("*.js"):
        if path.name == "fetch.js":  # the wrapper itself is the one real caller
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("//", 1)[0]
            if _BARE_FETCH.search(code):
                offenders.append(f"{path.relative_to(PANEL_SRC)}:{lineno}")
    assert not offenders, (
        "these call fetch() directly and so send no Authorization header; "
        f"use safeFetch/apiFetch instead: {offenders}"
    )


class TestAdminAuthMiddleware:
    """Test /api/* authentication via middleware."""

    def test_api_endpoint_rejects_missing_token_when_admin_token_set(self):
        """POST /api/endpoint without Bearer token → 401."""
        from config import settings
        from main import app
        client = TestClient(app)

        # Mock settings.admin_token to simulate token requirement
        with patch.object(settings, 'admin_token', 'test-secret-token'):
            # Attempt to call /api/* without token
            response = client.get("/api/campaigns")
            assert response.status_code == 401
            assert "Authentication required" in response.json()["error"]

    def test_api_endpoint_rejects_wrong_token(self):
        """POST /api/endpoint with wrong Bearer token → 401."""
        from config import settings
        from main import app
        client = TestClient(app)

        with patch.object(settings, 'admin_token', 'correct-token'):
            # Attempt with wrong token
            response = client.get(
                "/api/campaigns",
                headers={"Authorization": "Bearer wrong-token"}
            )
            assert response.status_code == 401

    def test_api_endpoint_accepts_correct_token(self):
        """POST /api/endpoint with correct Bearer token → passes through."""
        from config import settings
        from main import app
        client = TestClient(app)

        with patch.object(settings, 'admin_token', 'test-token'):
            # Note: this will still fail downstream (no real campaign), but auth passes
            response = client.get(
                "/api/campaigns",
                headers={"Authorization": "Bearer test-token"}
            )
            # 200, 404, 500 are all valid — what matters is it's not 401
            assert response.status_code != 401


class _StartupReached(Exception):
    """Sentinel: the startup auth gate passed and lifespan moved on."""


class TestAdminStartupSecurity:
    """main.lifespan refuses to start an exposed API with no token.

    These drive lifespan itself. The previous version inlined the check
    against string literals and asserted on its own copy, never importing
    lifespan — so `is_loopback = True` could be pasted over main.py and the
    whole suite stayed green.
    """

    @pytest.mark.asyncio
    async def test_network_host_without_a_token_refuses_to_start(self, monkeypatch):
        import main

        monkeypatch.setattr(main.settings, "admin_host", "0.0.0.0")
        monkeypatch.setattr(main.settings, "admin_token", "")

        with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
            async with main.lifespan(MagicMock()):
                pass

    @pytest.mark.asyncio
    async def test_loopback_without_a_token_is_allowed(self, monkeypatch):
        """On loopback the OS is the auth boundary, so this must not raise."""
        import main

        monkeypatch.setattr(main.settings, "admin_host", "127.0.0.1")
        monkeypatch.setattr(main.settings, "admin_token", "")
        # RelayManager moved to api.startup when lifespan was decomposed.
        monkeypatch.setattr(startup, "RelayManager", MagicMock(side_effect=_StartupReached))

        with pytest.raises(_StartupReached):
            async with main.lifespan(MagicMock()):
                pass

    @pytest.mark.asyncio
    async def test_network_host_with_a_token_is_allowed(self, monkeypatch):
        import main

        monkeypatch.setattr(main.settings, "admin_host", "0.0.0.0")
        monkeypatch.setattr(main.settings, "admin_token", "a-real-token")
        # RelayManager moved to api.startup when lifespan was decomposed.
        monkeypatch.setattr(startup, "RelayManager", MagicMock(side_effect=_StartupReached))

        with pytest.raises(_StartupReached):
            async with main.lifespan(MagicMock()):
                pass


class TestWebSocketAuth:
    """The admin WebSocket handler, driven for real.

    This used to re-inline secrets.compare_digest in the test body and assert
    on that copy, so it passed whether or not the handler checked anything.
    """

    def test_wrong_token_closes_the_socket(self, monkeypatch):
        import json

        import main
        from fastapi import FastAPI
        from starlette.websockets import WebSocketDisconnect

        monkeypatch.setattr(main.settings, "admin_token", "correct-token")
        main.websocket_clients.clear()
        app = FastAPI()
        app.add_api_websocket_route("/api/ws", main.admin_websocket)

        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as caught:
                with client.websocket_connect("/api/ws") as ws:
                    ws.send_text(json.dumps({"type": "auth", "token": "wrong-token"}))
                    ws.receive_text()

        assert caught.value.code == 1008

    def test_correct_token_reaches_the_message_loop(self, monkeypatch):
        import json

        import main
        from fastapi import FastAPI

        monkeypatch.setattr(main.settings, "admin_token", "correct-token")
        main.websocket_clients.clear()
        app = FastAPI()
        app.add_api_websocket_route("/api/ws", main.admin_websocket)

        with TestClient(app) as client:
            with client.websocket_connect("/api/ws") as ws:
                ws.send_text(json.dumps({"type": "auth", "token": "correct-token"}))
                ws.send_text(json.dumps({"type": "ping"}))
                assert json.loads(ws.receive_text())["type"] == "pong"


class TestAdminRouterBehindMiddleware:
    """Test that API routes sit behind auth middleware."""

    def test_generic_api_route_requires_auth_token(self):
        """Verify /api/* routes reject requests without token."""
        from config import settings
        from main import app
        client = TestClient(app)

        with patch.object(settings, 'admin_token', 'required-token'):
            # Any /api/* route without the correct token should get 401
            response = client.get("/api/campaigns")
            assert response.status_code == 401, "/api/* should reject requests without token"
