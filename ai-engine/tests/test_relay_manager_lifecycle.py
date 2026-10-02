#!/usr/bin/env python3
"""RelayManager lifecycle: start/stop/restart, Foundry auto-start, the
watchdog, spawn/env building, and the process-management helpers that don't
need a live relay (chrome lock/profile cleanup, binary resolution).

These mock at the process/HTTP boundary (subprocess.Popen/run, httpx) so the
tests exercise the manager's own control flow — retry/backoff, fail-closed
error paths, cleanup ordering — rather than mocking the methods under test.
"""

import asyncio
import os
import subprocess
import sys

import httpx
import pytest

import relay_proc.manager as rm
from relay_proc.manager import RelayManager
from config import settings


def run(coro):
    return asyncio.run(coro)


class FakeProc:
    """A stand-in for subprocess.Popen with controllable exit behavior."""

    def __init__(self, pid=4242, hang_on_terminate=False):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.hang_on_terminate = hang_on_terminate
        self._alive = True

    def poll(self):
        return None if self._alive else (self.returncode or 0)

    def terminate(self):
        self.terminated = True
        if not self.hang_on_terminate:
            self._alive = False
            self.returncode = 0

    def kill(self):
        self.killed = True
        self._alive = False
        self.returncode = -9

    def wait(self, timeout=None):
        if self.hang_on_terminate and timeout is not None and not self.killed:
            raise subprocess.TimeoutExpired(cmd="relay", timeout=timeout)
        self._alive = False
        return self.returncode or 0


@pytest.fixture
def manager(tmp_path):
    m = RelayManager()
    m.data_dir = tmp_path / "relay"
    m._credentials_path = m.data_dir / "aigm-credentials.json"
    return m


# ── start(): adopting an external relay ─────────────────────────────────────

def test_start_adopts_an_already_running_relay(manager, monkeypatch):
    monkeypatch.setattr(settings, "foundry_auto_start", False)
    manager._is_healthy = lambda timeout=1.0: _const_true()
    manager._clear_chrome_locks = lambda: None
    calls = []

    async def fake_ensure_api_key():
        calls.append("ensure_api_key")

    manager.ensure_api_key = fake_ensure_api_key

    run(manager.start())

    assert manager.adopted is True
    assert calls == ["ensure_api_key"]


async def _const_true():
    return True


async def _const_false():
    return False


# ── start(): spawning a managed relay ───────────────────────────────────────

def test_start_spawns_and_arms_the_watchdog_when_nothing_is_listening(manager, monkeypatch):
    monkeypatch.setattr(settings, "foundry_auto_start", False)
    healthy_calls = {"n": 0}

    async def fake_is_healthy(timeout=1.0):
        # First call (adoption probe) says unhealthy; _wait_ready's own call
        # then says healthy immediately.
        healthy_calls["n"] += 1
        return healthy_calls["n"] > 1

    manager._is_healthy = fake_is_healthy
    manager._ensure_binary = lambda: None
    manager._clear_chrome_locks = lambda: None

    spawned = {}

    def fake_spawn():
        spawned["called"] = True
        manager.proc = FakeProc()

    manager._spawn = fake_spawn

    async def fake_watch():
        return

    manager._watch = fake_watch

    api_key_calls = []

    async def fake_ensure_api_key():
        api_key_calls.append(1)

    manager.ensure_api_key = fake_ensure_api_key

    run(manager.start())

    assert manager.adopted is False
    assert spawned.get("called") is True
    assert manager.data_dir.exists()
    assert manager._watchdog is not None
    assert api_key_calls == [1]


def test_start_raises_when_relay_never_becomes_healthy(manager, monkeypatch):
    monkeypatch.setattr(settings, "foundry_auto_start", False)
    manager._is_healthy = lambda timeout=1.0: _const_false()
    manager._ensure_binary = lambda: None
    manager._clear_chrome_locks = lambda: None
    manager._spawn = lambda: setattr(manager, "proc", FakeProc())

    with pytest.raises(RuntimeError, match="did not become healthy"):
        run(manager.start())


# ── stop() ───────────────────────────────────────────────────────────────────

def test_stop_terminates_a_graceful_process(manager, monkeypatch):
    monkeypatch.setattr(settings, "foundry_shutdown_on_exit", True, raising=False)
    manager.proc = FakeProc()
    manager._foundry_started_by_us = False

    run(manager.stop())

    assert manager.proc.terminated is True
    assert manager.proc.killed is False


