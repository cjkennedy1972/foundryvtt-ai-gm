"""FoundryClient hook waiting, scene activation listeners and context setters."""
import asyncio

import pytest

import foundry.client as client_mod
from foundry.client import FoundryClient
from test_foundry_client_cov_common import client_with_pw


def run(coro):
    return asyncio.run(coro)


# ── wait_for_hook / activate_scene_and_wait ──────────────────────────────────

def test_wait_for_hook_true_when_fired_and_handler_removed():
    async def scenario():
        c = client_with_pw()
        task = asyncio.create_task(c.wait_for_hook("canvasReady", timeout=2))
        await asyncio.sleep(0.01)
        for h in list(c._handlers["hooks"]):
            await h({"hook": "other"})
            await h({"hook": "canvasReady"})
        return c, await task

    c, fired = run(scenario())
    assert fired is True
    assert c._handlers["hooks"] == []


def test_wait_for_hook_times_out_false_and_removes_handler():
    c = client_with_pw()
    assert run(c.wait_for_hook("never", timeout=0.01)) is False
    assert c._handlers["hooks"] == []
    assert c._pw.channels == ["hooks"]


def test_wait_for_hook_tolerates_handler_already_removed():
    async def scenario():
        c = client_with_pw()
        task = asyncio.create_task(c.wait_for_hook("x", timeout=0.05))
        await asyncio.sleep(0.01)
        c._handlers["hooks"].clear()
        return await task

    assert run(scenario()) is False


def test_activate_scene_and_wait_returns_result_after_canvas_ready():
    async def scenario():
        c = client_with_pw()
        order = []

        async def set_scene(name):
            order.append(("set", bool(c._handlers.get("hooks"))))  # listener installed BEFORE activation
            for h in list(c._handlers["hooks"]):
                await h({"hook": "somethingElse"})
                await h({"hook": "canvasReady"})
            return {"ok": True, "name": name}

        c.set_active_scene = set_scene
        res = await c.activate_scene_and_wait("Crypt", timeout=1)
        return c, res, order

    c, res, order = run(scenario())
    assert res == {"ok": True, "name": "Crypt"}
    assert order == [("set", True)]
    assert c._handlers["hooks"] == []


def test_activate_scene_and_wait_times_out_but_still_returns_result(caplog):
    async def scenario():
        c = client_with_pw()

        async def set_scene(name):
            return {"ok": True}

        c.set_active_scene = set_scene
        return c, await c.activate_scene_and_wait("Crypt", timeout=0.01)

    with caplog.at_level("WARNING"):
        c, res = run(scenario())
    assert res == {"ok": True}
    assert any("canvasReady" in r.message for r in caplog.records)
    assert c._handlers["hooks"] == []


def test_activate_scene_and_wait_tolerates_removed_handler_and_propagates_error():
    async def scenario():
        c = client_with_pw()

        async def set_scene(name):
            c._handlers["hooks"].clear()
            raise RuntimeError("activate failed")

        c.set_active_scene = set_scene
        await c.activate_scene_and_wait("Crypt", timeout=0.01)

    with pytest.raises(RuntimeError, match="activate failed"):
        run(scenario())


# ── context setters ──────────────────────────────────────────────────────────

def test_context_truncation_at_limit(monkeypatch):
    monkeypatch.setattr(client_mod.settings, "context_max_chars", 10)
    c = FoundryClient()
    run(c.set_npc_context("x" * 25))
    run(c.set_world_context("short"))
    run(c.set_ai_tone("grim"))
    assert c._npc_context == "x" * 10
    assert c._world_context == "short"
    assert c._ai_tone == "grim"
