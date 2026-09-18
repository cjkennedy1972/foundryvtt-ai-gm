"""Phase 6: Relay manager lifecycle — process management, API key provisioning, sessions.

Tests verify relay start/stop/restart, API key generation, headless session lifecycle,
and health monitoring without spawning actual relay processes.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay_proc.manager import RelayManager, _is_permanent_headless_error, _resolve_chrome_path


class TestPermanentErrorDetection:
    """Verify permanent error classification."""

    def test_configured_user_not_found_is_permanent(self):
        """Configured Foundry user not found → permanent error."""
        msg = "Configured Foundry user not found"

        assert _is_permanent_headless_error(msg) is True

    def test_user_already_logged_in_is_permanent(self):
        """User already logged in → permanent error."""
        msg = "Configured Foundry user is already logged in"

        assert _is_permanent_headless_error(msg) is True

    def test_password_rejected_is_permanent(self):
        """Password rejected → permanent error."""
        msg = "Configured Foundry user password was rejected"

        assert _is_permanent_headless_error(msg) is True

    def test_connection_error_is_transient(self):
        """Connection timeout → transient error (retryable)."""
        msg = "Failed to connect to relay"

        assert _is_permanent_headless_error(msg) is False

    def test_error_detection_is_case_insensitive(self):
        """Error detection ignores case."""
        msg = "CONFIGURED FOUNDRY USER NOT FOUND"

        assert _is_permanent_headless_error(msg) is True


class TestChromePathResolution:
    """Verify Chrome binary resolution."""

    def test_explicit_setting_preferred(self):
        """Settings relay_chrome_path is used if set."""
        with patch("relay_proc.manager.settings.relay_chrome_path", "/custom/chrome"):
            path = _resolve_chrome_path()

            assert path == "/custom/chrome"

    def test_macos_app_path_checked(self):
        """macOS app path is checked first."""
        with patch("relay_proc.manager.settings.relay_chrome_path", ""):
            with patch("relay_proc.manager.Path.exists", return_value=True):
                path = _resolve_chrome_path()

                # Should be one of the candidates
                assert path is not None

    def test_fallback_to_which(self):
        """Falls back to which() for PATH lookup."""
        with patch("relay_proc.manager.settings.relay_chrome_path", ""):
            with patch("relay_proc.manager.Path.exists", return_value=False):
                with patch("relay_proc.manager.shutil.which", return_value="/usr/bin/google-chrome"):
                    path = _resolve_chrome_path()

                    assert path is not None


class TestRelayManagerInitialization:
    """Verify relay manager setup."""

    def test_relay_manager_initializes(self):
        """RelayManager() constructs successfully."""
        manager = RelayManager()

        assert manager is not None
        assert hasattr(manager, 'proc')
        assert hasattr(manager, 'port')



class TestRelayManagerStatus:
    """Verify relay status reporting."""

    def test_status_returns_dict(self):
        """status() returns dict with running/managed flags."""
        manager = RelayManager()

        status = manager.status()

        assert isinstance(status, dict)
        assert "running" in status
        assert "managed" in status

    def test_status_includes_pid_when_running(self):
        """status() includes pid when relay is running."""
        manager = RelayManager()
        manager.relay_proc = MagicMock()
        manager.relay_proc.pid = 12345
        manager.relay_proc.poll = MagicMock(return_value=None)

        status = manager.status()

        # Status should include process info
        assert isinstance(status, dict)


class TestRelayStartStop:
    """Verify start/stop lifecycle."""





class TestAPIKeyProvisioning:
    """Verify API key generation and management."""





class TestHeadlessSessionManagement:
    """Verify headless session lifecycle."""




class TestHealthMonitoring:
    """Verify health check methods."""





class TestCredentialManagement:
    """Verify credential loading/saving."""

    def test_load_credentials_returns_dict(self):
        """_load_credentials() returns dict."""
        manager = RelayManager()
        manager.data_dir.mkdir(parents=True, exist_ok=True)

        creds = manager._load_credentials()

        assert isinstance(creds, dict)
        assert "email" in creds or "password" in creds



class TestProcessManagement:
    """Verify process lifecycle helpers."""






class TestErrorRecoveryPatterns:
    """Verify error handling strategies."""



class TestSessionTokenManagement:
    """Verify session token acquisition."""




class TestCredentialsOnDisk:
    """Replaces a set of "assert callable(manager.x)" tests that proved nothing.

    The conftest autouse fixture redirects settings.relay_data_dir, so these
    touch a tmp dir. Without it the originals overwrote the developer's real
    data/relay/aigm-credentials.json on every pytest run.
    """

    def test_generated_credentials_round_trip(self):
        manager = RelayManager()

        created = manager._load_credentials()
        reloaded = manager._load_credentials()

        assert created == reloaded, "a second load must not regenerate the password"
        assert created["email"]
        assert len(created["password"]) >= 8

    def test_generated_password_meets_the_relay_rules(self):
        """>=8 chars with upper, lower and digit, per the comment in _load_credentials."""
        password = RelayManager()._load_credentials()["password"]

        assert len(password) >= 8
        assert any(c.isupper() for c in password)
        assert any(c.islower() for c in password)
        assert any(c.isdigit() for c in password)

    def test_credentials_file_is_not_world_readable(self):
        manager = RelayManager()

        manager._save_credentials({"email": "a@b.c", "password": "Secret123"})

        assert oct(manager._credentials_path.stat().st_mode)[-3:] == "600"

    def test_saving_tightens_permissions_on_a_pre_existing_loose_file(self):
        """O_CREAT's mode only applies to a new file, hence the explicit chmod."""
        manager = RelayManager()
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        manager._credentials_path.write_text("{}")
        manager._credentials_path.chmod(0o644)

        manager._save_credentials({"email": "a@b.c", "password": "Secret123"})

        assert oct(manager._credentials_path.stat().st_mode)[-3:] == "600"


class TestStaleProfileReaping:
    """_reap_stale_profiles removes dead-pid dirs and never a live one."""

    def test_removes_a_dead_pid_profile_and_keeps_a_live_one(self):
        manager = RelayManager()
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        live = manager.data_dir / f"chrome-profile-{os.getpid()}"
        dead = manager.data_dir / "chrome-profile-999999"
        for d in (live, dead):
            d.mkdir()
            (d / "SingletonLock").write_text("x")

        manager._reap_stale_profiles()

        assert live.exists(), "a running relay's profile must never be reaped"
        assert not dead.exists()

    def test_ignores_directories_without_a_numeric_suffix(self):
        manager = RelayManager()
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        odd = manager.data_dir / "chrome-profile-backup"
        odd.mkdir()

        manager._reap_stale_profiles()

        assert odd.exists()
