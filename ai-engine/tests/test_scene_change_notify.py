#!/usr/bin/env python3
"""
Regression test: scene-switch actions must notify SceneAwareness directly.

The relay does not reliably emit a scene-event for a programmatic
scene.activate() from the headless client (and its payload keys it by sceneId,
not sceneName), so relying on the event left SceneAwareness empty all session —
no [Scene] logs, no scene summary, no per-scene encounter context. The
switch_scene and setup_scene executors must call on_scene_change themselves.

Run:
    cd ai-engine && python -m pytest tests/test_scene_change_notify.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import executors


def _app_state():
    awareness = SimpleNamespace(on_scene_change=AsyncMock())
    return SimpleNamespace(scene_awareness=awareness), awareness


def test_switch_scene_notifies_awareness():
    foundry = SimpleNamespace(set_active_scene=AsyncMock(return_value={"ok": True}))
    app_state, awareness = _app_state()
    asyncio.run(executors.execute_switch_scene("The Crypt", foundry=foundry, app_state=app_state))
    awareness.on_scene_change.assert_awaited_once_with("The Crypt")


def test_setup_scene_notifies_awareness():
    foundry = SimpleNamespace(
        set_active_scene=AsyncMock(return_value={"ok": True}),
        configure_scene=AsyncMock(return_value={}),
    )
    app_state, awareness = _app_state()
    asyncio.run(executors.execute_setup_scene(
        scene_name="The Nave", foundry=foundry, app_state=app_state,
    ))
    awareness.on_scene_change.assert_awaited_once_with("The Nave")


def test_notify_is_safe_without_app_state():
    # No app_state (e.g. older call sites) must not raise.
    foundry = SimpleNamespace(set_active_scene=AsyncMock(return_value={}))
    asyncio.run(executors.execute_switch_scene("X", foundry=foundry))


if __name__ == "__main__":
    test_switch_scene_notifies_awareness()
    print("PASS  switch_scene notifies SceneAwareness")
    test_setup_scene_notifies_awareness()
    print("PASS  setup_scene notifies SceneAwareness")
    test_notify_is_safe_without_app_state()
    print("PASS  notify is safe without app_state")
    print("All scene-change-notify tests passed.")
