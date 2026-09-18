"""FoundryClient._send: the RPC contract every Foundry call goes through.

The relay signals failure two ways, and the second one used to sail through
as success: {"type": "<msg>-result", "error": "..."} means the relay's socket
to us is fine but its downstream Foundry client is dead. Treating that as a
successful reply left is_connected True forever, so the self-heal reconnect
never fired.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from foundry.client import FoundryClient


def _client():
    c = FoundryClient()
    c._ws = AsyncMock()
    c._connected = True
    return c


async def _reply(client, payload, msg_type="test"):
    """Run _send and resolve its pending future with `payload`."""
    task = asyncio.create_task(client._send(msg_type))
    for _ in range(50):
        await asyncio.sleep(0)
        if client._rpc_futures:
            break
    request_id = next(iter(client._rpc_futures))
    client._rpc_futures[request_id].set_result(payload)
    return await task


@pytest.mark.asyncio
async def test_a_successful_reply_is_returned():
    client = _client()

    result = await _reply(client, {"type": "test-result", "data": {"ok": True}})

    assert result["data"] == {"ok": True}


@pytest.mark.asyncio
async def test_the_payload_is_flat_with_no_params_nesting():
    """The relay reads type/requestId at the top level."""
    client = _client()

    task = asyncio.create_task(client._send("roll", formula="1d20"))
    for _ in range(50):
        await asyncio.sleep(0)
        if client._rpc_futures:
            break
    sent = json.loads(client._ws.send.await_args.args[0])
    next(iter(client._rpc_futures.values())).set_result({"ok": True})
    await task

    assert sent["type"] == "roll"
    assert sent["formula"] == "1d20"
    assert "params" not in sent


@pytest.mark.asyncio
async def test_a_protocol_error_reply_raises():
    client = _client()

    with pytest.raises(Exception):
        await _reply(client, {"type": "error", "error": "bad request"})


@pytest.mark.asyncio
async def test_a_dead_foundry_client_marks_us_disconnected():
    """This is what lets the self-heal relaunch fire."""
    client = _client()

    with pytest.raises(Exception):
        await _reply(client, {
            "type": "test-result",
            "error": "Foundry client is no longer connected",
        })

    assert client._connected is False, (
        "staying connected here means the reconnect loop never relaunches the "
        "headless session"
    )


@pytest.mark.asyncio
async def test_an_unrelated_error_does_not_mark_us_disconnected():
    """Only a dead downstream client should trigger the relaunch path."""
    client = _client()

    with pytest.raises(Exception):
        await _reply(client, {"type": "test-result", "error": "scene not found"})

    assert client._connected is True


@pytest.mark.asyncio
async def test_a_timeout_raises_connection_error_and_frees_the_slot():
    client = _client()

    with pytest.raises(ConnectionError, match="timed out"):
        await client._send("test", _timeout=0.01)

    assert client._rpc_futures == {}, "a leaked entry grows the map forever"


@pytest.mark.asyncio
async def test_cancellation_propagates_rather_than_becoming_a_connection_error():
    """Swallowing it makes _send_with_retry loop instead of exiting at shutdown."""
    client = _client()

    task = asyncio.create_task(client._send("test"))
    for _ in range(50):
        await asyncio.sleep(0)
        if client._rpc_futures:
            break
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_the_request_slot_is_freed_on_every_path():
    client = _client()

    await _reply(client, {"ok": True})

    assert client._rpc_futures == {}


class TestReconnectSelfHeal:
    """_reconnect owns the only path that recovers a dead headless session.

    A plain reconnect cannot fix a relay whose Foundry client has gone: the
    socket to the relay comes back fine and the world is still unreachable.
    The relaunch hook is what breaks that loop, and the cooldown is what stops
    it becoming a relaunch storm.
    """

    def _client(self, connect_ok=False, error="No connected Foundry client"):
        c = FoundryClient()
        c._reconnecting = False
        c._last_connect_error = error
        c._last_relaunch_at = 0.0
        c.connect = AsyncMock(return_value=connect_ok)
        c._relaunch_headless = AsyncMock()
        return c

    @pytest.mark.asyncio
    async def test_a_concurrent_call_does_not_start_a_second_reconnect(self):
        client = self._client()
        client._reconnecting = True

        await client._reconnect()

        client.connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_successful_reconnect_does_not_relaunch(self):
        client = self._client(connect_ok=True)

        await client._reconnect()

        client._relaunch_headless.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_dead_foundry_client_triggers_the_relaunch(self):
        client = self._client(connect_ok=False)

        await client._reconnect()

        client._relaunch_headless.assert_awaited_once()
        assert client.connect.await_count == 2, "it should retry after relaunching"

    @pytest.mark.asyncio
    async def test_an_unrelated_failure_does_not_relaunch(self):
        """Relaunching Chrome for a network blip would be a heavy false positive."""
        client = self._client(connect_ok=False, error="connection refused")

        await client._reconnect()

        client._relaunch_headless.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_cooldown_prevents_a_relaunch_storm(self):
        client = self._client(connect_ok=False)
        client._last_relaunch_at = asyncio.get_event_loop().time()

        await client._reconnect()

        client._relaunch_headless.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failing_relaunch_is_absorbed_and_clears_the_flag(self):
        """Leaving _reconnecting True would block every later attempt."""
        client = self._client(connect_ok=False)
        client._relaunch_headless.side_effect = RuntimeError("Chrome will not start")

        await client._reconnect()

        assert client._reconnecting is False

    @pytest.mark.asyncio
    async def test_the_flag_is_cleared_after_a_normal_reconnect(self):
        client = self._client(connect_ok=True)

        await client._reconnect()

        assert client._reconnecting is False
