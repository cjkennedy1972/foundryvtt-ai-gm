"""Edge-case coverage for main.py (review pass): the fail-closed admin gate in
lifespan, the request-protection middleware, error handlers, audio serving,
the admin websocket's failure paths and the __main__ entry point."""

import json
import runpy
import socket
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import main
from api import startup
from api.deps import ApiError, websocket_clients
from campaign.vault import CampaignNotFound
from config import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "admin_host", "127.0.0.1")
    monkeypatch.setattr(settings, "max_request_body_bytes", 100)
    monkeypatch.setattr(settings, "api_requests_per_minute", 1000)
    main._api_rate.clear()
    main._admin_ws_rate.clear()
    websocket_clients.clear()
    yield
    main._api_rate.clear()
    main._admin_ws_rate.clear()
    websocket_clients.clear()


# --- lifespan: the loopback gate, allowed and denied ----------------------------

def _stub_builders(monkeypatch, calls):
    for name in ("build_persistence", "build_context", "build_foundry", "build_chat", "shutdown"):
        monkeypatch.setattr(startup, name, AsyncMock(side_effect=lambda *a, _n=name, **k: calls.append(_n)))
    for name in ("build_tts", "deploy_bundled_modules", "build_llm", "build_gameplay"):
        monkeypatch.setattr(startup, name, MagicMock(side_effect=lambda *a, _n=name, **k: calls.append(_n)))


@pytest.mark.asyncio
async def test_lifespan_refuses_network_bind_without_token_before_building_anything(monkeypatch):
    calls = []
    _stub_builders(monkeypatch, calls)
    monkeypatch.setattr(settings, "admin_host", "0.0.0.0")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        async with main.lifespan(MagicMock()):
            pass
    assert calls == []  # nothing was constructed, nothing needs tearing down


@pytest.mark.asyncio
@pytest.mark.parametrize("host,token", [("127.0.0.1", ""), ("localhost", ""), ("::1", ""), ("0.0.0.0", "s3cret")])
async def test_lifespan_allows_loopback_or_tokened_network_and_builds_in_order(monkeypatch, host, token):
    calls = []
    _stub_builders(monkeypatch, calls)
    monkeypatch.setattr(settings, "admin_host", host)
    monkeypatch.setattr(settings, "admin_token", token)
    app = MagicMock()
    async with main.lifespan(app):
        assert calls == ["build_persistence", "build_context", "build_tts", "deploy_bundled_modules",
                         "build_llm", "build_foundry", "build_gameplay", "build_chat"]
        app.include_router.assert_called_once()
    assert calls[-1] == "shutdown"


@pytest.mark.asyncio
async def test_lifespan_callbacks_broadcast_to_the_admin_panel(monkeypatch):
    captured = {}
    _stub_builders(monkeypatch, [])
    monkeypatch.setattr(startup, "build_gameplay", MagicMock(side_effect=lambda st, cb: captured.update(update=cb)))
    monkeypatch.setattr(startup, "build_chat", AsyncMock(side_effect=lambda st, cb: captured.update(notify=cb)))
    sent = AsyncMock()
    monkeypatch.setattr(main, "broadcast_state_update", sent)
    async with main.lifespan(MagicMock()):
        await captured["update"]({"type": "x"})
        await captured["notify"]([{"ok": True}])
    assert [c.args[0] for c in sent.await_args_list] == [
        {"type": "x"}, {"type": "actions_executed", "actions": [{"ok": True}]}]


# --- protect_api_resources ----------------------------------------------------------

def _protected_app():
    app = FastAPI()
    app.middleware("http")(main.protect_api_resources)

    @app.post("/api/echo")
    async def echo(request: Request):
        return {"body": (await request.body()).decode()}

    @app.get("/open")
    async def open_route():
        return {"ok": True}

    return app


async def _call(method, path, client=("9.9.9.9", 1), **kw):
    transport = httpx.ASGITransport(app=_protected_app(), client=client)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, **kw)


