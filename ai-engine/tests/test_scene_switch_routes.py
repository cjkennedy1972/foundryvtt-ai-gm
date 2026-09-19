#!/usr/bin/env python3
"""The scene-switch route left the action caches holding the old scene.

execute_switch_scene calls _notify_scene_change, which does two things:
tells SceneAwareness, and calls reset_action_caches() — "New scene, new
canvas: forget which NPCs were confirmed present."

POST /api/scene/switch is a parallel implementation that does only the
first. So switching scenes from the admin panel left _npc_presence_checked,
the PC name and uuid caches, and the sound cache populated from the previous
canvas, and the next action could target a token that is no longer there.

This is the third place a route reimplemented an executor and diverged from
it (see #188). The route now calls the executor.

Run:
    cd ai-engine && python -m pytest tests/test_scene_switch_routes.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import executors
from api.deps import AppState, get_app_state
from api.routes import scene as scene_routes


@pytest.fixture
def state():
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.foundry_client.set_active_scene = AsyncMock(return_value={"ok": True})
    s.scene_awareness = AsyncMock()
    return s


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(scene_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


def _dirty_the_caches():
    executors._npc_presence_checked["Goblin"] = True
    executors._pc_uuid_cache["Thorin"] = "Actor.thorin"


def test_switching_scenes_forgets_the_previous_canvas(client):
    """A token confirmed present on the old scene is not on the new one."""
    _dirty_the_caches()

    resp = client.post("/api/scene/switch", params={"scene_name": "The Crypt"})

    assert resp.status_code == 200
    assert executors._npc_presence_checked == {}
    assert executors._pc_uuid_cache == {}


def test_switching_scenes_still_activates_the_scene(client, state):
    client.post("/api/scene/switch", params={"scene_name": "The Crypt"})

    state.foundry_client.set_active_scene.assert_awaited_once_with("The Crypt")


def test_switching_scenes_still_tells_scene_awareness(client, state):
    client.post("/api/scene/switch", params={"scene_name": "The Crypt"})

    state.scene_awareness.on_scene_change.assert_awaited_once_with("The Crypt")


def test_a_disconnected_foundry_is_refused(client, state):
    state.foundry_client = None

    resp = client.post("/api/scene/switch", params={"scene_name": "The Crypt"})

    assert resp.status_code == 503


def test_a_failed_switch_is_reported_not_crashed(client, state):
    state.foundry_client.set_active_scene = AsyncMock(side_effect=RuntimeError("relay down"))

    resp = client.post("/api/scene/switch", params={"scene_name": "The Crypt"})

    assert resp.status_code == 500
    assert "SCENE_SWITCH_FAILED" in resp.text


def test_awareness_failing_does_not_fail_the_switch(client, state):
    """_notify_scene_change logs and continues; the scene did change."""
    state.scene_awareness.on_scene_change = AsyncMock(side_effect=RuntimeError("boom"))

    resp = client.post("/api/scene/switch", params={"scene_name": "The Crypt"})

    assert resp.status_code == 200
