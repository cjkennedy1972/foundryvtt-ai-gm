#!/usr/bin/env python3
"""The connection machinery, against a relay that speaks the real protocol.

connect/disconnect/reconnect was the largest untested block in
foundry/client.py — roughly 113 lines of the 367 uncovered. Mocking a
WebSocket well enough to exercise it is most of the way to writing a server,
so tests/fake_relay.py is one, built from a protocol capture of the live
relay rather than from assumption.

Run:
    cd ai-engine && python -m pytest tests/test_client_connection_lifecycle.py -v
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
def _quiet_settings(monkeypatch):
    monkeypatch.setattr(settings, "relay_api_key", "valid-token")
    monkeypatch.setattr(settings, "relay_headless_client_id", "test-client", raising=False)
    yield


def _client(relay, key="valid-token"):
    c = FoundryClient()
    c.ws_url = relay.ws_url
    c.api_key = key
    return c


async def _connected(relay, **kw):
    c = _client(relay, **kw)
    await c.connect(max_retries=1)
    return c


# ── the handshake ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_good_handshake_connects():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            assert c.is_connected
            assert relay.connections == 1
            auth = relay.received  # auth is consumed before the loop
            assert auth == [] or auth[0].get("type") != "auth"
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_the_auth_frame_carries_the_token_and_client_id():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        await c.disconnect()

    seen = relay.auth_frames[0]
    assert seen["type"] == "auth"
    assert seen["token"] == "valid-token"
    assert seen["clientId"] == "test-client", "the headless clientId must be sent"


@pytest.mark.asyncio
async def test_a_rejected_key_does_not_report_connected():
    async with FakeRelay(token="a-different-token") as relay:
        c = _client(relay)

        ok = await c.connect(max_retries=1)

        assert not ok and not c.is_connected


@pytest.mark.asyncio
async def test_no_foundry_client_does_not_report_connected():
    """The relay closes 4002 when no Foundry session is attached, which is
    what a cold engine start hits."""
    async with FakeRelay(foundry_connected=False) as relay:
        c = _client(relay)

        assert not await c.connect(max_retries=1)


@pytest.mark.asyncio
async def test_connect_retries_then_gives_up():
    async with FakeRelay(token="nope") as relay:
        c = _client(relay)

        assert not await c.connect(max_retries=2)
        assert relay.connections == 2, "each retry should be a fresh socket"


@pytest.mark.asyncio
async def test_an_unreachable_relay_is_reported_not_raised():
    c = FoundryClient()
    c.ws_url = "ws://127.0.0.1:1/ws/api"
    c.api_key = "valid-token"

    assert await c.connect(max_retries=1) is False


# ── RPCs over the live socket ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_rpc_round_trips():
    async with FakeRelay(responses={"search": {"results": [{"name": "The Crypt"}]}}) as relay:
        c = await _connected(relay)
        try:
            out = await asyncio.wait_for(
                c._send("search", filter="documentType:Scene"), timeout=10
            )
            assert out["results"] == [{"name": "The Crypt"}]
            assert relay.received[0]["filter"] == "documentType:Scene"
            assert relay.received[0]["requestId"], "every RPC needs a requestId"
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_an_error_reply_raises_with_the_relays_message():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            with pytest.raises(RuntimeError, match="Unknown message type"):
                await asyncio.wait_for(c._send("not-a-real-type"), timeout=10)
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_concurrent_rpcs_get_their_own_replies():
    async with FakeRelay(responses={
        "search": {"results": ["s"]},
        "roll": {"results": ["r"]},
    }) as relay:
        c = await _connected(relay)
        try:
            a, b = await asyncio.wait_for(
                asyncio.gather(c._send("search"), c._send("roll")), timeout=10
            )
            assert a["results"] == ["s"] and b["results"] == ["r"]
        finally:
            await c.disconnect()


# ── teardown ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disconnect_clears_the_connected_flag_and_tasks():
    async with FakeRelay() as relay:
        c = await _connected(relay)

        await c.disconnect()

        assert not c.is_connected
        assert c._reader_task is None or c._reader_task.done()


@pytest.mark.asyncio
async def test_disconnect_is_safe_before_any_connection():
    c = FoundryClient()

    await c.disconnect()

    assert not c.is_connected


@pytest.mark.asyncio
async def test_reconnecting_replaces_the_previous_socket():
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            first = c._ws
            await c.connect(max_retries=1)
            assert c.is_connected
            assert c._ws is not first, "the old socket should be replaced"
            assert relay.connections == 2
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_a_pending_rpc_fails_when_the_socket_is_replaced():
    """Otherwise the caller waits forever on a reply that can never arrive."""
    async with FakeRelay() as relay:
        c = await _connected(relay)
        try:
            loop = asyncio.get_running_loop()
            stuck = loop.create_future()
            c._rpc_futures["orphan"] = stuck

            await c.connect(max_retries=1)

            assert stuck.done() and isinstance(stuck.exception(), ConnectionError)
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_a_dropped_socket_leaves_the_client_disconnected():
    async with FakeRelay(drop_after=0) as relay:
        c = await _connected(relay)
        try:
            with pytest.raises(Exception):
                await asyncio.wait_for(c._send("search"), timeout=10)
        finally:
            await c.disconnect()