def test_stop_kills_a_process_that_ignores_sigterm(manager):
    manager.proc = FakeProc(hang_on_terminate=True)

    run(manager.stop())

    assert manager.proc.terminated is True
    assert manager.proc.killed is True


def test_stop_cancels_the_watchdog(manager):
    async def _forever():
        await asyncio.sleep(1000)

    manager.proc = None

    async def go():
        manager._watchdog = asyncio.create_task(_forever())
        await manager.stop()
        assert manager._watchdog is None

    run(go())


def test_stop_closes_the_log_file(manager, tmp_path):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager._log_file = open(manager.data_dir / "relay.log", "a")
    manager.proc = None

    run(manager.stop())

    assert manager._log_file is None


def test_stop_quits_foundry_only_when_owned_and_asked(manager):
    manager.proc = None
    stopped = []

    async def fake_stop_foundry():
        stopped.append(1)

    manager._stop_foundry_if_owned = fake_stop_foundry

    run(manager.stop(stop_foundry=True))
    assert stopped == [1]

    stopped.clear()
    run(manager.stop(stop_foundry=False))
    assert stopped == []


# ── restart() ────────────────────────────────────────────────────────────────

def test_restart_refuses_on_an_adopted_relay(manager):
    manager.adopted = True
    with pytest.raises(RuntimeError, match="externally-managed"):
        run(manager.restart())


def test_restart_stops_and_starts_leaving_foundry_alone(manager):
    calls = []

    async def fake_stop(*, stop_foundry=True):
        calls.append(("stop", stop_foundry))

    async def fake_start(*, start_foundry=True):
        calls.append(("start", start_foundry))

    manager.stop = fake_stop
    manager.start = fake_start
    manager.crashed = True

    run(manager.restart())

    assert calls == [("stop", False), ("start", False)]
    assert manager.crashed is False


# ── _foundry_process_running ─────────────────────────────────────────────────

def test_foundry_process_running_true_when_pgrep_matches(monkeypatch):
    monkeypatch.setattr(
        rm.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0),
    )
    assert RelayManager._foundry_process_running("/Applications/Foundry.app") is True


def test_foundry_process_running_false_when_pgrep_finds_nothing(monkeypatch):
    monkeypatch.setattr(
        rm.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1),
    )
    assert RelayManager._foundry_process_running("/Applications/Foundry.app") is False


def test_foundry_process_running_false_when_pgrep_is_missing(monkeypatch):
    def boom(*a, **k):
        raise OSError("no pgrep")

    monkeypatch.setattr(rm.subprocess, "run", boom)
    assert RelayManager._foundry_process_running("/Applications/Foundry.app") is False


# ── _ensure_foundry_started ──────────────────────────────────────────────────

def test_ensure_foundry_started_skips_when_auto_start_disabled(manager, monkeypatch):
    monkeypatch.setattr(settings, "foundry_auto_start", False)
    popen_calls = []
    monkeypatch.setattr(rm.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))

    run(manager._ensure_foundry_started())

    assert popen_calls == []


def test_ensure_foundry_started_skips_off_macos(manager, monkeypatch):
    monkeypatch.setattr(settings, "foundry_auto_start", True)
    monkeypatch.setattr(rm.sys, "platform", "linux")
    popen_calls = []
    monkeypatch.setattr(rm.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))

    run(manager._ensure_foundry_started())

    assert popen_calls == []


def test_ensure_foundry_started_warns_when_app_missing(manager, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "foundry_auto_start", True)
    monkeypatch.setattr(rm.sys, "platform", "darwin")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(settings, "foundry_app_path", str(tmp_path / "missing.app"))
    popen_calls = []
    monkeypatch.setattr(rm.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))

    run(manager._ensure_foundry_started())

    assert popen_calls == []


def test_ensure_foundry_started_leaves_an_already_running_app_alone(manager, monkeypatch, tmp_path):
    app = tmp_path / "Foundry.app"
    app.mkdir()
    monkeypatch.setattr(settings, "foundry_auto_start", True)
    monkeypatch.setattr(rm.sys, "platform", "darwin")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(settings, "foundry_app_path", str(app))
    monkeypatch.setattr(RelayManager, "_foundry_process_running", staticmethod(lambda p: True))
    popen_calls = []
    monkeypatch.setattr(rm.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))

    run(manager._ensure_foundry_started())

    assert popen_calls == []
    assert manager._foundry_started_by_us is False


