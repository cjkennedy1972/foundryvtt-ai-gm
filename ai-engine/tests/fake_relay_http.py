#!/usr/bin/env python3
"""The relay's REST surface, as the running one answers it.

Response shapes captured from the live go-relay on the amd64 docker host,
not invented:

    POST /auth/login          -> {"sessionToken", "email", "role", "id",
                                  "sessionExpiresAt", ...}
    GET  /auth/api-keys       -> {"keys": [{"id", "name", "key", "scopes",
                                            "enabled", "scopedClientId", ...}]}
    POST /auth/api-keys       -> the created key, same entry shape
    POST /auth/regenerate-key -> {"apiKey": "<64 hex>"}
    GET  /session             -> {"activeSessions": [{"clientId", ...}]}
    POST /session-handshake   -> {"token", "nonce", "publicKey", "foundryUrl",
                                  "username", "instanceId", "expires"}
    POST /start-session       -> {"clientId": "..."}

Errors are the relay's own: 401 {"error": "Invalid API key"} and
400 {"error": "No stored Foundry credential is configured"}.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSION_TOKEN = "s" * 64
MASTER_KEY = "m" * 64
SCOPED_KEY = "k" * 64


class FakeRelayHTTP:
    """`with FakeRelayHTTP(...) as relay:` → relay.url."""

    def __init__(
        self,
        *,
        login_ok: bool = True,
        existing_keys: list | None = None,
        has_foundry_credential: bool = True,
        active_sessions: list | None = None,
        start_session_status: int = 200,
        client_id: str = "qsl-integration-test",
    ):
        self.login_ok = login_ok
        self.keys = list(existing_keys or [])
        self.has_foundry_credential = has_foundry_credential
        self.active_sessions = list(active_sessions or [])
        self.start_session_status = start_session_status
        self.client_id = client_id
        self.calls: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.created: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    return json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return {}

            def do_POST(self):
                outer.calls.append(("POST", self.path))
                body = self._body()
                if self.path == "/auth/login":
                    if not outer.login_ok:
                        return self._json(401, {"error": "Invalid credentials"})
                    return self._json(200, {
                        "sessionToken": SESSION_TOKEN, "email": body.get("email"),
                        "id": 1, "role": "admin", "emailVerified": True,
                        "apiKeyRotationRequired": False, "requestsThisMonth": 0,
                        "createdAt": "2026-09-20T00:00:00Z",
                        "sessionExpiresAt": "2026-09-27T00:00:00Z",
                    })
                if self.path == "/auth/regenerate-key":
                    return self._json(200, {"apiKey": MASTER_KEY})
                if self.path == "/auth/api-keys":
                    entry = {
                        "id": len(outer.keys) + 1, "name": body.get("name"),
                        "key": SCOPED_KEY, "scopes": body.get("scopes", []),
                        "enabled": True, "isExpired": False,
                        "scopedClientId": body.get("scopedClientId"),
                        "scopedClientIds": [], "scopedUserId": None,
                        "scopedUserIds": [], "monthlyLimit": 0,
                        "requestsThisMonth": 0, "expiresAt": None,
                        "createdAt": "2026-09-20T00:00:00Z",
                        "updatedAt": "2026-09-20T00:00:00Z",
                    }
                    outer.created.append(entry)
                    outer.keys.append(entry)
                    return self._json(201, entry)
                if self.path == "/session-handshake":
                    if not outer.has_foundry_credential:
                        return self._json(400, {
                            "error": "No stored Foundry credential is configured"})
                    return self._json(200, {
                        "token": "handshake-token", "nonce": "abc",
                        "publicKey": "-----BEGIN PUBLIC KEY-----\nAA\n-----END PUBLIC KEY-----",
                        "foundryUrl": "http://foundry:30000", "username": "Gamemaster",
                        "instanceId": "local", "expires": "2026-09-20T01:00:00Z",
                    })
                if self.path == "/start-session":
                    if outer.start_session_status != 200:
                        return self._json(outer.start_session_status,
                                          {"error": "Headless launch failed"})
                    return self._json(200, {"clientId": outer.client_id})
                return self._json(404, {"error": "not found"})

            def do_GET(self):
                outer.calls.append(("GET", self.path))
                if self.path == "/auth/api-keys":
                    return self._json(200, {"keys": outer.keys})
                if self.path.startswith("/session"):
                    return self._json(200, {"activeSessions": outer.active_sessions})
                return self._json(404, {"error": "not found"})

            def do_DELETE(self):
                outer.calls.append(("DELETE", self.path))
                outer.deleted.append(self.path.rsplit("/", 1)[-1])
                return self._json(200, {"deleted": True})

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def paths(self, method=None):
        return [p for m, p in self.calls if method in (None, m)]
