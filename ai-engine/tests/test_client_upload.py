#!/usr/bin/env python3
"""upload_file, against a real HTTP server.

45 of foundry/client.py's uncovered lines were here. It runs a synchronous
httpx.Client inside asyncio.to_thread, so a patched-out httpx proves nothing
about the path that actually runs; these drive a real socket.

The success shape is the live relay's, captured from an upload into a running
Foundry v13 world:

    {"type": "upload-file-result", "requestId": "upload-file_1789873929229",
     "success": true, "path": "probe/probe.png"}

That upload also showed the endpoint needs a key carrying file:write.
_get_or_create_scoped_key mints a session:manage key and
ensure_rest_scoped_key mints the file:write one; sending the former gets a
bare 403.

Run:
    cd ai-engine && python -m pytest tests/test_client_upload.py -v
"""

import asyncio
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from foundry.client import FoundryClient

LIVE_SHAPE = {
    "type": "upload-file-result",
    "requestId": "upload-file_1789873929229",
    "success": True,
    "path": "probe/probe.png",
}


class _Relay:
    """A stand-in for the relay's POST /upload."""

    def __init__(self, statuses=(200,), body=None):
        self.statuses = list(statuses) or [200]
        self.body = body if body is not None else LIVE_SHAPE
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": payload,
                })
                # The last status sticks. Defaulting to 200 once the list ran
                # out meant an unexpected extra request quietly succeeded, so
                # "did it raise?" could answer no for the wrong reason — which
                # is how this passed on 3.11 and failed on 3.13.
                status = (outer.statuses.pop(0) if len(outer.statuses) > 1
                          else outer.statuses[0])
                data = json.dumps(outer.body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

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


@pytest.fixture
def no_backoff(monkeypatch):
    """The retry waits 1s then 2s. The schedule is not what these assert."""
    real_sleep = asyncio.sleep

    async def instant(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", instant)


@pytest.fixture
def relay_settings(monkeypatch):
    def apply(url, scoped="scoped-key", master="master-key"):
        monkeypatch.setattr(settings, "relay_url", url)
        monkeypatch.setattr(settings, "relay_scoped_key", scoped)
        monkeypatch.setattr(settings, "relay_api_key", master)
    return apply


def _upload(**kw):
    c = FoundryClient()
    c.api_key = "master-key"
    return asyncio.run(c.upload_file(b"\x89PNG-bytes", "maps", "crypt.png", **kw))


# ── the happy path ────────────────────────────────────────────────────────

def test_a_successful_upload_returns_the_relays_saved_path(relay_settings):
    with _Relay() as relay:
        relay_settings(relay.url)

        out = _upload()

        assert out["path"] == "probe/probe.png"
        assert out["success"] is True


def test_the_file_is_sent_as_a_base64_data_url(relay_settings):
    with _Relay() as relay:
        relay_settings(relay.url)

        _upload(mime_type="image/webp")

        body = relay.requests[0]["body"]
        assert body["fileData"].startswith("data:image/webp;base64,")
        encoded = body["fileData"].split(",", 1)[1]
        assert base64.b64decode(encoded) == b"\x89PNG-bytes"


def test_the_path_filename_and_flags_are_sent(relay_settings):
    with _Relay() as relay:
        relay_settings(relay.url)

        _upload(source="data", overwrite=False)

        body = relay.requests[0]["body"]
        assert body["path"] == "maps"
        assert body["filename"] == "crypt.png"
        assert body["source"] == "data"
        assert body["overwrite"] is False


def test_it_posts_to_the_upload_endpoint(relay_settings):
    with _Relay() as relay:
        relay_settings(relay.url + "/")

        _upload()

        assert relay.requests[0]["path"] == "/upload"


# ── which key ─────────────────────────────────────────────────────────────

def test_the_scoped_key_is_used_when_there_is_one(relay_settings):
    """REST needs the file:write scoped key; the master key is WebSocket-only
    and the live relay answers 403."""
    with _Relay() as relay:
        relay_settings(relay.url, scoped="scoped-key")

        _upload()

        assert relay.requests[0]["headers"]["x-api-key"] == "scoped-key"


def test_the_master_key_is_the_fallback(relay_settings):
    with _Relay() as relay:
        relay_settings(relay.url, scoped="")

        _upload()

        assert relay.requests[0]["headers"]["x-api-key"] == "master-key"


# ── failure handling ──────────────────────────────────────────────────────

@pytest.mark.usefixtures("no_backoff")
def test_a_408_is_retried(relay_settings):
    """The relay answers 408 while it waits on Foundry; the upload is not
    lost, it is tried again."""
    with _Relay(statuses=(408, 200)) as relay:
        relay_settings(relay.url)

        out = _upload()

        assert out["success"] is True
        assert len(relay.requests) == 2


@pytest.mark.usefixtures("no_backoff")
def test_a_408_that_never_clears_eventually_raises(relay_settings):
    with _Relay(statuses=(408,)) as relay:
        relay_settings(relay.url)

        with pytest.raises(Exception):
            _upload()

        assert len(relay.requests) == 3, "three attempts, then give up"


def test_a_403_is_not_retried(relay_settings):
    """A missing scope will not fix itself, and retrying hides it."""
    with _Relay(statuses=(403,)) as relay:
        relay_settings(relay.url)

        with pytest.raises(Exception):
            _upload()

        assert len(relay.requests) == 1


def test_a_500_is_not_retried(relay_settings):
    with _Relay(statuses=(500,)) as relay:
        relay_settings(relay.url)

        with pytest.raises(Exception):
            _upload()

        assert len(relay.requests) == 1
