"""Connection lifecycle: connect, disconnect, reconnect, and error recovery.

These are the critical paths: if connection handling breaks, every action fails.
Tests cover:
1. Successful connection with API key validation
2. Connection failure and retry logic
3. Graceful disconnect and cleanup
4. Reconnection after transient failures
5. Background task management
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from foundry.client import FoundryClient


@pytest.fixture
def mock_client():
    """FoundryClient with all async I/O mocked."""
    client = FoundryClient()
    # Mock all async methods to prevent real WebSocket connections
    client._send = AsyncMock()
    client.ensure_connected = AsyncMock()
    client._reader_loop = AsyncMock()
    return client


class TestConnectionEstablishment:
    """Test initial connection and validation."""

    @pytest.mark.asyncio
    async def test_connect_success_with_valid_api_key(self, mock_client):
        """connect() with valid API key and relay response → connected."""
        # Mock the WebSocket connection
        mock_socket = AsyncMock()
        mock_socket.recv = AsyncMock(return_value='{"type": "connected", "api_key": "key123"}')

        with patch("foundry.client.websockets.connect", new_callable=AsyncMock, return_value=mock_socket):
            mock_client.api_key = "test-key"
            result = await mock_client.connect(max_retries=1)

            # Should be connected and _send should have been called with auth frame
            assert result is True or mock_client._connected
            assert mock_client._send.called or mock_socket.send.called

    @pytest.mark.asyncio
    async def test_connect_retries_on_transient_failure(self, mock_client):
        """connect() with transient error → retries and recovers."""
        call_count = 0

        async def failing_connect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionRefusedError("Relay not ready")
            mock_socket = AsyncMock()
            mock_socket.recv = AsyncMock(return_value='{"type": "connected"}')
            return mock_socket

        with patch("foundry.client.websockets.connect", side_effect=failing_connect):
            mock_client.api_key = "test-key"
            result = await mock_client.connect(max_retries=2)

            assert call_count == 2, "Should have retried after first failure"

    @pytest.mark.asyncio
    async def test_connect_fails_after_max_retries(self, mock_client):
        """connect() exhausting retries → returns False."""
        async def always_fail(*args, **kwargs):
            raise ConnectionRefusedError("Relay down")

        with patch("foundry.client.websockets.connect", side_effect=always_fail):
            mock_client.api_key = "test-key"
            result = await mock_client.connect(max_retries=2)

            assert result is False, "Should fail after exhausting retries"


class TestDisconnection:
    """Test graceful disconnection and cleanup."""

    @pytest.mark.asyncio
    async def test_disconnect_closes_socket_and_stops_tasks(self, mock_client):
        """disconnect() closes socket and cancels background tasks."""
        # Simulate a connected client
        mock_socket = AsyncMock()
        mock_client._socket = mock_socket
        mock_client._connected = True
        mock_client._closing = False
        mock_client._reader_task = asyncio.create_task(asyncio.sleep(1))

        try:
            await mock_client.disconnect()

            assert mock_socket.close.called or mock_client._closing
            # Task should be cancelled
            await asyncio.sleep(0.1)
            assert not mock_client._reader_task or mock_client._reader_task.cancelled()
        finally:
            if mock_client._reader_task and not mock_client._reader_task.done():
                mock_client._reader_task.cancel()

    @pytest.mark.asyncio
    async def test_disconnect_noop_when_not_connected(self, mock_client):
        """disconnect() when not connected → no error."""
        mock_client._connected = False
        mock_client._socket = None

        # Should not raise
        await mock_client.disconnect()
        assert mock_client._connected is False


class TestCancelBackgroundTasks:
    """Test background task cleanup."""

    @pytest.mark.asyncio
    async def test_cancel_all_background_tasks_cancels_tracked_tasks(self, mock_client):
        """cancel_all_background_tasks() cancels tasks in _background_tasks."""
        # Create a mock task that can be cancelled
        async def long_running():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(long_running())
        mock_client._background_tasks = [task]

        await mock_client.cancel_all_background_tasks()

        # Task should be cancelled after calling cancel_all_background_tasks
        await asyncio.sleep(0.05)
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_cancel_all_background_tasks_empty_list(self, mock_client):
        """Idempotent with nothing tracked, and it must actually cancel what is."""
        mock_client._background_tasks = []

        await mock_client.cancel_all_background_tasks()
        await mock_client.cancel_all_background_tasks()

        assert mock_client._background_tasks == []

    @pytest.mark.asyncio
    async def test_cancel_all_background_tasks_cancels_a_live_task(self):
        """The empty case alone would pass against a method that did nothing."""
        import asyncio

        from foundry.client import FoundryClient

        client = FoundryClient()

        async def never_finishes():
            await asyncio.sleep(3600)

        task = asyncio.create_task(never_finishes())
        client._background_tasks = [task]

        await client.cancel_all_background_tasks()

        assert task.cancelled() or task.done()


class TestUpdateActor:
    """update_actor resolves a name through four strategies, then updates by uuid."""

    @pytest.mark.asyncio
    async def test_resolves_via_scene_token_and_updates_by_uuid(self, mock_client):
        mock_client.get_scenes = AsyncMock(return_value=[{"name": "Crypt"}])
        mock_client.get_scene_tokens = AsyncMock(
            return_value=[{"name": "Goblin", "actorUuid": "Actor.g1"}]
        )
        mock_client.update_entity = AsyncMock(return_value={"ok": True})

        result = await FoundryClient.update_actor(
            mock_client, "goblin", {"img": "portrait.png"}
        )

        assert result == {"ok": True}
        mock_client.update_entity.assert_awaited_once_with(
            uuid="Actor.g1", data={"img": "portrait.png"}
        )

    @pytest.mark.asyncio
    async def test_falls_through_to_world_actors_when_no_token_matches(self, mock_client):
        mock_client.get_scenes = AsyncMock(return_value=[])
        mock_client.get_scene_tokens = AsyncMock(return_value=[])
        mock_client._send = AsyncMock(return_value={"results": []})
        mock_client.get_actors = AsyncMock(
            return_value=[{"name": "Goblin", "uuid": "Actor.g2"}]
        )
        mock_client.update_entity = AsyncMock(return_value={"ok": True})

        await FoundryClient.update_actor(mock_client, "Goblin", {"img": "p.png"})

        mock_client.update_entity.assert_awaited_once_with(
            uuid="Actor.g2", data={"img": "p.png"}
        )

    @pytest.mark.asyncio
    async def test_returns_none_and_updates_nothing_when_unresolvable(self, mock_client):
        mock_client.get_scenes = AsyncMock(return_value=[])
        mock_client.get_scene_tokens = AsyncMock(return_value=[])
        mock_client._send = AsyncMock(return_value={"results": []})
        mock_client.get_actors = AsyncMock(return_value=[])
        mock_client.update_entity = AsyncMock()

        result = await FoundryClient.update_actor(mock_client, "Nobody", {"img": "p.png"})

        assert result is None
        mock_client.update_entity.assert_not_awaited()


class TestAddonCapabilities:
    """discover_addon_capabilities summarises a scan; pure over its input."""

    @pytest.mark.asyncio
    async def test_counts_and_classifies_what_the_world_offers(self, mock_client):
        scan = {
            "scenes": [{"fogOfWar": True}, {"timedLights": True}, {}],
            "actors": [{"hp": 7}, {"hp": "?"}, {"hp": None}],
            "items": [{"name": "Sword"}],
            "journal": [{"name": "Lore"}],
            "quests": [{"active": True}, {"active": False}],
            "world": {"systems": [{"name": "Combat Utility Belt", "version": "1.0", "active": True}]},
        }

        caps = await FoundryClient.discover_addon_capabilities(mock_client, scan)

        assert caps["available_maps"] == 3
        assert caps["scenes_with_fog"] == 1
        assert caps["scenes_with_lighting"] == 1
        assert caps["total_actors"] == 3
        assert caps["actors_with_combat"] == 1, "'?' and None are not combat-ready HP"
        assert caps["total_items"] == 1
        assert caps["total_journal_entries"] == 1
        assert caps["active_encounters"] == 1
        assert caps["modules"][0]["name"] == "Combat Utility Belt"
        assert any("Combat Tracker" in s for s in caps["suggestions"])

    @pytest.mark.asyncio
    async def test_an_empty_world_yields_zeroes_not_errors(self, mock_client):
        caps = await FoundryClient.discover_addon_capabilities(mock_client, {})

        assert caps["available_maps"] == 0
        assert caps["total_actors"] == 0
        assert caps["modules"] == []
