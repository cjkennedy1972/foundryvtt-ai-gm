"""The reconnect supervisor heals idle drops and stops on intentional close."""

import asyncio

import pytest

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
