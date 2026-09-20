#!/usr/bin/env python3
"""_key_is_valid, over the four ways the relay closes an auth handshake.

Master keys are only accepted on the WebSocket path, so validating one means
performing the same handshake FoundryClient does and reading the close
reason. All four reasons were captured from the running relay:

    valid key, no session   4002 "No connected Foundry client found"
    valid key, wrong client 4002 "Invalid clientId"
    bad key                 4002 "Invalid API key"
    malformed auth frame    4002 "Invalid auth message"

Only "Invalid API key" means the key is bad. "No connected Foundry client
found" proves the opposite — the relay checks the key before it looks for a
Foundry session, so reaching that error means the key passed. Getting this
backwards would make the engine discard a good key and re-provision on every
start, rotating the one the admin just pasted in.

Anything else is ambiguous and must not block startup: an unreachable relay
returns valid, because refusing to start on a network hiccup is worse than
carrying on with a key that might be stale.

Not covered here: the recv timeout, which the relay hits when a cold world
takes longer than ten seconds to resolve a Foundry client. It returns valid
like the other ambiguous cases, and asserting it costs the suite those ten
seconds, since the wait is the thing under test.

Run:
    cd ai-engine && python -m pytest tests/test_relay_manager_key_check.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from fake_relay import FakeRelay
from relay_proc.manager import RelayManager


def run(coro):
    return asyncio.run(coro)


def _check(relay_url, key, monkeypatch):
    monkeypatch.setattr(settings, "relay_ws_url", relay_url)
    return run(RelayManager()._key_is_valid(key))


@pytest.mark.asyncio
async def test_a_key_the_relay_accepts_is_valid(monkeypatch):
    async with FakeRelay(token="good-key") as relay:
        monkeypatch.setattr(settings, "relay_ws_url", relay.ws_url)

        assert await RelayManager()._key_is_valid("good-key") is True


@pytest.mark.asyncio
async def test_a_key_the_relay_rejects_is_invalid(monkeypatch):
    async with FakeRelay(token="good-key") as relay:
        monkeypatch.setattr(settings, "relay_ws_url", relay.ws_url)

        assert await RelayManager()._key_is_valid("wrong-key") is False


@pytest.mark.asyncio
async def test_no_foundry_session_still_proves_the_key_is_good(monkeypatch):
    """The relay checks the key before looking for a Foundry client, so this
    close reason is reached only by a valid key. Treating it as invalid would
    rotate a good key on every start of an unpaired world."""
    async with FakeRelay(token="good-key", foundry_connected=False) as relay:
        monkeypatch.setattr(settings, "relay_ws_url", relay.ws_url)

        assert await RelayManager()._key_is_valid("good-key") is True


@pytest.mark.asyncio
async def test_an_unreachable_relay_does_not_condemn_the_key(monkeypatch):
    """Ambiguous, and refusing to start on a network hiccup is worse than
    carrying on."""
    monkeypatch.setattr(settings, "relay_ws_url", "ws://127.0.0.1:1/ws/api")

    assert await RelayManager()._key_is_valid("any-key") is True


@pytest.mark.asyncio
async def test_an_unrecognised_close_reason_is_treated_as_ambiguous(monkeypatch):
    """Neither decisive reason. Condemning the key here would discard a good
    one whenever the relay closes for a reason this code has not seen."""
    async with FakeRelay(token="good-key", close_reason="Server shutting down") as relay:
        monkeypatch.setattr(settings, "relay_ws_url", relay.ws_url)

        result = await RelayManager()._key_is_valid("good-key")

    assert result is True
