#!/usr/bin/env python3
"""Reconnection, against a relay that speaks the real protocol.

ensure_connected, _await_reconnect and cancel_all_background_tasks were
almost entirely uncovered. They decide what happens when the relay drops
mid-session, which the live stack does on its own — the headless Foundry
session expired three times while probing, each time closing the socket with
4002 "No connected Foundry client found".

_await_reconnect exists because failing fast there leaves scenes half built:
the WS drops during a scene switch and every queued action fails for the ten
seconds the reconnect takes, so no walls, no lights, no player token.

Run:
    cd ai-engine && python -m pytest tests/test_client_reconnect.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from fake_relay import FakeRelay
from foundry.client import FoundryClient


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "relay_api_key", "valid-token")
    monkeypatch.setattr(settings, "relay_headless_client_id", "test-client", raising=False)


async def _connected(relay):
    c = FoundryClient()
    c.ws_url = relay.ws_url
    c.api_key = "valid-token"
    await c.connect(max_retries=1)
    return c


# ── ensure_connected ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_healthy_connection_is_left_alone():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            await c.ensure_connected()
            # The reconnect is spawned, not awaited, so give it a turn to run
            # before concluding it did not happen.
            await asyncio.sleep(0.3)

            assert relay.connections == 1, "a healthy socket should not be replaced"
            assert c.is_connected
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_a_reconnect_already_running_is_not_started_twice():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            # Count spawns rather than surviving tasks: _reconnect returns at
            # once on the same flag, so the task is finished and discarded from
            # the set before any assertion here could see it.
            spawned = []
            original = c._spawn_background_task
            c._spawn_background_task = lambda coro: (spawned.append(1), original(coro))[1]

            c._reconnecting = True
            c._connected = False

            await c.ensure_connected()
            await asyncio.sleep(0.3)

            assert spawned == [], "a redundant reconnect was spawned"
            assert relay.connections == 1
        finally:
            c._reconnecting = False
            await c.disconnect()


@pytest.mark.asyncio
async def test_a_crashed_reader_is_cleared_and_a_reconnect_begins():
    """The reader task finishing while _connected is still True is the shape a
    dropped socket leaves behind."""
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            await c._ws.close()
            await asyncio.sleep(0.1)

            await c.ensure_connected()
            for _ in range(40):
                if relay.connections > 1:
                    break
                await asyncio.sleep(0.05)

            assert relay.connections > 1, "no reconnect was attempted"
        finally:
            await c.disconnect()


# ── _await_reconnect ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_waiting_returns_once_the_socket_is_back():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            await c._ws.close()
            await asyncio.sleep(0.1)

            await asyncio.wait_for(c._await_reconnect(wait=10.0), timeout=15)

            assert c.is_connected
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_waiting_gives_up_when_the_relay_stays_down():
    c = FoundryClient()
    c.ws_url = "ws://127.0.0.1:1/ws/api"
    c.api_key = "valid-token"

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(c._await_reconnect(wait=0.5), timeout=20)


@pytest.mark.asyncio
async def test_a_deliberate_shutdown_fails_immediately_rather_than_waiting():
    """Closing down is not a hiccup; waiting 15s for it would stall shutdown."""
    c = FoundryClient()
    c._closing = True

    started = asyncio.get_event_loop().time()
    with pytest.raises(ConnectionError):
        await c._await_reconnect(wait=15.0)

    assert asyncio.get_event_loop().time() - started < 1.0
    # And it does not go looking for a socket on the way out: the loop below
    # would also raise on _closing, so the elapsed time alone cannot tell the
    # early return from its absence.
    assert not c._background_tasks, "shutdown still tried to reconnect"


# ── background tasks ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancelling_background_tasks_leaves_none_running():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            async def forever():
                await asyncio.sleep(3600)

            c._spawn_background_task(forever())
            c._spawn_background_task(forever())
            await asyncio.sleep(0.05)

            await c.cancel_all_background_tasks()

            assert all(t.done() for t in c._background_tasks), "a task survived"
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_cancelling_is_safe_when_nothing_is_running():
    c = FoundryClient()

    await c.cancel_all_background_tasks()

    assert c._background_tasks == set() or all(t.done() for t in c._background_tasks)


@pytest.mark.asyncio
async def test_disconnect_stops_the_background_tasks_too():
    async with FakeRelay() as relay:
        c = await _connected(relay)

        async def forever():
            await asyncio.sleep(3600)

        c._spawn_background_task(forever())
        await asyncio.sleep(0.05)

        await c.disconnect()

        assert all(t.done() for t in c._background_tasks)
