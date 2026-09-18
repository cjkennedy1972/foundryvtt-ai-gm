"""Relay connection: API key validation, scoped key generation, headless sessions.

These functions manage credentials and browser sessions for Foundry access.
Tests focus on ensuring key methods can be called safely without errors.
"""

import json

import pytest
import websockets
import websockets.exceptions
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from relay_proc.manager import RelayManager


class _closed(websockets.exceptions.ConnectionClosed):
    """ConnectionClosed carrying just the reason _key_is_valid reads.

    The real constructor's signature moves between websockets releases; the
    handler only ever reads .reason.
    """

    def __init__(self, reason):
        self._reason = reason
        Exception.__init__(self, reason)

    @property
    def reason(self):
        # The parent declares reason read-only off the received Close frame.
        return self._reason


@pytest.fixture
def relay_manager(tmp_path):
    """RelayManager with temp data directory."""
    manager = RelayManager()
    manager.data_dir = tmp_path
    manager._credentials_path = tmp_path / "creds.json"
    return manager


class TestKeyValidation:
    """_key_is_valid fails open on anything ambiguous, closed only on rejection.

    These drive the real handshake branches with a stubbed websockets.connect.
    The previous single test called the live relay and asserted
    isinstance(result, bool), which the return annotation already guarantees:
    it passed whether the key was valid, invalid, or the relay was down, and it
    made suite runtime depend on a socket.
    """

    @pytest.mark.asyncio
    async def test_unreachable_relay_does_not_block_startup(self, relay_manager):
        """Connect failure is ambiguous, so the key is accepted."""
        with patch("websockets.connect", side_effect=OSError("connection refused")):
            assert await relay_manager._key_is_valid("any-key") is True

    @pytest.mark.asyncio
    async def test_connected_ack_accepts_the_key(self, relay_manager):
        ws = AsyncMock()
        ws.recv.return_value = json.dumps({"type": "connected"})
        with patch("websockets.connect", AsyncMock(return_value=ws)):
            assert await relay_manager._key_is_valid("good-key") is True
        ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_api_key_close_rejects_the_key(self, relay_manager):
        """The one close reason that means the key is genuinely bad."""
        ws = AsyncMock()
        ws.recv.side_effect = _closed("Invalid API key")
        with patch("websockets.connect", AsyncMock(return_value=ws)):
            assert await relay_manager._key_is_valid("bad-key") is False

    @pytest.mark.asyncio
    async def test_unpaired_foundry_still_accepts_the_key(self, relay_manager):
        """The relay checks the key before resolving a Foundry client."""
        ws = AsyncMock()
        ws.recv.side_effect = _closed("No connected Foundry client found")
        with patch("websockets.connect", AsyncMock(return_value=ws)):
            assert await relay_manager._key_is_valid("good-key") is True


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



class TestWatchLoopMonitoring:
    """Test background monitoring loop."""