def test_ensure_foundry_started_launches_and_waits(manager, monkeypatch, tmp_path):
    app = tmp_path / "Foundry.app"
    app.mkdir()
    monkeypatch.setattr(settings, "foundry_auto_start", True)
    monkeypatch.setattr(rm.sys, "platform", "darwin")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(settings, "foundry_app_path", str(app))
    monkeypatch.setattr(RelayManager, "_foundry_process_running", staticmethod(lambda p: False))
    popen_calls = []
    monkeypatch.setattr(
        rm.subprocess, "Popen",
        lambda args, **k: popen_calls.append(args) or FakeProc(),
    )
    waited = []

    async def fake_wait():
        waited.append(1)

    manager._wait_for_foundry_http = fake_wait

    run(manager._ensure_foundry_started())

    assert popen_calls and popen_calls[0][0] == "open"
    assert manager._foundry_started_by_us is True
    assert waited == [1]


def test_ensure_foundry_started_handles_a_failed_launch(manager, monkeypatch, tmp_path):
    app = tmp_path / "Foundry.app"
    app.mkdir()
    monkeypatch.setattr(settings, "foundry_auto_start", True)
    monkeypatch.setattr(rm.sys, "platform", "darwin")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(settings, "foundry_app_path", str(app))
    monkeypatch.setattr(RelayManager, "_foundry_process_running", staticmethod(lambda p: False))

    def boom(*a, **k):
        raise OSError("cannot launch")

    monkeypatch.setattr(rm.subprocess, "Popen", boom)
    waited = []

    async def fake_wait():
        waited.append(1)

    manager._wait_for_foundry_http = fake_wait

    run(manager._ensure_foundry_started())

    assert manager._foundry_started_by_us is False
    assert waited == [], "a launch failure must not wait for an app that never started"


# ── _wait_for_foundry_http ───────────────────────────────────────────────────

class _FakeAsyncClient:
    def __init__(self, get):
        self._get = get

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return self._get(url)


def test_wait_for_foundry_http_returns_as_soon_as_it_responds(manager, monkeypatch):
    get_calls = []
    monkeypatch.setattr(
        rm.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(lambda url: get_calls.append(url) or object()),
    )
    sleep_calls = []
    real_sleep = asyncio.sleep

    async def counting_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", counting_sleep)

    run(manager._wait_for_foundry_http(port=30000, timeout=5.0))

    assert len(get_calls) == 1, "a successful GET must end the wait without retrying"
    assert sleep_calls == [], "no retry sleep should happen on the first success"


