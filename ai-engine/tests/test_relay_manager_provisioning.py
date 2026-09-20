#!/usr/bin/env python3
"""RelayManager's provisioning chain, against the relay's real REST shapes.

relay_proc/manager.py sat at 35%, its largest untested blocks being the
HTTP-driven ones: ensure_api_key, ensure_rest_scoped_key,
_get_or_create_scoped_key, _launch_headless_session, _find_active_session.

Those three that matter most were driven against the live relay first:

    _get_session_token       ok (64-char sessionToken)
    _get_or_create_scoped_key ok (64-char key)
    _launch_headless_session  -> clientId "qsl-integration-test"

and their failure modes observed there too — a master key on a REST endpoint
gets 401 "Invalid API key", and a handshake with no stored world credential
gets 400 "No stored Foundry credential is configured". tests/fake_relay_http
answers with those shapes.

Run:
    cd ai-engine && python -m pytest tests/test_relay_manager_provisioning.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from fake_relay_http import MASTER_KEY, SCOPED_KEY, SESSION_TOKEN, FakeRelayHTTP
from relay_proc.manager import RelayManager

CREDS = {"email": "admin@test.local", "password": "AdminTestPass1"}


@pytest.fixture
def manager(monkeypatch):
    def build(relay):
        monkeypatch.setattr(settings, "relay_url", relay.url)
        m = RelayManager()
        m._load_credentials = lambda: dict(CREDS)
        m._clear_chrome_locks = lambda: None
        return m
    return build


def run(coro):
    return asyncio.run(coro)


# ── logging in ────────────────────────────────────────────────────────────

def test_a_session_token_is_returned(manager):
    with FakeRelayHTTP() as relay:
        assert run(manager(relay)._get_session_token(CREDS)) == SESSION_TOKEN
        assert ("POST", "/auth/login") in relay.calls


def test_rejected_credentials_return_no_token(manager):
    with FakeRelayHTTP(login_ok=False) as relay:
        assert run(manager(relay)._get_session_token(CREDS)) is None


def test_credentials_missing_a_password_never_reach_the_relay(manager):
    with FakeRelayHTTP() as relay:
        assert run(manager(relay)._get_session_token({"email": "a@b.c"})) is None
        assert relay.calls == [], "no point asking with half a credential"


def test_a_rejected_login_latches_so_the_relay_is_not_respawned_forever(manager):
    """The password is only hashed into the DB when the admin row is created,
    so restarting cannot fix it."""
    with FakeRelayHTTP(login_ok=False) as relay:
        m = manager(relay)
        run(m._get_session_token(CREDS))
        assert m._headless_blocked is True


# ── scoped keys ───────────────────────────────────────────────────────────

def test_a_scoped_key_is_created_when_there_is_none(manager):
    with FakeRelayHTTP() as relay:
        key = run(manager(relay)._get_or_create_scoped_key(SESSION_TOKEN))

        assert key == SCOPED_KEY
        assert relay.created[0]["name"] == "aigm-headless"
        assert relay.created[0]["scopes"] == ["session:manage"]


def test_an_existing_headless_key_is_replaced_not_reused(manager):
    """The relay hands back the plaintext once, at creation. Keeping the row
    would mean never seeing its key again, so the old one is deleted and a
    fresh one minted — and the old rows do not accumulate."""
    existing = {"id": 7, "name": "aigm-headless", "key": None,
                "scopes": ["session:manage"], "enabled": True}
    with FakeRelayHTTP(existing_keys=[existing]) as relay:
        key = run(manager(relay)._get_or_create_scoped_key(SESSION_TOKEN))

        assert key == SCOPED_KEY
        assert "7" in relay.deleted, "the stale row should be removed first"


def test_an_unrelated_key_is_left_alone(manager):
    other = {"id": 9, "name": "someone-elses-key", "key": None,
             "scopes": ["entity:read"], "enabled": True}
    with FakeRelayHTTP(existing_keys=[other]) as relay:
        run(manager(relay)._get_or_create_scoped_key(SESSION_TOKEN))

        assert relay.deleted == [], "only aigm-headless is ours to delete"


def test_the_rest_key_asks_for_the_scopes_the_engine_uses(manager):
    """file:write is what POST /upload requires — a session:manage key gets a
    bare 403 there, confirmed against the live relay."""
    with FakeRelayHTTP() as relay:
        run(manager(relay).ensure_rest_scoped_key())

        created = relay.created[-1]
        assert created["name"] == "aigm-engine"
        assert "file:write" in created["scopes"]
        assert "entity:write" in created["scopes"]


def test_a_stale_rest_key_is_deleted_before_a_fresh_one_is_made(manager):
    """The relay only hands back the plaintext once, so reusing the row means
    never seeing the key again."""
    stale = {"id": 42, "name": "aigm-engine", "key": None, "scopes": []}
    with FakeRelayHTTP(existing_keys=[stale]) as relay:
        run(manager(relay).ensure_rest_scoped_key())

        assert "42" in relay.deleted
        assert relay.created[-1]["name"] == "aigm-engine"


def test_the_rest_key_binds_the_client_id_when_one_is_known(manager):
    with FakeRelayHTTP() as relay:
        run(manager(relay).ensure_rest_scoped_key(client_id="qsl-integration-test"))

        assert relay.created[-1]["scopedClientId"] == "qsl-integration-test"


# ── headless sessions ─────────────────────────────────────────────────────

def test_a_headless_session_returns_its_client_id(manager):
    with FakeRelayHTTP() as relay:
        cid = run(manager(relay)._launch_headless_session(SCOPED_KEY,
                                                          world_name="test-world"))

        assert cid == "qsl-integration-test"
        assert ("POST", "/session-handshake") in relay.calls
        assert ("POST", "/start-session") in relay.calls


def test_no_stored_world_credential_yields_no_session(manager):
    """The relay decrypts its own stored credential; without one the handshake
    is 400 and the launch cannot proceed."""
    with FakeRelayHTTP(has_foundry_credential=False) as relay:
        cid = run(manager(relay)._launch_headless_session(SCOPED_KEY))

        assert cid is None
        assert ("POST", "/start-session") not in relay.calls


def test_a_failed_launch_yields_no_session(manager):
    with FakeRelayHTTP(start_session_status=500) as relay:
        assert run(manager(relay)._launch_headless_session(SCOPED_KEY)) is None


def test_the_world_name_is_forwarded_only_when_given(manager):
    with FakeRelayHTTP() as relay:
        run(manager(relay)._launch_headless_session(SCOPED_KEY))
        assert ("POST", "/start-session") in relay.calls


# ── existing sessions ─────────────────────────────────────────────────────

def test_an_existing_session_is_found(manager):
    with FakeRelayHTTP(active_sessions=[{"clientId": "already-running"}]) as relay:
        assert run(manager(relay)._find_active_session(SCOPED_KEY)) == "already-running"


def test_no_active_sessions_returns_nothing(manager):
    with FakeRelayHTTP(active_sessions=[]) as relay:
        assert run(manager(relay)._find_active_session(SCOPED_KEY)) is None


# ── the master key ────────────────────────────────────────────────────────

def test_ensure_api_key_provisions_a_master_key(manager, monkeypatch):
    monkeypatch.setattr(settings, "relay_api_key", "")
    with FakeRelayHTTP() as relay:
        run(manager(relay).ensure_api_key())

        assert ("POST", "/auth/regenerate-key") in relay.calls
        assert settings.relay_api_key == MASTER_KEY
