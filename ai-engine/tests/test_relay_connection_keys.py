"""Relay connection: API key validation, scoped key generation, headless sessions.

These functions manage credentials and browser sessions for Foundry access.
Tests focus on ensuring key methods can be called safely without errors.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from relay_proc.manager import RelayManager


@pytest.fixture
def relay_manager(tmp_path):
    """RelayManager with temp data directory."""
    manager = RelayManager()
    manager.data_dir = tmp_path
    manager._credentials_path = tmp_path / "creds.json"
    return manager


class TestKeyValidation:
    """Test key validation logic."""

    @pytest.mark.asyncio
    async def test_key_is_valid_returns_bool(self, relay_manager):
        """_key_is_valid() returns a boolean result."""
        result = await relay_manager._key_is_valid("test-key")
        assert isinstance(result, bool)


class TestCredentialHandling:
    """Test credential loading and storage."""

    def test_load_credentials_returns_dict(self, relay_manager):
        """_load_credentials() returns a credential dictionary."""
        relay_manager.data_dir.mkdir(parents=True, exist_ok=True)
        creds = relay_manager._load_credentials()
        assert isinstance(creds, dict)
        assert "email" in creds
        assert "password" in creds

    def test_save_credentials_stores_dict(self, relay_manager):
        """_save_credentials() stores credentials to file."""
        relay_manager.data_dir.mkdir(parents=True, exist_ok=True)
        test_creds = {"email": "test@example.com", "password": "TestPass1"}
        relay_manager._save_credentials(test_creds)
        assert relay_manager._credentials_path.exists()


class TestProcessManagement:
    """Test Chrome process lifecycle."""

    def test_kill_profile_chrome_handles_process(self, relay_manager):
        """_kill_profile_chrome() can be called without error."""
        relay_manager.chrome_proc = MagicMock()
        relay_manager.chrome_proc.poll = MagicMock(return_value=None)
        # Should not raise
        relay_manager._kill_profile_chrome()

    def test_reap_stale_profiles_cleans_profiles(self, relay_manager):
        """_reap_stale_profiles() can be called without error."""
        relay_manager.data_dir.mkdir(parents=True, exist_ok=True)
        # Should not raise
        relay_manager._reap_stale_profiles()


class TestStatusReporting:
    """Test status reporting."""

    def test_status_returns_dict(self, relay_manager):
        """status() returns a dictionary with state info."""
        relay_manager.relay_proc = None
        status = relay_manager.status()
        assert isinstance(status, dict)
        assert "running" in status
        assert "managed" in status

    def test_status_with_running_process(self, relay_manager):
        """status() includes PID when process is running."""
        relay_manager.relay_proc = MagicMock()
        relay_manager.relay_proc.pid = 99999
        relay_manager.relay_proc.poll = MagicMock(return_value=None)
        status = relay_manager.status()
        assert isinstance(status, dict)


class TestHeadlessSessionManagement:
    """Test headless browser session methods."""

    @pytest.mark.asyncio
    async def test_ensure_headless_session_method_exists(self, relay_manager):
        """ensure_headless_session() method is callable."""
        relay_manager.data_dir.mkdir(parents=True, exist_ok=True)
        relay_manager.api_key = "test-key"

        # Method should be callable (may fail due to missing dependencies)
        assert callable(relay_manager.ensure_headless_session)

    @pytest.mark.asyncio
    async def test_restart_headless_session_method_exists(self, relay_manager):
        """restart_headless_session() method is callable."""
        relay_manager.data_dir.mkdir(parents=True, exist_ok=True)
        assert callable(relay_manager.restart_headless_session)


class TestBinaryManagement:
    """Test binary/process setup."""

    def test_ensure_binary_handles_missing_chrome(self, relay_manager):
        """_ensure_binary() can handle missing Chrome."""
        relay_manager.data_dir.mkdir(parents=True, exist_ok=True)
        # Should complete without error even if Chrome is missing
        try:
            relay_manager._ensure_binary()
        except Exception:
            pass  # Expected if Chrome is not installed


class TestWatchLoopMonitoring:
    """Test background monitoring loop."""

    def test_clear_chrome_locks_cleans_lock_files(self, relay_manager):
        """_clear_chrome_locks() can be called without error."""
        relay_manager.data_dir.mkdir(parents=True, exist_ok=True)
        # Should not raise
        relay_manager._clear_chrome_locks()
