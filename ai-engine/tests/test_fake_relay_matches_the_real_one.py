#!/usr/bin/env python3
"""Keeps tests/fake_relay.py honest against the relay it imitates.

A test double is only worth what its fidelity is worth. Three of this
suite's doubles were wrong in ways nothing noticed: MockDatabase had no
save_state at all, the e2e harness passed a GameState where a Database
belonged, and a mocked generate_map returned None where the real one is
typed to return a dict.

fake_relay's protocol constants were captured from a live relay. This checks
them against one when LIVE_RELAY_WS is set — the nightly-e2e job has a stack
up on the same host — and skips otherwise, because a developer laptop has no
relay.

    LIVE_RELAY_WS=ws://host:3010/ws/api LIVE_RELAY_KEY=... \
        python -m pytest tests/test_fake_relay_matches_the_real_one.py -v

The close-reason checks need only a reachable relay. The two ack-shape checks
also need LIVE_RELAY_CLIENT_ID, because the relay answers a handshake only
once it has a paired Foundry session — the nightly job sets the first pair and
not the third, so those two skip there and run when a world is attached.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fake_relay

LIVE_WS = os.environ.get("LIVE_RELAY_WS")
LIVE_KEY = os.environ.get("LIVE_RELAY_KEY", "")
LIVE_CLIENT = os.environ.get("LIVE_RELAY_CLIENT_ID", "")

live_only = pytest.mark.skipif(
    not LIVE_WS, reason="set LIVE_RELAY_WS to check the double against a real relay"
)

# The relay only acks once it has resolved a paired Foundry client; without one
# it closes 4002 "No connected Foundry client found". So the ack-shape checks
# need a world attached, and the close-reason checks do not. Splitting them
# means the half that needs no setup runs wherever a relay exists.
needs_session = pytest.mark.skipif(
    not (LIVE_WS and LIVE_CLIENT),
    reason="set LIVE_RELAY_CLIENT_ID too: the ack only comes with a paired Foundry session",
)


async def _handshake(token, client_id=None):
    """Return ("ack", payload) or ("closed", (code, reason))."""
    import websockets

    ws = await websockets.connect(LIVE_WS)
    msg = {"type": "auth", "token": token}
    if client_id:
        msg["clientId"] = client_id
    await ws.send(json.dumps(msg))
    try:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        await ws.close()
        return "ack", ack
    except websockets.ConnectionClosed as e:
        return "closed", (e.code, e.reason)


@needs_session
def test_the_ack_shape_still_matches():
    kind, payload = asyncio.run(_handshake(LIVE_KEY, LIVE_CLIENT))

    assert kind == "ack", f"live relay rejected the probe: {payload}"
    assert payload["type"] == fake_relay.ACK_TYPE
    assert set(payload) >= {"type", "clientId", "eventChannels", "supportedTypes"}
    assert payload["eventChannels"] == fake_relay.EVENT_CHANNELS


@needs_session
def test_every_type_the_double_claims_is_one_the_relay_supports():
    kind, payload = asyncio.run(_handshake(LIVE_KEY, LIVE_CLIENT))
    assert kind == "ack", payload

    unknown = set(fake_relay.SUPPORTED_TYPES) - set(payload["supportedTypes"])

    assert unknown == set(), f"the double answers types the relay does not: {unknown}"


@live_only
def test_a_bad_key_closes_the_way_the_double_does():
    kind, payload = asyncio.run(_handshake("0" * 64, LIVE_CLIENT))

    assert kind == "closed"
    code, reason = payload
    assert code == fake_relay.CLOSE_CODE
    assert reason == fake_relay.REASON_BAD_KEY


@live_only
def test_a_malformed_auth_closes_the_way_the_double_does():
    kind, payload = asyncio.run(_handshake("", LIVE_CLIENT))

    assert kind == "closed"
    assert payload == (fake_relay.CLOSE_CODE, fake_relay.REASON_BAD_MESSAGE)


# ── runs everywhere: the double must at least be self-consistent ──────────

def test_the_doubles_close_reasons_are_distinct():
    """Four different failures, four different reasons — the client logs them,
    and collapsing two would hide a misconfiguration behind the wrong one."""
    reasons = {
        fake_relay.REASON_BAD_MESSAGE,
        fake_relay.REASON_BAD_KEY,
        fake_relay.REASON_BAD_CLIENT,
        fake_relay.REASON_NO_FOUNDRY,
    }

    assert len(reasons) == 4


def test_the_double_is_reachable_and_speaks_its_own_protocol():
    async def go():
        import websockets

        async with fake_relay.FakeRelay() as relay:
            ws = await websockets.connect(relay.ws_url)
            await ws.send(json.dumps(
                {"type": "auth", "token": "valid-token", "clientId": "test-client"}
            ))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            await ws.close()
            return ack

    ack = asyncio.run(go())

    assert ack["type"] == fake_relay.ACK_TYPE
    assert ack["eventChannels"] == fake_relay.EVENT_CHANNELS