@pytest.mark.asyncio
async def test_content_length_garbage_or_oversize_is_413_and_valid_passes():
    assert (await _call("POST", "/api/echo", content=b"x" * 101)).status_code == 413
    assert (await _call("POST", "/api/echo", content=b"x" * 100)).json() == {"body": "x" * 100}
    r = await _call("POST", "/api/echo", content=b"hi", headers={"content-length": "not-a-number"})
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_chunked_body_is_capped_and_replayed_to_the_route():
    async def gen(parts):
        for p in parts:
            yield p

    ok = await _call("POST", "/api/echo", content=gen([b"hel", b"lo"]))
    assert ok.status_code == 200 and ok.json() == {"body": "hello"}  # route still reads what was buffered
    big = await _call("POST", "/api/echo", content=gen([b"a" * 60, b"b" * 60]))
    assert big.status_code == 413


@pytest.mark.asyncio
async def test_token_check_and_non_api_paths_bypass(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "tok")
    assert (await _call("POST", "/api/echo", content=b"x")).status_code == 401
    assert (await _call("POST", "/api/echo", content=b"x", headers={"Authorization": "Bearer bad"})).status_code == 401
    ok = await _call("POST", "/api/echo", content=b"x", headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200
    assert (await _call("GET", "/open")).status_code == 200  # only /api/* is guarded


@pytest.mark.asyncio
async def test_rate_limit_is_per_client_and_window_slides(monkeypatch):
    monkeypatch.setattr(settings, "api_requests_per_minute", 2)
    for _ in range(2):
        assert (await _call("POST", "/api/echo", content=b"x")).status_code == 200
    assert (await _call("POST", "/api/echo", content=b"x")).status_code == 429
    assert (await _call("POST", "/api/echo", content=b"x", client=("8.8.8.8", 1))).status_code == 200
    # old entries age out of the 60s window
    main._api_rate["9.9.9.9"] = [t - 61 for t in main._api_rate["9.9.9.9"]]
    assert (await _call("POST", "/api/echo", content=b"x")).status_code == 200


@pytest.mark.asyncio
async def test_rate_map_stays_bounded_evicting_least_recently_active(monkeypatch):
    import time
    monkeypatch.setattr(main, "_API_RATE_MAX_CLIENTS", 3)
    now = time.time()
    for i in range(5):  # all active within the window, so only recency eviction can bound it
        main._api_rate[f"10.0.0.{i}"] = [now - 50 + i]
    assert (await _call("POST", "/api/echo", content=b"x")).status_code == 200
    assert len(main._api_rate) <= 4 and "10.0.0.0" not in main._api_rate and "10.0.0.4" in main._api_rate
    assert "9.9.9.9" in main._api_rate


# --- exception handlers / audio ------------------------------------------------------

@pytest.mark.asyncio
async def test_error_handlers_render_the_error_envelope():
    r = await main.api_error_handler(None, ApiError("nope", code="TEAPOT", status=418))
    assert r.status_code == 418
    body = json.loads(r.body)
    assert (body["status"], body["error"], body["code"]) == ("error", "nope", "TEAPOT")
    r = await main.campaign_not_found_handler(None, CampaignNotFound("secret/path/detail"))
    assert r.status_code == 404 and b"secret" not in r.body  # internal detail not leaked
    assert json.loads(r.body)["code"] == "CAMPAIGN_NOT_FOUND"


@pytest.mark.asyncio
async def test_serve_audio_only_serves_files_directly_inside_the_audio_dir(tmp_path, monkeypatch):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "ok.wav").write_bytes(b"RIFF")
    (tmp_path / "secret.txt").write_text("s")
    (audio / "sub").mkdir()
    monkeypatch.setattr(main, "_tts_audio_dir", audio)
    resp = await main.serve_audio("ok.wav")
    assert Path(resp.path) == (audio / "ok.wav").resolve()
    for bad in ("../secret.txt", "sub", "missing.wav", "sub/x.wav", "/etc/passwd"):
        with pytest.raises(HTTPException) as ei:
            await main.serve_audio(bad)
        assert ei.value.status_code == 404
    link = audio / "link.wav"
    link.symlink_to(tmp_path / "secret.txt")  # symlink escaping the dir is refused
    with pytest.raises(HTTPException):
        await main.serve_audio("link.wav")


# --- admin websocket ------------------------------------------------------------------

@pytest.fixture
def ws_client():
    app = FastAPI()
    app.add_api_websocket_route("/api/ws", main.admin_websocket)
    app.state.chat_listener = AsyncMock()
    app.state.chat_listener._reset_idle_timer = MagicMock()
    app.state.foundry_client = AsyncMock()
    return TestClient(app)


