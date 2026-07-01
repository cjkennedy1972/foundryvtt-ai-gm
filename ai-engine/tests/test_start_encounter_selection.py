#!/usr/bin/env python3
"""
Regression test: FoundryClient.start_encounter must select tokens on canvas and
use startWithSelected, not the relay's `tokens` param.

Verified live against a running world: passing `tokens=[bare_id]` or
`tokens=[full "Scene.x.Token.y" uuid]` both started a combat with 0 combatants,
regardless of any delay after placement (ruling out a timing race). The only
path that reliably added combatants was selecting the tokens on canvas first,
then calling start-encounter with startWithSelected: true (no tokens array).

Run:
    cd ai-engine && python -m pytest tests/test_start_encounter_selection.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient


def _client_with_mocks(selected_count=2):
    fc = FoundryClient.__new__(FoundryClient)  # bypass __init__ (no real connection)
    fc.execute_js = AsyncMock(return_value={"result": selected_count})
    fc._send = AsyncMock(return_value={"encounterId": "E1"})
    return fc


def test_start_encounter_selects_tokens_before_sending():
    """When tokens are given, execute_js selects them on canvas first."""
    fc = _client_with_mocks(selected_count=2)
    asyncio.run(fc.start_encounter(tokens=["tok1", "tok2"], roll_all=True))

    fc.execute_js.assert_awaited_once()
    js_arg = fc.execute_js.await_args.args[0]
    assert "tok1" in js_arg and "tok2" in js_arg
    assert "control(" in js_arg


def test_start_encounter_uses_start_with_selected_not_tokens_param():
    """The relay call must use startWithSelected, not the unreliable tokens param."""
    fc = _client_with_mocks(selected_count=2)
    asyncio.run(fc.start_encounter(tokens=["tok1", "tok2"], roll_all=True))

    fc._send.assert_awaited_once()
    args, kwargs = fc._send.await_args
    assert args[0] == "start-encounter"
    assert kwargs.get("startWithSelected") is True
    assert kwargs.get("rollAll") is True
    assert "tokens" not in kwargs, "must not send the unreliable 'tokens' param"


def test_start_encounter_no_tokens_starts_empty():
    """Calling with no tokens (start-all-on-scene semantics) skips selection."""
    fc = _client_with_mocks()
    asyncio.run(fc.start_encounter(tokens=None, roll_all=False))

    fc.execute_js.assert_not_awaited()
    args, kwargs = fc._send.await_args
    assert kwargs.get("tokens") == []
    assert "startWithSelected" not in kwargs


def test_start_encounter_passes_name():
    fc = _client_with_mocks(selected_count=1)
    asyncio.run(fc.start_encounter(tokens=["tok1"], roll_all=True, name="Goblin Ambush"))
    _, kwargs = fc._send.await_args
    assert kwargs.get("name") == "Goblin Ambush"


def test_start_encounter_warns_when_no_token_resolves():
    """If none of the given ids resolve on canvas, still proceeds but the
    combat will end up empty — caller-visible via logging, not a raised error."""
    fc = _client_with_mocks(selected_count=0)
    result = asyncio.run(fc.start_encounter(tokens=["stale_id"], roll_all=True))
    assert result == {"encounterId": "E1"}  # doesn't raise; relay call still made
    _, kwargs = fc._send.await_args
    assert kwargs.get("startWithSelected") is True


if __name__ == "__main__":
    test_start_encounter_selects_tokens_before_sending()
    print("PASS  selects tokens before sending")
    test_start_encounter_uses_start_with_selected_not_tokens_param()
    print("PASS  uses startWithSelected, not tokens param")
    test_start_encounter_no_tokens_starts_empty()
    print("PASS  no-tokens call skips selection")
    test_start_encounter_passes_name()
    print("PASS  passes name through")
    test_start_encounter_warns_when_no_token_resolves()
    print("PASS  handles zero-resolved tokens without raising")
    print("\nAll start_encounter selection tests passed!")
