"""Tests for session control API endpoints.

Tests the in-Foundry control surface API (pause/resume, idle beats, queries).

Run:
    cd ai-engine && python -m pytest tests/test_session_control_api.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.session_control import (
    create_session_control_router,
    SessionStatus,
    SettlementLocationResponse,
)


class TestSessionControlAPI:
    """Tests for session control endpoints."""

    def test_session_status_no_active_session(self):
        """Status endpoint returns minimal info when no session active."""
        app_state = MagicMock()
        app_state.db = AsyncMock()
        app_state.db.get_active_session_info = AsyncMock(return_value=None)

        app = FastAPI()
        router = create_session_control_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/session/status")
        assert response.status_code == 200
        data = response.json()
        assert data["is_running"] is False
        assert data["session_id"] is None

    def test_session_status_active_session(self):
        """Status endpoint returns full info for active session."""
        app_state = MagicMock()
        app_state.db = AsyncMock()
        app_state.db.get_active_session_info = AsyncMock(
            return_value={
                "session_id": "session-1",
                "campaign": "The Shattered Coast",
                "turn_count": 5,
            }
        )

        listener = MagicMock()
        listener._running = True
        listener._world_clock = MagicMock()
        listener._world_clock.get_current_time = MagicMock(return_value="dusk")
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_session_control_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/session/status")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "session-1"
        assert data["campaign"] == "The Shattered Coast"
        assert data["is_running"] is True
        assert data["current_time"] == "dusk"
        assert data["turn_count"] == 5

    def test_pause_session(self):
        """Pause endpoint sets _running to False."""
        app_state = MagicMock()
        listener = MagicMock()
        listener._running = True
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_session_control_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.post("/api/session/pause")
        assert response.status_code == 200
        assert response.json()["status"] == "paused"
        assert listener._running is False

    def test_resume_session(self):
        """Resume endpoint sets _running to True."""
        app_state = MagicMock()
        listener = MagicMock()
        listener._running = False
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_session_control_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.post("/api/session/resume")
        assert response.status_code == 200
        assert response.json()["status"] == "running"
        assert listener._running is True

    @pytest.mark.asyncio
    async def test_list_settlements(self):
        """List settlements endpoint returns settlement info."""
        from world.settlement import Settlement

        app_state = MagicMock()
        listener = MagicMock()

        settlement = Settlement(
            id="redmarch",
            name="Redmarch",
            region="The Coast",
            population=500,
            character="A bustling trade town",
        )
        settlement.npcs = {"mara": MagicMock(), "kess": MagicMock()}
        settlement.buildings = {"tavern": MagicMock(), "smithy": MagicMock()}

        listener._world_clock = MagicMock()
        listener._world_clock.list_settlements = MagicMock(return_value=[settlement])
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_session_control_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/session/settlements")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "redmarch"
        assert data[0]["name"] == "Redmarch"
        assert data[0]["npc_count"] == 2
        assert data[0]["building_count"] == 2

    @pytest.mark.asyncio
    async def test_query_settlement_locations(self):
        """Settlement query endpoint returns NPC locations."""
        app_state = MagicMock()
        listener = MagicMock()
        listener._world_clock = AsyncMock()
        listener._world_clock.query_location_at_time = AsyncMock(
            return_value={"tavern": ["mara"], "smithy": ["kess"]}
        )
        listener._world_clock.get_current_time = MagicMock(return_value="dusk")
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_session_control_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/session/settlements/redmarch")
        assert response.status_code == 200
        data = response.json()
        assert data["settlement_id"] == "redmarch"
        assert data["time_of_day"] == "dusk"
        assert data["locations"]["tavern"] == ["mara"]
        assert data["locations"]["smithy"] == ["kess"]

    @pytest.mark.asyncio
    async def test_query_settlement_at_specific_time(self):
        """Settlement query endpoint accepts explicit time parameter."""
        app_state = MagicMock()
        listener = MagicMock()
        listener._world_clock = AsyncMock()
        listener._world_clock.query_location_at_time = AsyncMock(
            return_value={"residence": ["mara"]}
        )
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_session_control_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/session/settlements/redmarch?time_of_day=morning")
        assert response.status_code == 200
        data = response.json()
        assert data["time_of_day"] == "morning"
        listener._world_clock.query_location_at_time.assert_called_with(
            "redmarch", "morning"
        )