def test_wait_for_foundry_http_gives_up_after_the_timeout(manager, monkeypatch):
    async def instant(_seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", instant)

    attempts = []

    def always_fails(url):
        attempts.append(url)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(rm.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(always_fails))

    # Must return rather than raise once the deadline passes, after at least
    # retrying the connection.
    run(manager._wait_for_foundry_http(port=30000, timeout=0.01))

    assert attempts, "it must have actually tried to reach Foundry before giving up"


# ── _stop_foundry_if_owned ───────────────────────────────────────────────────

def test_stop_foundry_if_owned_noop_when_not_owned(manager, monkeypatch):
    manager._foundry_started_by_us = False
    run_calls = []
    monkeypatch.setattr(rm.subprocess, "run", lambda *a, **k: run_calls.append(1))

    run(manager._stop_foundry_if_owned())

    assert run_calls == []


def test_stop_foundry_if_owned_noop_when_shutdown_on_exit_disabled(manager, monkeypatch):
    manager._foundry_started_by_us = True
    monkeypatch.setattr(settings, "foundry_shutdown_on_exit", False, raising=False)
    run_calls = []
    monkeypatch.setattr(rm.subprocess, "run", lambda *a, **k: run_calls.append(1))

    run(manager._stop_foundry_if_owned())

    assert run_calls == []
    assert manager._foundry_started_by_us is True


def test_stop_foundry_if_owned_quits_via_osascript(manager, monkeypatch):
    manager._foundry_started_by_us = True
    monkeypatch.setattr(settings, "foundry_shutdown_on_exit", True, raising=False)
    monkeypatch.setattr(settings, "foundry_app_path", "/Applications/Foundry Virtual Tabletop.app")
    seen = {}

    def fake_run(args, **k):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(rm.subprocess, "run", fake_run)

    run(manager._stop_foundry_if_owned())

    assert manager._foundry_started_by_us is False
    assert "Foundry Virtual Tabletop" in seen["args"][2]


def test_stop_foundry_if_owned_logs_a_nonzero_exit(manager, monkeypatch):
    manager._foundry_started_by_us = True
    monkeypatch.setattr(settings, "foundry_shutdown_on_exit", True, raising=False)
    monkeypatch.setattr(settings, "foundry_app_path", "/Applications/Foundry.app")
    monkeypatch.setattr(
        rm.subprocess, "run",
        lambda args, **k: subprocess.CompletedProcess(args, 1, stdout="", stderr="nope"),
    )

    run(manager._stop_foundry_if_owned())  # must not raise

    assert manager._foundry_started_by_us is False


def test_stop_foundry_if_owned_handles_oserror(manager, monkeypatch):
    manager._foundry_started_by_us = True
    monkeypatch.setattr(settings, "foundry_shutdown_on_exit", True, raising=False)
    monkeypatch.setattr(settings, "foundry_app_path", "/Applications/Foundry.app")

    def boom(*a, **k):
        raise OSError("no osascript")

    monkeypatch.setattr(rm.subprocess, "run", boom)

    run(manager._stop_foundry_if_owned())  # must not raise

    # The ownership flag was already cleared before the osascript attempt;
    # an OSError here must not leave it set and retried forever.
    assert manager._foundry_started_by_us is False


# ── ensure_api_key ───────────────────────────────────────────────────────────

def test_ensure_api_key_defers_validation_when_headless_auto_start_is_possible(manager, monkeypatch):
    manager._load_credentials = lambda: {"api_key": "stored-key"}
    monkeypatch.setattr(settings, "relay_allow_headless", True, raising=False)
    monkeypatch.setattr(settings, "relay_api_key", "", raising=False)

    async def should_not_be_called(key):
        raise AssertionError("validation must be skipped")

    manager._key_is_valid = should_not_be_called

    run(manager.ensure_api_key())

    assert settings.relay_api_key == "stored-key"


def test_ensure_api_key_accepts_a_validated_stored_key(manager, monkeypatch):
    manager._load_credentials = lambda: {"api_key": "stored-key"}
    monkeypatch.setattr(settings, "relay_allow_headless", False, raising=False)
    monkeypatch.setattr(settings, "relay_api_key", "", raising=False)

    async def valid(key):
        return True

    manager._key_is_valid = valid

    run(manager.ensure_api_key())

    assert settings.relay_api_key == "stored-key"


def test_ensure_api_key_stops_the_relay_and_raises_on_a_rejected_stored_key(manager, monkeypatch):
    """Fail closed: a rejected stored key must not leave the process running."""
    manager._load_credentials = lambda: {"api_key": "stored-key"}
    monkeypatch.setattr(settings, "relay_allow_headless", False, raising=False)

    async def invalid(key):
        return False

    manager._key_is_valid = invalid
    stopped = []

    async def fake_stop():
        stopped.append(1)

    manager.stop = fake_stop

    with pytest.raises(RuntimeError, match="rejected"):
        run(manager.ensure_api_key())

    assert stopped == [1], "a bad stored key must stop the subprocess, not leave it orphaned"


def test_ensure_api_key_persists_a_valid_env_key(manager, monkeypatch):
    manager._load_credentials = lambda: {}
    saved = {}
    manager._save_credentials = lambda creds: saved.update(creds)
    monkeypatch.setattr(settings, "relay_api_key", "env-key", raising=False)

    async def valid(key):
        return True

    manager._key_is_valid = valid

    run(manager.ensure_api_key())

    assert saved.get("api_key") == "env-key"


def test_ensure_api_key_reprovisions_when_the_env_key_is_rejected(manager, monkeypatch):
    from fake_relay_http import MASTER_KEY, FakeRelayHTTP

    manager._load_credentials = lambda: {"email": "a@b.c", "password": "pw"}
    monkeypatch.setattr(settings, "relay_api_key", "stale-env-key", raising=False)

    async def invalid(key):
        return False

    manager._key_is_valid = invalid
    saved = {}
    manager._save_credentials = lambda creds: saved.update(creds)

    with FakeRelayHTTP() as relay:
        monkeypatch.setattr(settings, "relay_url", relay.url)
        run(manager.ensure_api_key())
        assert ("POST", "/auth/regenerate-key") in relay.calls

    assert saved.get("api_key") == MASTER_KEY
    assert settings.relay_api_key == MASTER_KEY


def test_ensure_api_key_stops_the_relay_when_provisioning_fails(manager, monkeypatch):
    manager._load_credentials = lambda: {"email": "a@b.c", "password": "pw"}
    monkeypatch.setattr(settings, "relay_api_key", "", raising=False)
    monkeypatch.setattr(settings, "relay_url", "http://127.0.0.1:1")  # nothing listening
    stopped = []

    async def fake_stop():
        stopped.append(1)

    manager.stop = fake_stop

    with pytest.raises(RuntimeError, match="Could not provision"):
        run(manager.ensure_api_key())

    assert stopped == [1]


# ── ensure_rest_scoped_key ───────────────────────────────────────────────────

def test_ensure_rest_scoped_key_gives_up_without_a_session_token(manager, monkeypatch):
    async def no_token(creds):
        return None

    manager._get_session_token = no_token
    monkeypatch.setattr(settings, "relay_scoped_key", "", raising=False)

    run(manager.ensure_rest_scoped_key())

    assert settings.relay_scoped_key == ""


def test_ensure_rest_scoped_key_logs_but_does_not_raise_on_failure(manager, monkeypatch):
    from fake_relay_http import SESSION_TOKEN

    async def token(creds):
        return SESSION_TOKEN

    manager._get_session_token = token
    monkeypatch.setattr(settings, "relay_url", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "relay_scoped_key", "unchanged", raising=False)

    run(manager.ensure_rest_scoped_key())  # must not raise

    assert settings.relay_scoped_key == "unchanged"


# ── admin_credentials ────────────────────────────────────────────────────────

def test_admin_credentials_returns_loaded_credentials(manager):
    manager._load_credentials = lambda: {"email": "gm@test", "password": "pw"}
    assert manager.admin_credentials() == {"email": "gm@test", "password": "pw"}


# ── ensure_headless_session: scoped-key failure ─────────────────────────────

def test_ensure_headless_session_gives_up_without_a_scoped_key(manager):
    async def token(creds):
        return "session-token"

    async def no_scoped_key(token):
        return None

    manager._get_session_token = token
    manager._get_or_create_scoped_key = no_scoped_key

    assert run(manager.ensure_headless_session()) is None


# ── restart_headless_session: the relay-restart fallback ───────────────────

def test_restart_headless_session_restarts_the_relay_when_chrome_kill_is_not_enough(manager, monkeypatch):
    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)
    manager._kill_profile_chrome = lambda: None
    manager._clear_chrome_locks = lambda: None

    attempts = {"n": 0}

    async def ensure(world_name=None):
        attempts["n"] += 1
        return None if attempts["n"] == 1 else "recovered-client"

    manager.ensure_headless_session = ensure
    restarted = []

    async def fake_restart():
        restarted.append(1)

    manager.restart = fake_restart

    result = run(manager.restart_headless_session())

    assert restarted == [1]
    assert result == "recovered-client"


