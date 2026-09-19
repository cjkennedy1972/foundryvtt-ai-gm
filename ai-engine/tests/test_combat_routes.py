#!/usr/bin/env python3
"""Drives api/routes/combat.py through a real TestClient.

The module sat at 20% with nothing exercising it, while four of its
endpoints run code changed in #179 (encounter difficulty) and #183 (grid
distance). A route contract broken by either would have gone unnoticed.

It also found one defect: /difficulty/suggestions maps a caller-supplied
string straight through EncounterDifficulty[difficulty.upper()], so any
value outside the five band names is an uncaught KeyError and a 500.

Run:
    cd ai-engine && python -m pytest tests/test_combat_routes.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import combat as combat_routes


@pytest.fixture
def state():
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    # is_running / current_round / current_turn / turn_order are properties
    # on CombatLoop, so the route reads them as values, not calls.
    s.combat_loop = MagicMock()
    s.combat_loop.is_running = False
    s.combat_loop.current_round = 0
    s.combat_loop.current_turn = 0
    s.combat_loop.turn_order = []
    s.combat_loop.stop = AsyncMock()
    s.state_tracker = AsyncMock()
    return s


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(combat_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _api_error(request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump(),
        )

    return TestClient(app, raise_server_exceptions=False)


# ── difficulty, over the engine rewritten in #179 ─────────────────────────

class TestDifficulty:
    def test_a_party_gets_a_rating_and_its_reasoning(self, client):
        resp = client.post(
            "/api/combat/difficulty/suggest",
            params={"num_players": 4, "avg_level": 5}, json=[2, 2, 2],
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["difficulty"] in ("trivial", "easy", "medium", "hard", "deadly")
        assert body["estimated_xp"] > 0
        assert body["party_power_rating"] == 1.0

    def test_the_rating_tracks_party_level(self, client):
        """#179: level was read and never used, so these were identical."""
        def rate(level):
            return client.post(
                "/api/combat/difficulty/suggest",
                params={"num_players": 4, "avg_level": level}, json=[0.25] * 4,
            ).json()["difficulty"]

        assert rate(1) != rate(20)

    def test_an_empty_encounter_is_trivial(self, client):
        resp = client.post(
            "/api/combat/difficulty/suggest",
            params={"num_players": 4, "avg_level": 5}, json=[],
        )

        assert resp.json()["difficulty"] == "trivial"

    @pytest.mark.parametrize("band", ["trivial", "easy", "medium", "hard", "deadly"])
    def test_every_band_can_be_asked_for_suggestions(self, client, band):
        resp = client.get(
            "/api/combat/difficulty/suggestions",
            params={"num_players": 4, "avg_level": 5, "difficulty": band},
        )

        assert resp.status_code == 200
        assert resp.json()["suggestions"]

    def test_an_unknown_band_is_rejected_not_a_crash(self, client):
        """EncounterDifficulty[...] on caller input is a KeyError, so this
        answered 500 for any typo."""
        resp = client.get(
            "/api/combat/difficulty/suggestions",
            params={"num_players": 4, "avg_level": 5, "difficulty": "impossible"},
        )

        assert resp.status_code == 400, f"got {resp.status_code}"
        assert "impossible" in resp.text or "difficulty" in resp.text.lower()

    def test_the_band_is_matched_case_insensitively(self, client):
        resp = client.get(
            "/api/combat/difficulty/suggestions",
            params={"num_players": 4, "avg_level": 5, "difficulty": "Hard"},
        )

        assert resp.status_code == 200


# ── combat lifecycle ──────────────────────────────────────────────────────

class TestLifecycle:
    def test_status_reports_a_quiet_table(self, client):
        resp = client.get("/api/combat/status")

        assert resp.status_code == 200
        assert resp.json()["running"] is False

    def test_status_reports_a_running_fight(self, client, state):
        state.combat_loop.is_running = True
        state.combat_loop.current_round = 3
        state.combat_loop.turn_order = ["tok1", "tok2"]

        body = client.get("/api/combat/status").json()

        assert body["running"] is True
        assert body["round"] == 3
        assert body["turn_order"] == ["tok1", "tok2"]

    def test_stopping_combat_stops_the_loop(self, client, state):
        resp = client.post("/api/combat/stop")

        assert resp.status_code == 200
        state.combat_loop.stop.assert_awaited_once()

    def test_a_disconnected_foundry_is_refused_not_crashed(self, client, state):
        state.foundry_client.is_connected = False

        resp = client.post("/api/combat/tactical/analyze", params={"actor_id": "tok1"})

        assert resp.status_code == 503


# ── tactical, over the geometry fixed in #183 ─────────────────────────────

class TestTactical:
    def test_analysis_returns_the_snapshot_for_a_token(self, client):
        with patch("combat.tactics.build_tactical_snapshot",
                   AsyncMock(return_value="- Goblin: 5 ft")), \
             patch("actions.executors._resolve_token_id", AsyncMock(return_value="tok1")):
            resp = client.post("/api/combat/tactical/analyze", params={"actor_id": "Thorin"})

        assert resp.status_code == 200
        assert "Goblin" in resp.json()["analysis"]

    def test_an_empty_battlefield_says_so_rather_than_returning_nothing(self, client):
        with patch("combat.tactics.build_tactical_snapshot", AsyncMock(return_value="")), \
             patch("actions.executors._resolve_token_id", AsyncMock(return_value="tok1")):
            resp = client.post("/api/combat/tactical/analyze", params={"actor_id": "Thorin"})

        assert "No enemies visible" in resp.json()["analysis"]

    def test_flanking_is_reported_with_its_benefit(self, client):
        with patch("combat.tactics.flanking_check", MagicMock(return_value=True)), \
             patch("combat.tactics.fetch_scene_state", AsyncMock(return_value={"tokens": []})), \
             patch("actions.executors._resolve_token_id", AsyncMock(side_effect=["a", "b"])):
            resp = client.post("/api/combat/tactical/flanking",
                               params={"attacker_id": "a", "target_id": "b"})

        body = resp.json()
        assert body["is_flanking"] is True
        assert "advantage" in body["benefit"]

    def test_an_unresolvable_token_is_not_reported_as_flanking(self, client):
        with patch("combat.tactics.flanking_check", MagicMock(return_value=None)), \
             patch("combat.tactics.fetch_scene_state", AsyncMock(return_value={"tokens": []})), \
             patch("actions.executors._resolve_token_id", AsyncMock(side_effect=["a", "b"])):
            resp = client.post("/api/combat/tactical/flanking",
                               params={"attacker_id": "ghost", "target_id": "b"})

        body = resp.json()
        assert body["is_flanking"] is False
        assert "Could not resolve" in body["benefit"]

    def test_spatial_relationships_survive_an_unreadable_scene(self, client):
        with patch("combat.tactics.fetch_scene_state", AsyncMock(return_value=None)):
            resp = client.get("/api/combat/spatial-relationships")

        assert resp.status_code == 200
        assert resp.json()["relationships"] == []
