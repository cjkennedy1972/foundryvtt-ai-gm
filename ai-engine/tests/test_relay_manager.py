"""Unit tests for relay_proc.manager's pure/local helpers.

The relay process itself needs a real binary + Foundry, so the lifecycle paths
are integration-only. These cover the logic that doesn't need a live process:
error classification, chrome resolution, status reporting, credential handling
(including file permissions), and log tailing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import relay_proc.manager as rm
from relay_proc.manager import RelayManager, _is_permanent_headless_error


# ── permanent vs transient headless errors ───────────────────────────────────

def test_permanent_headless_errors_detected():
    assert _is_permanent_headless_error("Configured Foundry user not found") is True
    assert _is_permanent_headless_error("configured foundry user is already logged in") is True


def test_permanent_check_is_case_insensitive():
    assert _is_permanent_headless_error("CONFIGURED FOUNDRY USER NOT FOUND") is True


def test_transient_errors_are_retryable():
    assert _is_permanent_headless_error("connection refused") is False
    assert _is_permanent_headless_error("") is False


# ── chrome path resolution ───────────────────────────────────────────────────

def test_chrome_path_prefers_explicit_setting(monkeypatch):
    monkeypatch.setattr(rm.settings, "relay_chrome_path", "/custom/chrome", raising=False)
    assert rm._resolve_chrome_path() == "/custom/chrome"


def test_chrome_path_uses_existing_absolute_candidate(monkeypatch):
    monkeypatch.setattr(rm.settings, "relay_chrome_path", "", raising=False)
    monkeypatch.setattr(rm, "_CHROME_CANDIDATES", ["/opt/my-chrome"])
    monkeypatch.setattr(rm.Path, "exists", lambda self: str(self) == "/opt/my-chrome")
    assert rm._resolve_chrome_path() == "/opt/my-chrome"


def test_chrome_path_falls_back_to_which(monkeypatch):
    monkeypatch.setattr(rm.settings, "relay_chrome_path", "", raising=False)
    monkeypatch.setattr(rm, "_CHROME_CANDIDATES", ["chromium"])
    monkeypatch.setattr(rm.shutil, "which", lambda n: "/usr/bin/chromium")
    assert rm._resolve_chrome_path() == "/usr/bin/chromium"


def test_chrome_path_empty_when_nothing_found(monkeypatch):
    monkeypatch.setattr(rm.settings, "relay_chrome_path", "", raising=False)
    monkeypatch.setattr(rm, "_CHROME_CANDIDATES", ["nope"])
    monkeypatch.setattr(rm.shutil, "which", lambda n: None)
    assert rm._resolve_chrome_path() == ""


# ── status ───────────────────────────────────────────────────────────────────

class _Proc:
    def __init__(self, pid=123, alive=True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 1


def test_status_reports_stopped_manager():
    m = RelayManager()
    m.proc = None
    st = m.status()
    assert st["running"] is False and st["pid"] is None and st["managed"] is True


def test_status_reports_running_pid():
    m = RelayManager()
    m.proc = _Proc(pid=999)
    st = m.status()
    assert st["running"] is True and st["pid"] == 999


def test_status_dead_process_has_no_pid():
    m = RelayManager()
    m.proc = _Proc(alive=False)
    st = m.status()
    assert st["running"] is False and st["pid"] is None


def test_status_adopted_relay_is_running_but_unmanaged():
    m = RelayManager()
    m.adopted = True
    m.proc = None
    st = m.status()
    assert st["running"] is True and st["managed"] is False


def test_status_surfaces_crash_and_restart_counters():
    m = RelayManager()
    m.crashed = True
    m.restarts = 3
    st = m.status()
    assert st["crashed"] is True and st["restarts"] == 3
    assert st["dashboard_url"].startswith("http://localhost:")


# ── credentials ──────────────────────────────────────────────────────────────

def test_load_credentials_reads_existing_file(tmp_path):
    m = RelayManager()
    m.data_dir = tmp_path
    m._credentials_path = tmp_path / "creds.json"
    m._credentials_path.write_text(json.dumps({"email": "a@b.c", "password": "pw"}))
    assert m._load_credentials() == {"email": "a@b.c", "password": "pw"}


def test_load_credentials_generates_compliant_password(tmp_path, monkeypatch):
    """Relay password rules: >=8 chars with upper, lower, and digit."""
    monkeypatch.setattr(rm.settings, "relay_admin_password", "", raising=False)
    m = RelayManager()
    m.data_dir = tmp_path
    m._credentials_path = tmp_path / "creds.json"

    creds = m._load_credentials()
    pw = creds["password"]
    assert len(pw) >= 8
    assert any(c.isupper() for c in pw)
    assert any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw)
    # Generated credentials are persisted for reuse.
    assert m._credentials_path.exists()


def test_save_credentials_is_owner_only(tmp_path):
    """Credentials must not be world-readable."""
    m = RelayManager()
    m.data_dir = tmp_path / "nested"
    m._credentials_path = m.data_dir / "creds.json"
    m._save_credentials({"email": "a@b.c", "password": "pw"})
    assert m._credentials_path.exists()
    assert (m._credentials_path.stat().st_mode & 0o777) == 0o600


# ── log tailing ──────────────────────────────────────────────────────────────

def test_tail_log_missing_file(tmp_path):
    m = RelayManager()
    m.data_dir = tmp_path
    assert m._tail_log() == "(no relay.log)"


def test_tail_log_returns_last_lines(tmp_path):
    m = RelayManager()
    m.data_dir = tmp_path
    (tmp_path / "relay.log").write_text("\n".join(f"line{i}" for i in range(50)))
    out = m._tail_log(lines=5)
    assert out.splitlines() == ["line45", "line46", "line47", "line48", "line49"]