def test_restart_headless_session_survives_a_failed_relay_restart(manager, monkeypatch):
    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)
    manager._kill_profile_chrome = lambda: None
    manager._clear_chrome_locks = lambda: None

    async def ensure(world_name=None):
        return None

    manager.ensure_headless_session = ensure

    async def fake_restart():
        raise RuntimeError("relay binary missing")

    manager.restart = fake_restart

    assert run(manager.restart_headless_session()) is None


def test_restart_headless_session_skips_relay_restart_for_an_adopted_relay(manager, monkeypatch):
    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)
    manager._kill_profile_chrome = lambda: None
    manager._clear_chrome_locks = lambda: None
    manager.adopted = True

    async def ensure(world_name=None):
        return None

    manager.ensure_headless_session = ensure
    restart_calls = []

    async def fake_restart():
        restart_calls.append(1)

    manager.restart = fake_restart

    assert run(manager.restart_headless_session()) is None
    assert restart_calls == [], "an adopted relay cannot be restarted by this manager"


# ── _get_or_create_scoped_key / _find_active_session: transport failures ───

def test_get_or_create_scoped_key_handles_a_transport_error(manager, monkeypatch):
    monkeypatch.setattr(settings, "relay_url", "http://127.0.0.1:1")
    assert run(manager._get_or_create_scoped_key("token")) is None


def test_find_active_session_handles_a_transport_error(manager, monkeypatch):
    monkeypatch.setattr(settings, "relay_url", "http://127.0.0.1:1")
    assert run(manager._find_active_session("scoped-key")) is None


def test_find_active_session_handles_a_bare_list_payload(manager, monkeypatch):
    from fake_relay_http import FakeRelayHTTP

    with FakeRelayHTTP(active_sessions=[{"clientId": "abc"}]) as relay:
        monkeypatch.setattr(settings, "relay_url", relay.url)
        # The real relay wraps sessions in {"activeSessions": [...]}; this
        # exercises the branch that also accepts a bare list.
        assert run(manager._find_active_session("scoped-key")) == "abc"


