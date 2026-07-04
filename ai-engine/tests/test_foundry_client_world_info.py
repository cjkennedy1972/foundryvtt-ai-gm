"""Checks for FoundryClient.get_world_info() reading modules via execute_js.

Regression test for a real bug: the relay's 'world-info' RPC always
reported zero active modules even with 72 modules active in the world,
silently disabling every module-integration check in deploy_to_foundry
and combat/loop.py. get_world_info() now reads game.modules directly.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foundry.client import FoundryClient


class StubExecuteJS:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def execute_js(self, code):
        self.calls.append(code)
        return {"result": self._result}


def test_get_world_info_returns_modules_from_execute_js():
    client = FoundryClient()
    stub_result = [
        {"id": "midi-qol", "title": "Midi QOL", "version": "1.0", "active": True},
        {"id": "some-disabled-module", "title": "Disabled", "version": "2.0", "active": False},
    ]
    client.execute_js = StubExecuteJS(stub_result).execute_js

    info = asyncio.run(client.get_world_info())

    assert info["modules"] == stub_result


def test_get_world_info_tolerates_malformed_response():
    client = FoundryClient()
    client.execute_js = StubExecuteJS(None).execute_js  # execute_js returns {"result": None}

    info = asyncio.run(client.get_world_info())

    assert info["modules"] == []


def test_get_world_info_tolerates_execute_js_failure():
    client = FoundryClient()

    async def _raise(code):
        raise ConnectionError("relay unreachable")

    client.execute_js = _raise

    info = asyncio.run(client.get_world_info())

    assert info == {"modules": []}
