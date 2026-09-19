#!/usr/bin/env python3
"""RelayManager._get_session_token, which /provision-relay-scoped-key runs on.

#192 made _load_credentials survive a file that does not parse, and
deliberately left a file that *does* parse alone — a dict without an api_key
is the normal pre-pairing state, and regenerating would change the password
the relay dashboard expects.

That leaves a gap it did not close: a dict missing email or password (hand
edited, or written by an older version) is returned as-is, and
_get_session_token indexes creds["password"] with only httpx.HTTPError
caught. The KeyError propagates through ensure_rest_scoped_key to
POST /api/setup/provision-relay-scoped-key as a 500.

The repair has to be surgical: fill what is missing and keep the api_key,
because that is how the world was paired.

Run:
    cd ai-engine && python -m pytest tests/test_relay_login_path.py -v
"""

import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from relay_proc.manager import RelayManager


@pytest.fixture
def manager(tmp_path):
    m = RelayManager()
    m.data_dir = tmp_path
    m._credentials_path = tmp_path / "aigm-credentials.json"
    return m


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def mock_relay(monkeypatch):
    """Replace httpx.AsyncClient with one backed by a scripted transport."""
    calls = []

    def install(handler):
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = _transport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return calls

    return install


# ── the credentials the login is built from ───────────────────────────────

def test_a_file_missing_the_password_is_repaired(manager):
    manager._credentials_path.write_text(json.dumps({"email": "gm@example.com"}))

    creds = manager._load_credentials()

    assert creds["email"] == "gm@example.com"
    assert creds.get("password")


def test_repairing_keeps_the_api_key_the_world_was_paired_under(manager):
    manager._credentials_path.write_text(
        json.dumps({"email": "gm@example.com", "api_key": "paired-key"})
    )

    creds = manager._load_credentials()

    assert creds["api_key"] == "paired-key"
    assert creds.get("password")


def test_a_repair_is_written_back(manager):
    manager._credentials_path.write_text(json.dumps({"email": "gm@example.com"}))

    first = manager._load_credentials()
    second = manager._load_credentials()

    assert first == second, "the password would change on every login"


def test_a_complete_file_is_not_rewritten(manager):
    saved = {"email": "gm@example.com", "password": "Secret123", "api_key": "k"}
    manager._credentials_path.write_text(json.dumps(saved))
    before = manager._credentials_path.stat().st_mtime_ns

    assert manager._load_credentials() == saved
    assert manager._credentials_path.stat().st_mtime_ns == before


# ── the login itself ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_successful_login_returns_the_session_token(manager, mock_relay):
    mock_relay(lambda request: httpx.Response(200, json={"sessionToken": "tok-abc"}))

    token = await manager._get_session_token(
        {"email": "gm@example.com", "password": "Secret123"}
    )

    assert token == "tok-abc"


@pytest.mark.asyncio
async def test_incomplete_credentials_do_not_raise(manager, mock_relay):
    """This reached /provision-relay-scoped-key as a KeyError 500."""
    mock_relay(lambda request: httpx.Response(200, json={"sessionToken": "tok-abc"}))

    token = await manager._get_session_token({"email": "gm@example.com"})

    assert token is None


@pytest.mark.asyncio
async def test_rejected_credentials_latch_so_the_relay_is_not_respawned(manager, mock_relay):
    """Restarting cannot fix a drifted password — the relay only hashes it
    when the admin row is first created."""
    mock_relay(lambda request: httpx.Response(401, json={"error": "bad login"}))

    token = await manager._get_session_token(
        {"email": "gm@example.com", "password": "wrong"}
    )

    assert token is None
    assert manager._headless_blocked is True


@pytest.mark.asyncio
async def test_a_server_error_does_not_latch(manager, mock_relay):
    """A 500 is transient; latching would stop a relay that recovers."""
    mock_relay(lambda request: httpx.Response(500, text="boom"))

    token = await manager._get_session_token(
        {"email": "gm@example.com", "password": "Secret123"}
    )

    assert token is None
    assert manager._headless_blocked is False


@pytest.mark.asyncio
async def test_a_relay_that_is_not_listening_does_not_raise(manager, mock_relay):
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    mock_relay(refuse)

    assert await manager._get_session_token(
        {"email": "gm@example.com", "password": "Secret123"}
    ) is None
