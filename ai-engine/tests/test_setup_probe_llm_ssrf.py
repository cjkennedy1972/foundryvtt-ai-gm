"""probe-llm must never forward the stored LLM key to a caller-chosen host.

base_url and api_key are scalars with defaults, so FastAPI binds them as query
params: POST /api/setup/probe-llm?base_url=... needs no body, which makes it a
CORS simple request that any page the operator visits can fire. It used to send
settings.llm_api_key to that host in an Authorization header.
"""

import http.server
import json
import socketserver
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import AppState, get_app_state
from api.routes import setup as setup_routes
from config import settings


@pytest.fixture
def capture_server():
    """A stand-in for the attacker's host; records any Authorization header."""
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["authorization"] = self.headers.get("Authorization")
            body = json.dumps({"data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-stored-secret")
    app = FastAPI()
    app.include_router(setup_routes.router)
    app.dependency_overrides[get_app_state] = lambda: AppState()
    return TestClient(app)


def test_base_url_without_api_key_is_rejected(client, capture_server):
    """The exfiltration primitive: base_url alone used to borrow the stored key."""
    url, seen = capture_server

    resp = client.post(f"/api/setup/probe-llm?base_url={url}")

    assert resp.status_code == 400
    assert "api_key is required" in resp.json()["detail"]
    assert seen == {}, "no request should have been made at all"


def test_caller_supplied_host_only_ever_sees_the_caller_supplied_key(client, capture_server):
    url, seen = capture_server

    resp = client.post(f"/api/setup/probe-llm?base_url={url}&api_key=sk-user-typed")

    assert resp.status_code == 200
    assert resp.json()["healthy"] is True
    assert seen["authorization"] == "Bearer sk-user-typed"
    assert "sk-stored-secret" not in seen["authorization"]


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211",
        "http://user:pass@evil.tld",
    ],
)
def test_non_http_schemes_and_embedded_credentials_are_rejected(client, bad_url):
    resp = client.post(f"/api/setup/probe-llm?base_url={bad_url}&api_key=k")

    assert resp.status_code == 400


def test_configured_endpoint_still_uses_the_stored_key(client, capture_server, monkeypatch):
    """Probing the operator's own configured host is the legitimate default."""
    url, seen = capture_server
    monkeypatch.setattr(settings, "llm_base_url", url)

    resp = client.post("/api/setup/probe-llm")

    assert resp.status_code == 200
    assert seen["authorization"] == "Bearer sk-stored-secret"
