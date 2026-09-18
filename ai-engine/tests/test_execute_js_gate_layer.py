"""ALLOW_EXECUTE_JS must gate untrusted input, never the transport.

The flag used to be checked inside FoundryClient.execute_js, which blocked all
89 call sites instead of the 2 that carry untrusted code. Under the documented
default of false, combat turn sync, initiative, death saves, spell slots, scene
setup, teardown and TTS playback all raised ValueError. No test caught it
because every other test in this suite replaces execute_js with an AsyncMock,
so the real method never ran. These tests drive the real method.
"""

import pytest
from unittest.mock import AsyncMock

from actions.executors import execute_execute_js
from config import settings
from foundry.client import FoundryClient


@pytest.fixture
def client(monkeypatch):
    c = FoundryClient()
    # Stub the wire, not the method under test.
    monkeypatch.setattr(c, "_send", AsyncMock(return_value={"result": "ok"}))
    return c


@pytest.mark.asyncio
async def test_first_party_call_works_under_shipped_default(client, monkeypatch):
    """roll_initiative carries no untrusted input and must run when the flag is off."""
    monkeypatch.setattr(settings, "allow_execute_js", False)

    assert await client.roll_initiative() == {"result": "ok"}
    client._send.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_never_reads_the_flag(client, monkeypatch):
    """Flipping the flag must not change transport behaviour either way."""
    monkeypatch.setattr(settings, "allow_execute_js", False)
    off = await client.execute_js("return 1;")
    monkeypatch.setattr(settings, "allow_execute_js", True)
    on = await client.execute_js("return 1;")

    assert off == on == {"result": "ok"}
    assert client._send.await_count == 2


@pytest.mark.asyncio
async def test_untrusted_action_still_blocked_when_flag_is_off(monkeypatch):
    """The LLM-driven action keeps its gate; it is the real injection surface."""
    monkeypatch.setattr(settings, "allow_execute_js", False)
    foundry = AsyncMock()

    result = await execute_execute_js(
        code="game.actors.forEach(a => a.delete())", foundry=foundry
    )

    assert result["success"] is False
    assert "disabled" in result["error"].lower()
    foundry.execute_js.assert_not_awaited()
