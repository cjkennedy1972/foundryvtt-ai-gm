#!/usr/bin/env python3
"""ensure_headless_session and its self-heal, over the relay's REST surface.

These two sit on top of the provisioning calls covered in
test_relay_manager_provisioning: log in, get a session:manage key, reuse an
existing session or launch one, then bind a REST key to whatever clientId
came back.

The whole chain was driven against the live relay on the amd64 docker host —
it answered clientId "qsl-integration-test" — so the double below returns
what that relay returns.

ensure_headless_session is idempotent by design: a session already running
is reused rather than launching a second Chrome. restart_headless_session is
the opposite, and exists because a dead-but-alive Chrome leaves a session the
relay still reports as active, which a plain ensure would happily reuse.

Run:
    cd ai-engine && python -m pytest tests/test_relay_manager_sessions.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from fake_relay_http import FakeRelayHTTP
from relay_proc.manager import RelayManager

CREDS = {"email": "admin@test.local", "password": "AdminTestPass1"}


@pytest.fixture
def no_wait(monkeypatch):
    """restart_headless_session waits 3s for the relay to notice Chrome died.
    Real, and not what these assert."""
    real_sleep = asyncio.sleep

    async def instant(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", instant)


@pytest.fixture
def manager(monkeypatch):
    def build(relay, **attrs):
        monkeypatch.setattr(settings, "relay_url", relay.url)
        monkeypatch.setattr(settings, "foundry_world", "", raising=False)
        m = RelayManager()
        m._load_credentials = lambda: dict(CREDS)
        m._clear_chrome_locks = lambda: None
        m._kill_profile_chrome = lambda: None
        for k, v in attrs.items():
            setattr(m, k, v)
        return m
    return build


def run(coro):
    return asyncio.run(coro)


# ── the happy path ────────────────────────────────────────────────────────

def test_a_session_is_launched_and_its_client_id_returned(manager):
    with FakeRelayHTTP() as relay:
        assert run(manager(relay).ensure_headless_session()) == "qsl-integration-test"
        assert ("POST", "/start-session") in relay.calls


def test_a_rest_key_is_bound_to_the_new_session(manager):
    """Without the binding the relay falls back to "exactly one WebSocket
    client under this master key", which breaks once the headless module and
    the engine authenticate as different principals."""
    with FakeRelayHTTP() as relay:
        run(manager(relay).ensure_headless_session())

        rest = [k for k in relay.created if k["name"] == "aigm-engine"]
        assert rest, "no REST key was provisioned"
        assert rest[-1]["scopedClientId"] == "qsl-integration-test"


def test_the_requested_world_is_remembered_for_a_later_self_heal(manager):
    with FakeRelayHTTP() as relay:
        m = manager(relay)

        run(m.ensure_headless_session("The Sunken Chapel"))

        assert m._headless_world_name == "The Sunken Chapel"


# ── idempotence ───────────────────────────────────────────────────────────

def test_an_existing_session_is_reused_rather_than_launching_chrome(manager):
    with FakeRelayHTTP(active_sessions=[{"clientId": "already-running"}]) as relay:
        assert run(manager(relay).ensure_headless_session()) == "already-running"
        assert ("POST", "/start-session") not in relay.calls


def test_a_reused_session_still_gets_its_rest_key_bound(manager):
    with FakeRelayHTTP(active_sessions=[{"clientId": "already-running"}]) as relay:
        run(manager(relay).ensure_headless_session())

        rest = [k for k in relay.created if k["name"] == "aigm-engine"]
        assert rest[-1]["scopedClientId"] == "already-running"


# ── giving up ─────────────────────────────────────────────────────────────

def test_a_blocked_run_does_not_touch_the_relay(manager):
    """After a permanent credential rejection, retrying only resets the
    relay's brute-force limiter."""
    with FakeRelayHTTP() as relay:
        m = manager(relay, _headless_blocked=True)

        assert run(m.ensure_headless_session()) is None
        assert relay.calls == []


def test_a_rejected_login_yields_no_session(manager):
    with FakeRelayHTTP(login_ok=False) as relay:
        assert run(manager(relay).ensure_headless_session()) is None
        assert ("POST", "/start-session") not in relay.calls


def test_no_stored_world_credential_yields_no_session(manager):
    with FakeRelayHTTP(has_foundry_credential=False) as relay:
        assert run(manager(relay).ensure_headless_session()) is None


def test_a_failed_launch_binds_no_rest_key(manager):
    with FakeRelayHTTP(start_session_status=500) as relay:
        assert run(manager(relay).ensure_headless_session()) is None

        assert [k for k in relay.created if k["name"] == "aigm-engine"] == []


# ── the self-heal ─────────────────────────────────────────────────────────

@pytest.mark.usefixtures("no_wait")
def test_a_restart_kills_chrome_before_relaunching(manager):
    """A dead-but-alive Chrome leaves a session the relay still reports as
    active, so a plain ensure would reuse the corpse."""
    killed = []
    with FakeRelayHTTP(active_sessions=[{"clientId": "stale"}]) as relay:
        m = manager(relay)
        m._kill_profile_chrome = lambda: killed.append(1)

        run(m.restart_headless_session())

        assert killed, "Chrome was not killed before the relaunch"


@pytest.mark.usefixtures("no_wait")
def test_a_restart_is_skipped_when_the_credentials_were_rejected(manager):
    killed = []
    with FakeRelayHTTP() as relay:
        m = manager(relay, _headless_blocked=True)
        m._kill_profile_chrome = lambda: killed.append(1)

        assert run(m.restart_headless_session()) is None
        assert killed == [], "a blocked run should not even kill Chrome"


@pytest.mark.usefixtures("no_wait")
def test_a_restart_remembers_the_world_it_was_given(manager):
    with FakeRelayHTTP() as relay:
        m = manager(relay)

        run(m.restart_headless_session("The Sunken Chapel"))

        assert m._headless_world_name == "The Sunken Chapel"
