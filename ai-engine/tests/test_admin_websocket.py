#!/usr/bin/env python3
"""The admin panel's WebSocket, the other half of what the shipped UI uses.

admin-panel/src calls /api/setup/* (covered in #190) and /api/ws. This is
/api/ws, and nothing exercised it.

The auth handshake carefully guards json.JSONDecodeError, TypeError and
AttributeError on the first frame. The message loop that follows guards none
of them: a malformed frame, a JSON scalar where a dict was expected, or a
command arriving before the chat listener exists all fell to the outer
`except Exception`, which closes the socket. The panel reconnects with
exponential backoff, so each one blinded it for a growing interval.

Run:
    cd ai-engine && python -m pytest tests/test_admin_websocket.py -v
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from api.deps import websocket_clients
from config import settings


@pytest.fixture(autouse=True)
def _no_auth_and_clean_registry(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "")
    websocket_clients.clear()
    main._admin_ws_rate.clear()
    yield
    websocket_clients.clear()
    main._admin_ws_rate.clear()


@pytest.fixture
def app_state():
    s = MagicMock()
    s.chat_listener = AsyncMock()
    s.foundry_client = AsyncMock()
    return s


@pytest.fixture
def client(app_state):
    app = FastAPI()
    app.add_api_websocket_route("/api/ws", main.admin_websocket)
    app.state.chat_listener = app_state.chat_listener
    app.state.foundry_client = app_state.foundry_client
    return TestClient(app)


def _send(ws, payload, wait=0.25):
    """Send and pace past the 0.2s per-connection rate limit."""
    import time
    time.sleep(wait)
    ws.send_text(payload if isinstance(payload, str) else json.dumps(payload))


def _reply(ws, timeout=3.0):
    """Receive one frame, or fail.

    A handler that dies mid-loop leaves TestClient's portal waiting rather
    than raising WebSocketDisconnect, so a plain receive_text() hangs the
    suite instead of failing it. Bound it.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _Timeout

    # Not a `with` block: __exit__ calls shutdown(wait=True), which blocks on
    # the very thread that is stuck, so the guard would hang the suite it
    # exists to protect.
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(ws.receive_text)
    try:
        return json.loads(future.result(timeout=timeout))
    except _Timeout:
        pytest.fail("no reply — the handler closed the connection")
    finally:
        pool.shutdown(wait=False)


# ── the loop survives bad input ───────────────────────────────────────────

def test_a_malformed_frame_does_not_close_the_socket(client):
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, "{not json", wait=0)
        assert _reply(ws)["type"] == "error"

        _send(ws, {"type": "ping"})
        assert _reply(ws)["type"] == "pong"


def test_a_json_scalar_does_not_close_the_socket(client):
    """"5" parses fine and then .get() raises AttributeError."""
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, "5", wait=0)
        assert _reply(ws)["type"] == "error"

        _send(ws, {"type": "ping"})
        assert _reply(ws)["type"] == "pong"


def test_a_command_before_the_listener_exists_does_not_close_the_socket(client):
    with client.websocket_connect("/api/ws") as ws:
        ws.app_state_removed = True
        client.app.state.chat_listener = None
        _send(ws, {"type": "pause"}, wait=0)
        assert _reply(ws)["type"] == "error"

        client.app.state.chat_listener = AsyncMock()
        _send(ws, {"type": "ping"})
        assert _reply(ws)["type"] == "pong"


def test_an_unknown_command_is_ignored_quietly(client):
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, {"type": "fly_to_the_moon"}, wait=0)
        _send(ws, {"type": "ping"})
        assert _reply(ws)["type"] == "pong"


# ── the commands the panel sends ──────────────────────────────────────────

def test_ping_is_answered(client):
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, {"type": "ping"}, wait=0)
        assert _reply(ws)["type"] == "pong"


def test_pause_pauses_the_listener_and_the_world(client):
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, {"type": "pause"}, wait=0)
        _reply(ws)   # the ai_paused broadcast

    client.app.state.chat_listener.pause.assert_awaited_once()
    client.app.state.foundry_client.execute_js.assert_awaited()


def test_resume_resumes_and_resets_the_idle_timer(client):
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, {"type": "resume"}, wait=0)
        _reply(ws)

    client.app.state.chat_listener.resume.assert_awaited_once()
    client.app.state.chat_listener._reset_idle_timer.assert_called_once()


def test_a_roll_command_reaches_foundry(client):
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, {"type": "roll_command", "formula": "2d6", "speaker": "GM"}, wait=0)
        _send(ws, {"type": "ping"})
        _reply(ws)

    client.app.state.foundry_client.roll.assert_awaited_once()
    assert client.app.state.foundry_client.roll.await_args.args[0] == "2d6"


# ── limits and bookkeeping ────────────────────────────────────────────────

def test_an_oversized_frame_closes_the_connection(client, monkeypatch):
    monkeypatch.setattr(settings, "ws_max_message_bytes", 32)
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws") as ws:
            _send(ws, {"type": "ping", "pad": "x" * 200}, wait=0)
            ws.receive_text()


def test_rapid_frames_are_rate_limited_not_dropped(client):
    with client.websocket_connect("/api/ws") as ws:
        _send(ws, {"type": "ping"}, wait=0)
        assert _reply(ws)["type"] == "pong"
        _send(ws, {"type": "ping"}, wait=0)
        assert _reply(ws)["type"] == "rate_limited"


def test_a_disconnect_removes_the_client_from_the_registry(client):
    with client.websocket_connect("/api/ws"):
        assert len(websocket_clients) == 1

    assert websocket_clients == []
    assert main._admin_ws_rate == {}
