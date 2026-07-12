"""Regression test: wait_for_hook must match against a hook name the relay
actually forwards.

Root cause this guards against: scene-switch code waited on "renderCanvasFrame"
and "sceneActivated", but the relay's REST API module only ever forwards
"canvasReady" (see FORWARDED_HOOKS in its eventChannels.ts) — those two names
never arrived, so every scene switch silently burned its full timeout before
falling back to a blind sleep.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from foundry.client import FoundryClient


def _make_client():
    client = FoundryClient()
    client._send = AsyncMock(return_value={})
    return client


def test_wait_for_hook_resolves_on_matching_event():
    client = _make_client()

    async def run():
        task = asyncio.create_task(client.wait_for_hook("canvasReady", timeout=2))
        await asyncio.sleep(0)  # let subscribe_to_channel/subscribe register the handler
        for handler in client._handlers["hooks"]:
            await handler({"hook": "canvasReady", "args": []})
        return await task

    assert asyncio.run(run()) is True


def test_wait_for_hook_ignores_nonmatching_event_and_times_out():
    client = _make_client()

    async def run():
        task = asyncio.create_task(client.wait_for_hook("canvasReady", timeout=0.3))
        await asyncio.sleep(0)
        # The exact wrong names this bug used — must NOT satisfy the wait.
        for handler in client._handlers["hooks"]:
            await handler({"hook": "renderCanvasFrame", "args": []})
            await handler({"hook": "sceneActivated", "args": []})
        return await task

    assert asyncio.run(run()) is False


def test_activate_scene_registers_handler_before_activation():
    client = _make_client()
    order = []

    async def activate(scene_name):
        order.append("activate")
        for handler in client._handlers["hooks"]:
            await handler({"hook": "canvasReady", "args": []})
        return {"ok": True, "name": scene_name}

    client.set_active_scene = activate

    async def run():
        original_subscribe = client.subscribe

        def subscribe(channel, handler):
            order.append("register")
            original_subscribe(channel, handler)

        client.subscribe = subscribe
        return await client.activate_scene_and_wait("Courtyard", timeout=1)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert order == ["register", "activate"]