# ── _launch_headless_session: transport + permanent errors ─────────────────

def test_launch_headless_session_degrades_on_a_transport_error(manager, monkeypatch):
    monkeypatch.setattr(settings, "relay_url", "http://127.0.0.1:1")
    assert run(manager._launch_headless_session("scoped-key")) is None


class _PermanentErrorAsyncClient:
    """A minimal httpx.AsyncClient stand-in: handshake ok, start-session 400
    with a body _is_permanent_headless_error recognizes."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        class Resp:
            def __init__(self, status_code, body, text=""):
                self.status_code = status_code
                self._body = body
                self.text = text or str(body)

            def json(self):
                return self._body

        if url.endswith("/session-handshake"):
            return Resp(200, {"token": "handshake-token"})
        if url.endswith("/start-session"):
            return Resp(
                400,
                {"error": "configured Foundry user not found"},
                text="configured Foundry user not found",
            )
        return Resp(404, {})


def test_launch_headless_session_latches_on_a_permanent_credential_error(manager, monkeypatch):
    monkeypatch.setattr(rm.httpx, "AsyncClient", _PermanentErrorAsyncClient)

    result = run(manager._launch_headless_session("scoped-key"))

    assert result is None
    assert manager._headless_blocked is True


# ── _ensure_binary ───────────────────────────────────────────────────────────

def test_ensure_binary_noop_when_binary_present(manager, tmp_path, monkeypatch):
    manager.binary = tmp_path / "relay-bin"
    manager.binary.write_text("x")
    build_calls = []
    monkeypatch.setattr(rm.subprocess, "run", lambda *a, **k: build_calls.append(a))

    manager._ensure_binary()

    assert build_calls == [], "an existing binary must never be rebuilt"


def test_ensure_binary_builds_with_go_when_missing(manager, tmp_path, monkeypatch):
    manager.binary = tmp_path / "relay-bin"
    manager.repo_root = tmp_path
    (tmp_path / "relay" / "go-relay").mkdir(parents=True)
    monkeypatch.setattr(rm.shutil, "which", lambda n: "/usr/bin/go")
    build_calls = []

    def fake_run(args, **k):
        build_calls.append(args)
        manager.binary.write_text("built")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(rm.subprocess, "run", fake_run)

    manager._ensure_binary()

    assert build_calls and build_calls[0][:2] == ["go", "build"]


def test_ensure_binary_raises_without_go_or_a_binary(manager, tmp_path, monkeypatch):
    manager.binary = tmp_path / "relay-bin"
    monkeypatch.setattr(rm.shutil, "which", lambda n: None)

    with pytest.raises(RuntimeError, match="Go is not installed"):
        manager._ensure_binary()


# ── _kill_profile_chrome / _clear_chrome_locks ──────────────────────────────

def test_kill_profile_chrome_noop_without_pgrep(manager, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(rm.subprocess, "run", boom)
    kill_calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append(pid))

    manager._kill_profile_chrome()

    assert kill_calls == [], "with no pgrep there is nothing to signal"


def test_kill_profile_chrome_kills_matches_but_spares_itself(manager, monkeypatch):
    me = os.getpid()
    other_pid = me + 1
    monkeypatch.setattr(
        rm.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{me}\n{other_pid}\n"),
    )
    killed = []

    def fake_kill(pid, sig):
        killed.append(pid)

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(rm.time, "sleep", lambda s: None)

    manager._kill_profile_chrome()

    assert killed == [other_pid]


def test_kill_profile_chrome_ignores_processes_already_gone(manager, monkeypatch):
    other_pid = os.getpid() + 1
    monkeypatch.setattr(
        rm.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{other_pid}\n"),
    )
    attempted = []

    def fake_kill(pid, sig):
        attempted.append(pid)
        raise ProcessLookupError()

    monkeypatch.setattr(os, "kill", fake_kill)

    manager._kill_profile_chrome()  # a ProcessLookupError must not propagate

    assert attempted == [other_pid]


def test_clear_chrome_locks_removes_stale_lock_files(manager):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager._kill_profile_chrome = lambda: None
    profile = manager.data_dir / "chrome-profile-123"
    profile.mkdir()
    lock = profile / "SingletonLock"
    lock.write_text("x")

    manager._clear_chrome_locks()

    assert not lock.exists()


# ── _reap_stale_profiles: permission and removal failures ──────────────────

def test_reap_stale_profiles_leaves_a_profile_it_cannot_signal(manager, monkeypatch):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    unreadable = manager.data_dir / "chrome-profile-555"
    unreadable.mkdir()

    def fake_kill(pid, sig):
        raise PermissionError()

    monkeypatch.setattr(os, "kill", fake_kill)

    manager._reap_stale_profiles()

    assert unreadable.exists()


def test_reap_stale_profiles_logs_but_does_not_raise_on_removal_failure(manager, monkeypatch):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    dead = manager.data_dir / "chrome-profile-999998"
    dead.mkdir()

    def fake_kill(pid, sig):
        raise ProcessLookupError()

    def boom_rmtree(path):
        raise OSError("disk gremlin")

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(rm.shutil, "rmtree", boom_rmtree)

    manager._reap_stale_profiles()  # an rmtree failure must not propagate

    assert dead.exists(), "a removal failure must leave the directory as-is, not half-deleted"


# ── _spawn ───────────────────────────────────────────────────────────────────

def test_spawn_builds_the_expected_environment(manager, monkeypatch, tmp_path):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager.static_dir = tmp_path / "relay-static"
    manager.binary = tmp_path / "relay-bin"
    manager.binary.write_text("x")
    manager._load_credentials = lambda: {"email": "a@b.c", "password": "Secret123"}
    manager._clear_chrome_locks = lambda: None
    monkeypatch.setattr(settings, "relay_log_level", "info", raising=False)
    monkeypatch.setattr(settings, "relay_allow_headless", True, raising=False)
    monkeypatch.setattr(rm, "_resolve_chrome_path", lambda: "/usr/bin/google-chrome")
    monkeypatch.setenv("RELAY_ENV_STRIPE_KEY", "sk_test_123")

    captured = {}

    def fake_popen(args, cwd, env, stdout, stderr):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = env
        return FakeProc()

    monkeypatch.setattr(rm.subprocess, "Popen", fake_popen)

    manager._spawn()

    assert captured["args"] == [str(manager.binary)]
    assert captured["cwd"] == manager.data_dir
    assert captured["env"]["ADMIN_EMAIL"] == "a@b.c"
    assert captured["env"]["ADMIN_PASSWORD"] == "Secret123"
    assert captured["env"]["ALLOW_HEADLESS"] == "true"
    assert captured["env"]["PUPPETEER_EXECUTABLE_PATH"] == "/usr/bin/google-chrome"
    assert captured["env"]["STRIPE_KEY"] == "sk_test_123"
    assert manager.proc is not None


def test_spawn_warns_but_continues_without_chrome_when_headless_is_allowed(manager, monkeypatch, tmp_path):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager.static_dir = tmp_path / "relay-static"
    manager.binary = tmp_path / "relay-bin"
    manager.binary.write_text("x")
    manager._load_credentials = lambda: {"email": "a@b.c", "password": "Secret123"}
    manager._clear_chrome_locks = lambda: None
    monkeypatch.setattr(settings, "relay_log_level", "info", raising=False)
    monkeypatch.setattr(settings, "relay_allow_headless", True, raising=False)
    monkeypatch.setattr(rm, "_resolve_chrome_path", lambda: "")
    captured = {}
    monkeypatch.setattr(
        rm.subprocess, "Popen",
        lambda args, cwd, env, stdout, stderr: captured.setdefault("env", env) or FakeProc(),
    )

    manager._spawn()  # must not raise despite no chrome path

    assert "PUPPETEER_EXECUTABLE_PATH" not in captured["env"], (
        "with no chrome found, no (empty) executable path should be passed through"
    )


# ── _wait_ready ──────────────────────────────────────────────────────────────

def test_wait_ready_raises_if_the_process_exits_during_startup(manager):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    proc = FakeProc()
    proc._alive = False
    proc.returncode = 7
    manager.proc = proc

    with pytest.raises(RuntimeError, match="exited during startup"):
        run(manager._wait_ready(timeout=1.0))


def test_wait_ready_returns_once_healthy(manager, monkeypatch):
    manager.proc = FakeProc()
    calls = {"n": 0}

    async def fake_is_healthy(timeout=1.0):
        calls["n"] += 1
        return calls["n"] > 1

    manager._is_healthy = fake_is_healthy

    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)

    run(manager._wait_ready(timeout=5.0))

    assert calls["n"] >= 2


def test_wait_ready_times_out(manager, monkeypatch):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager.proc = FakeProc()

    async def never(timeout=1.0):
        return False

    manager._is_healthy = never

    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)

    with pytest.raises(RuntimeError, match="did not become healthy"):
        run(manager._wait_ready(timeout=0.001))


# ── _is_healthy ──────────────────────────────────────────────────────────────

def test_is_healthy_true_on_200(manager, monkeypatch):
    class Resp:
        status_code = 200

    class Client(_FakeAsyncClient):
        async def get(self, url):
            return Resp()

    monkeypatch.setattr(rm.httpx, "AsyncClient", lambda *a, **k: Client(None))
    assert run(manager._is_healthy()) is True


def test_is_healthy_false_on_transport_error(manager, monkeypatch):
    monkeypatch.setattr(settings, "relay_url", "http://127.0.0.1:1")
    assert run(manager._is_healthy(timeout=0.5)) is False


# ── _watch: the self-heal loop ──────────────────────────────────────────────

def test_watch_gives_up_after_the_restart_budget_is_exhausted(manager, monkeypatch):
    monkeypatch.setattr(rm, "_MAX_RESTARTS_PER_WINDOW", 1)
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager.proc = FakeProc()
    manager.proc._alive = False
    manager._restart_times = [0.0, 0.0]  # already at/over budget for t=now

    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)
    monkeypatch.setattr(rm.time, "monotonic", lambda: 100.0)

    run(manager._watch())  # returns (doesn't loop forever) once crashed

    assert manager.crashed is True


def test_watch_respawns_a_dead_process_and_counts_the_restart(manager, monkeypatch):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager.proc = FakeProc()
    manager.proc._alive = False

    respawned = {"n": 0}

    def fake_spawn():
        respawned["n"] += 1
        manager.proc = FakeProc()
        # Die again next loop so the watchdog hits its restart budget and
        # returns, instead of running forever.
        manager.proc._alive = False

    manager._spawn = fake_spawn

    async def fake_wait_ready(timeout=30.0):
        return

    manager._wait_ready = fake_wait_ready

    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)
    monkeypatch.setattr(rm, "_MAX_RESTARTS_PER_WINDOW", 2)

    run(manager._watch())

    assert respawned["n"] >= 1
    assert manager.restarts >= 1
    assert manager.crashed is True


def test_watch_logs_but_survives_a_failed_restart(manager, monkeypatch):
    manager.data_dir.mkdir(parents=True, exist_ok=True)
    manager.proc = FakeProc()
    manager.proc._alive = False

    def fake_spawn():
        manager.proc = FakeProc()
        manager.proc._alive = False

    manager._spawn = fake_spawn

    async def fake_wait_ready(timeout=30.0):
        raise RuntimeError("relay exited during startup")

    manager._wait_ready = fake_wait_ready

    async def instant(_seconds):
        return

    monkeypatch.setattr(asyncio, "sleep", instant)
    monkeypatch.setattr(rm, "_MAX_RESTARTS_PER_WINDOW", 1)

    run(manager._watch())  # must not propagate the RuntimeError

    assert manager.crashed is True


def test_watch_leaves_a_healthy_process_alone(manager, monkeypatch):
    manager.proc = FakeProc()  # alive
    spawn_calls = []
    manager._spawn = lambda: spawn_calls.append(1)

    async def one_iteration_then_stop(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", one_iteration_then_stop)

    with pytest.raises(asyncio.CancelledError):
        run(manager._watch())

    assert spawn_calls == [], "a live process must not be respawned"


# ── _hint_migration ──────────────────────────────────────────────────────────

def test_hint_migration_noop_when_no_legacy_db_exists(manager, tmp_path, caplog):
    manager.repo_root = tmp_path
    manager.data_dir = tmp_path / "data" / "relay"
    manager.data_dir.mkdir(parents=True)

    with caplog.at_level("INFO", logger="relay"):
        manager._hint_migration()

    assert "existing relay database" not in caplog.text


def test_hint_migration_notices_an_unmigrated_legacy_database(manager, tmp_path):
    manager.repo_root = tmp_path
    legacy = tmp_path.parent / "foundryvtt-rest-api-relay" / "data"
    legacy.mkdir(parents=True)
    (legacy / "relay.db").write_text("x")
    manager.data_dir = tmp_path / "data" / "relay"
    manager.data_dir.mkdir(parents=True)

    manager._hint_migration()  # logs info; must not raise
    assert not (manager.data_dir / "relay.db").exists()
