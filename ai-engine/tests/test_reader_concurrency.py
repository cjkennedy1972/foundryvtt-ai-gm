#!/usr/bin/env python3
"""
Regression tests for the WebSocket reader-loop concurrency fix.

Background: event handlers used to be awaited *inline* in the single reader
loop. A handler (e.g. the chat handler) often issues its own relay RPCs and
awaits their replies — but those replies are only routed by the same reader
loop. Running handlers inline therefore deadlocked the handler's own RPCs,
which surfaced in play-testing as "player chat ignored" / relay hangs.

These tests inject a fake relay (no live relay, no Foundry, no LLM) and assert:
  1. A handler that issues an RPC and awaits its reply completes — proving the
     reader stays free to route replies while a handler runs.
  2. Two events on the same channel are processed one-at-a-time — proving the
     worker serialises a channel so two turns can't overlap.

A regression reintroduces the deadlock, so each scenario is wrapped in a hard
timeout: the test then FAILS (instead of hanging the suite).

Run:
    cd ai-engine && python -m pytest tests/test_reader_concurrency.py -v
  or standalone:
    cd ai-engine && python tests/test_reader_concurrency.py
"""

import asyncio
import json
import os
import sys

# Force the lazy `websockets.exceptions` submodule to load. The reader loop's
# `except websockets.exceptions.ConnectionClosed` clause resolves it only when
# matching an exception; production always calls connect() first (which loads
# it), but these tests inject a fake socket and bypass connect().
import websockets.exceptions  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient


class FakeRelay:
    """Minimal stand-in for the relay's WebSocket.

    recv() yields frames the engine should read (events + RPC replies).
    send() records outgoing frames and, for any request carrying a requestId,
    queues a matching reply — exactly as the real relay would, as a *later*
    frame the reader must read to unblock the caller.
    """

    def __init__(self):
        self._to_engine: asyncio.Queue = asyncio.Queue()
        self.sent: list = []

    async def recv(self):
        return await self._to_engine.get()

    async def send(self, data):
        payload = json.loads(data)
        self.sent.append(payload)
        rid = payload.get("requestId")
        if rid:
            reply = {"requestId": rid, "type": "ok", "result": {}}
            self._to_engine.put_nowait(json.dumps(reply))

    async def close(self):
        pass

    def push_event(self, event: dict):
        self._to_engine.put_nowait(json.dumps(event))


def _attach(client: FoundryClient, fake: FakeRelay):
    """Wire a fake relay in without going through connect()."""
    client._ws = fake
    client._connected = True
    client._reader_task = asyncio.create_task(client._reader_loop())
    client._event_worker_task = asyncio.create_task(client._event_worker())


async def _teardown(client: FoundryClient):
    for task in (client._reader_task, client._event_worker_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# Scenario 1: a handler-issued RPC must not deadlock the reader.
# ---------------------------------------------------------------------------
async def _scenario_handler_rpc():
    client = FoundryClient()
    fake = FakeRelay()
    handler_done = asyncio.Event()
    outcome = {}

    async def chat_handler(data):
        # This is the shape of the real bug: the handler issues a relay RPC
        # (chat-send) and awaits its reply, which only the reader can deliver.
        try:
            await client.chat_message("the GM responds", speaker="GM")
            outcome["ok"] = True
        except Exception as e:  # timeout/ConnectionError on regression
            outcome["ok"] = False
            outcome["error"] = repr(e)
        finally:
            handler_done.set()

    client.subscribe("chat-events", chat_handler)
    _attach(client, fake)

    fake.push_event({"type": "chat-event", "content": "hello", "speaker": "Pat"})

    try:
        await asyncio.wait_for(handler_done.wait(), timeout=5)
    except asyncio.TimeoutError:
        await _teardown(client)
        raise AssertionError(
            "handler-issued RPC never completed — reader loop is blocked "
            "(deadlock regression)"
        )

    await _teardown(client)
    assert outcome.get("ok"), f"handler RPC failed: {outcome.get('error')}"
    # The engine actually sent the chat-send to the relay.
    assert any(p.get("type") == "chat-send" for p in fake.sent), \
        "engine never sent the chat-send RPC"


def test_handler_rpc_not_blocked():
    asyncio.run(_scenario_handler_rpc())


# ---------------------------------------------------------------------------
# Scenario 2: two events on one channel are processed one-at-a-time.
# ---------------------------------------------------------------------------
async def _scenario_serialized():
    client = FoundryClient()
    fake = FakeRelay()
    active = 0
    max_active = 0
    processed = 0
    both_done = asyncio.Event()

    async def slow_handler(data):
        nonlocal active, max_active, processed
        active += 1
        max_active = max(max_active, active)
        # Yield repeatedly so a second concurrent handler would be observed.
        for _ in range(5):
            await asyncio.sleep(0)
        active -= 1
        processed += 1
        if processed == 2:
            both_done.set()

    client.subscribe("scene-events", slow_handler)
    _attach(client, fake)

    fake.push_event({"type": "scene-event", "sceneName": "A"})
    fake.push_event({"type": "scene-event", "sceneName": "B"})

    try:
        await asyncio.wait_for(both_done.wait(), timeout=5)
    except asyncio.TimeoutError:
        await _teardown(client)
        raise AssertionError("events were not both processed")

    await _teardown(client)
    assert max_active == 1, f"handlers overlapped (max concurrent = {max_active})"
    assert processed == 2


def test_channel_handlers_serialized():
    asyncio.run(_scenario_serialized())


if __name__ == "__main__":
    test_handler_rpc_not_blocked()
    print("PASS  handler RPC not blocked by reader loop")
    test_channel_handlers_serialized()
    print("PASS  channel handlers serialized")
    print("All reader-concurrency tests passed.")