def _recv_close(ws):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as ei:
        ws.receive_text()
    return ei.value.code


def test_ws_refuses_when_at_connection_cap(ws_client, monkeypatch):
    monkeypatch.setattr(settings, "ws_max_connections", 0)
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as ei:
        with ws_client.websocket_connect("/api/ws"):
            pass
    assert ei.value.code == 1013


@pytest.mark.parametrize("first", ["{bad", "[1]", '"str"', json.dumps({"type": "ping"}),
                                   json.dumps({"type": "auth", "token": "wrong"}),
                                   json.dumps({"type": "auth"})])
def test_ws_with_token_rejects_bad_first_frames(ws_client, monkeypatch, first):
    monkeypatch.setattr(settings, "admin_token", "tok")
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text(first)
        assert _recv_close(ws) == 1008
    assert websocket_clients == []


def test_ws_with_token_accepts_valid_auth_and_registers_client(ws_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "tok")
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "tok"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text()) == {"type": "pong"}
        assert len(websocket_clients) == 1
    assert websocket_clients == []  # unregistered on disconnect


def test_ws_oversize_message_closes_with_1009(ws_client, monkeypatch):
    monkeypatch.setattr(settings, "ws_max_message_bytes", 10)
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text("x" * 50)
        assert _recv_close(ws) == 1009


def test_ws_pause_resume_survive_foundry_js_failure(ws_client):
    import time
    ws_client.app.state.foundry_client.execute_js.side_effect = RuntimeError("relay down")
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"type": "pause"}))
        time.sleep(0.25)
        ws.send_text(json.dumps({"type": "resume"}))
        time.sleep(0.25)
        ws.send_text(json.dumps({"type": "ping"}))
        frames = [json.loads(ws.receive_text())["type"] for _ in range(3)]
        assert frames == ["ai_paused", "ai_resumed", "pong"]  # broadcasts still went out; still connected
    ws_client.app.state.chat_listener.pause.assert_awaited_once()
    ws_client.app.state.chat_listener.resume.assert_awaited_once()
    ws_client.app.state.chat_listener._reset_idle_timer.assert_called_once()


def test_ws_roll_command_forwards_and_defaults(ws_client):
    import time
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"type": "roll_command"}))
        time.sleep(0.25)
        ws.send_text(json.dumps({"type": "roll_command", "formula": "2d6", "speaker": "Orc", "flavor": "hit"}))
        time.sleep(0.25)
        ws.send_text(json.dumps({"type": "ping"}))
        ws.receive_text()
    calls = ws_client.app.state.foundry_client.roll.await_args_list
    assert calls[0].args == ("1d20",) and calls[0].kwargs == {"speaker": "GM", "flavor": ""}
    assert calls[1].args == ("2d6",) and calls[1].kwargs == {"speaker": "Orc", "flavor": "hit"}


def test_ws_handler_error_cleans_up_registration(ws_client):
    import time
    ws_client.app.state.foundry_client.roll.side_effect = RuntimeError("boom")
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"type": "roll_command"}))
        time.sleep(0.4)  # the handler dies; a receive here would hang the TestClient portal
    assert websocket_clients == [] and main._admin_ws_rate == {}


# --- __main__ entry point -----------------------------------------------------------------

def test_entry_point_exits_when_port_is_taken(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Busy:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def bind(self, addr):
            raise OSError("in use")

    ran = MagicMock()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: Busy())
    monkeypatch.setitem(sys.modules, "uvicorn", MagicMock(run=ran))
    with pytest.raises(SystemExit) as ei:
        runpy.run_path(main.__file__, run_name="__main__")
    assert ei.value.code == 1
    ran.assert_not_called()


def test_entry_point_binds_configured_loopback_address(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bound = []

    class Free:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def bind(self, addr):
            bound.append(addr)

    uv = MagicMock()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: Free())
    monkeypatch.setitem(sys.modules, "uvicorn", uv)
    runpy.run_path(main.__file__, run_name="__main__")
    assert bound == [(settings.admin_host, settings.admin_port)]
    kw = uv.run.call_args.kwargs
    assert uv.run.call_args.args == ("main:app",)
    assert kw["host"] == settings.admin_host and kw["port"] == settings.admin_port and kw["reload"] is False
