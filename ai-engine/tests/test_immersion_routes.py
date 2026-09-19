#!/usr/bin/env python3
"""The immersion HTTP routes bypassed the executors fixed in #185 and #186.

Those two PRs made the apply_token_effect and update_vision *actions* reach
Foundry, because the immersion managers are in-memory bookkeeping and the
actions were telling the model they had changed the map.

POST /api/immersion/token-effect and POST /api/immersion/vision call the same
managers directly, so the HTTP layer kept the original behaviour: the admin
panel could set a condition or light a torch, get a result with no error, and
nothing would appear in Foundry.

Both now go through the executors, which record and render.

api/routes/immersion.py was at 29% with nothing exercising it.

Run:
    cd ai-engine && python -m pytest tests/test_immersion_routes.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.deps import AppState, get_app_state
from api.routes import immersion as immersion_routes
from immersion.ambient import AmbientManager
from immersion.effects import EffectsManager
from immersion.vision import VisionManager


@pytest.fixture
def state():
    s = AppState()
    s.ambient_manager = AmbientManager()
    s.effects_manager = EffectsManager()
    s.vision_manager = VisionManager()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.foundry_client.get_scene_tokens = AsyncMock(return_value=[
        {"id": "tok1", "name": "Goblin", "actorUuid": "Actor.goblin"},
    ])
    s.foundry_client.add_effect = AsyncMock(return_value={"ok": True})
    s.foundry_client.execute_js = AsyncMock(
        return_value={"result": {"ok": True, "id": "tok1", "name": "Goblin"}}
    )
    return s


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(immersion_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


# ── the two that reached nothing ──────────────────────────────────────────

def test_a_condition_set_over_http_reaches_foundry(client, state):
    resp = client.post("/api/immersion/token-effect", params={
        "token_id": "tok1", "effect_type": "condition", "effect_name": "poisoned",
    })

    assert resp.status_code == 200
    state.foundry_client.add_effect.assert_awaited_once()
    assert resp.json().get("rendered_in_foundry") is True


def test_vision_set_over_http_reaches_foundry(client, state):
    resp = client.post("/api/immersion/vision", params={
        "token_id": "tok1", "vision_range": 60,
    })

    assert resp.status_code == 200
    state.foundry_client.execute_js.assert_awaited_once()
    assert resp.json().get("rendered_in_foundry") is True


def test_a_torch_lit_over_http_reaches_foundry(client, state):
    client.post("/api/immersion/vision", params={
        "token_id": "tok1", "vision_range": 60, "has_light": True, "light_radius": 40,
    })

    assert "40" in state.foundry_client.execute_js.await_args.args[0]


def test_an_aura_still_says_it_is_narration_only(client, state):
    resp = client.post("/api/immersion/token-effect", params={
        "token_id": "tok1", "effect_type": "aura", "effect_name": "protection",
    })

    assert resp.json().get("rendered_in_foundry") is False
    state.foundry_client.add_effect.assert_not_awaited()


def test_an_unknown_effect_type_is_still_rejected(client):
    resp = client.post("/api/immersion/token-effect", params={
        "token_id": "tok1", "effect_type": "sparkles", "effect_name": "x",
    })

    assert resp.json().get("error")


def test_the_effect_is_still_readable_afterwards(client):
    client.post("/api/immersion/token-effect", params={
        "token_id": "tok1", "effect_type": "condition", "effect_name": "poisoned",
    })

    body = client.get("/api/immersion/token-effects/tok1").json()

    assert body["effects"]


# ── the narrative-only ones, which are legitimate ─────────────────────────

def test_weather_is_recorded_and_reaches_the_atmosphere_line(client):
    """AmbientManager feeds the prompt rather than the map, which is what it
    is for — chat_listener reads get_atmosphere_description back."""
    assert client.post("/api/immersion/weather", params={"weather": "rain"}).status_code == 200

    body = client.get("/api/immersion/atmosphere").json()
    assert "rain" in str(body).lower()


def test_an_unknown_weather_is_rejected_not_a_crash(client):
    resp = client.post("/api/immersion/weather", params={"weather": "meteors"})

    assert resp.status_code == 200
    assert resp.json().get("error")


def test_an_unknown_time_is_rejected_not_a_crash(client):
    resp = client.post("/api/immersion/time", params={"time": "half_past_ten"})

    assert resp.status_code == 200
    assert resp.json().get("error")


def test_vision_status_reports_what_was_set(client):
    client.post("/api/immersion/vision", params={"token_id": "tok1", "vision_range": 60})

    assert client.get("/api/immersion/vision-status").status_code == 200
