"""The reconnect supervisor heals idle drops and stops on intentional close."""

import asyncio
import json

import pytest

from config import settings
from foundry.client import FoundryClient


def _client():
    c = FoundryClient()
    c._supervisor_interval = 0.01
    calls = []

    async def fake_ensure():
        calls.append(1)

    c.ensure_connected = fake_ensure  # type: ignore[assignment]
    return c, calls


@pytest.mark.anyio
async def test_supervisor_heals_idle_drop():
    c, calls = _client()
    # Simulate a socket drop while idle: reader gone, not connected, no _send.
    c._connected = False
    c._reader_task = None
    task = asyncio.create_task(c._supervisor_loop())
    try:
        await asyncio.sleep(0.05)
        assert calls, "supervisor should call ensure_connected on an idle drop"
    finally:
        c._closing = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.anyio
async def test_supervisor_noop_when_healthy():
    c, calls = _client()
    c._connected = True

    class _AliveTask:
        def done(self):
            return False

    c._reader_task = _AliveTask()  # type: ignore[assignment]
    task = asyncio.create_task(c._supervisor_loop())
    try:
        await asyncio.sleep(0.05)
        assert not calls, "supervisor must not reconnect a healthy connection"
    finally:
        c._closing = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.anyio
async def test_supervisor_exits_on_closing():
    c, calls = _client()
    c._connected = False
    c._reader_task = None
    c._closing = True  # intentional disconnect before the loop wakes
    # Should return promptly without ever calling ensure_connected.
    await asyncio.wait_for(c._supervisor_loop(), timeout=1.0)
    assert not calls


@pytest.mark.anyio
async def test_connect_uses_key_loaded_after_client_initialization(monkeypatch):
    """Campaign-gated relay startup must not use the empty constructor key."""
    sent = []

    class FakeSocket:
        async def send(self, payload):
            sent.append(payload)

        async def recv(self):
            return '{"type": "not-connected"}'

        async def close(self):
            pass

    async def fake_connect(_url):
        return FakeSocket()

    monkeypatch.setattr("foundry.client.websockets.connect", fake_connect)
    monkeypatch.setattr(settings, "relay_api_key", "newly-loaded-key")
    client = FoundryClient()
    client.api_key = ""

    assert await client.connect(max_retries=1) is False
    assert json.loads(sent[0])["token"] == "newly-loaded-key"


def test_send_does_not_leak_rpc_futures_when_the_reader_exits():
    """A disconnect mid-request must not leave the request id in _rpc_futures.

    The reader loop fails every pending future on exit; _send used to pop only
    on timeout/cancellation, so the resulting ConnectionError left the entry
    behind for the life of the process.
    """
    import asyncio

    from foundry.client import FoundryClient

    async def run():
        client = FoundryClient()
        client._connected = True

        sent = asyncio.Event()

        class FakeWS:
            async def send(self, _payload):
                sent.set()

        client._ws = FakeWS()

        async def caller():
            with pytest.raises(ConnectionError):
                await client._send("get-actors")

        task = asyncio.create_task(caller())
        await sent.wait()
        assert client._rpc_futures, "request should be pending before the reader exits"

        # Simulate the reader loop's failure path.
        for future in client._rpc_futures.values():
            if not future.done():
                future.set_exception(ConnectionError("Reader loop exited"))
        await task

        assert client._rpc_futures == {}

    asyncio.run(run())
