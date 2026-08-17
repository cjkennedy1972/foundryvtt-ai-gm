"""Tests for approval workflow API endpoints.

Tests the GM approval gate endpoints (pending, approve, reject).

Run:
    cd ai-engine && python -m pytest tests/test_approval_api.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.approval import create_approval_router
from actions.approval import ApprovalWorkflow, ApprovalStatus, ActionProposal


class TestApprovalAPI:
    """Tests for approval API endpoints."""

    def test_get_pending_proposals(self):
        """Pending endpoint returns list of waiting proposals."""
        app_state = MagicMock()
        workflow = ApprovalWorkflow()

        # Add some pending proposals
        p1 = workflow.propose(
            "grant_item",
            target_id="player-1",
            description="Magic sword",
        )
        p2 = workflow.propose(
            "level_up",
            target_id="player-1",
            description="Level to 5",
        )

        listener = MagicMock()
        listener._approval_workflow = workflow
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/approval/pending")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["action_type"] == "grant_item"
        assert data[1]["action_type"] == "level_up"

    def test_get_pending_no_workflow(self):
        """Pending endpoint returns empty list when no workflow."""
        app_state = MagicMock()
        listener = MagicMock()
        # No _approval_workflow attribute
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/approval/pending")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_approve_proposal(self):
        """Approve endpoint accepts a pending proposal."""
        app_state = MagicMock()
        workflow = ApprovalWorkflow()

        proposal = workflow.propose(
            "grant_item",
            target_id="player-1",
            description="Magic sword",
        )

        listener = MagicMock()
        listener._approval_workflow = workflow
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.post(f"/api/approval/{proposal.id}/approve")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"

        # Verify state changed
        pending = workflow.get_pending()
        assert len(pending) == 0
        approved = workflow.get_approved()
        assert len(approved) == 1
        assert approved[0].status == ApprovalStatus.APPROVED

    def test_approve_nonexistent_proposal(self):
        """Approve endpoint returns 404 for nonexistent proposal."""
        app_state = MagicMock()
        workflow = ApprovalWorkflow()

        listener = MagicMock()
        listener._approval_workflow = workflow
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.post("/api/approval/nonexistent/approve")
        assert response.status_code == 404

    def test_reject_proposal(self):
        """Reject endpoint rejects a pending proposal."""
        app_state = MagicMock()
        workflow = ApprovalWorkflow()

        proposal = workflow.propose(
            "grant_item",
            target_id="player-1",
            description="Forbidden item",
        )

        listener = MagicMock()
        listener._approval_workflow = workflow
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.post(f"/api/approval/{proposal.id}/reject")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"

        # Verify state changed
        pending = workflow.get_pending()
        assert len(pending) == 0
        rejected = workflow.get_rejected()
        assert len(rejected) == 1
        assert rejected[0].status == ApprovalStatus.REJECTED

    def test_reject_nonexistent_proposal(self):
        """Reject endpoint returns 404 for nonexistent proposal."""
        app_state = MagicMock()
        workflow = ApprovalWorkflow()

        listener = MagicMock()
        listener._approval_workflow = workflow
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.post("/api/approval/nonexistent/reject")
        assert response.status_code == 404

    def test_proposal_includes_all_fields(self):
        """Proposal response includes all relevant fields."""
        app_state = MagicMock()
        workflow = ApprovalWorkflow()

        proposal = workflow.propose(
            "modify_stat",
            actor_id="npc-1",
            target_id="player-1",
            parameters={"stat": "strength", "delta": 2},
            description="Permanent strength boost",
            reasoning="Character earned a wish",
        )

        listener = MagicMock()
        listener._approval_workflow = workflow
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/approval/pending")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        item = data[0]
        assert item["id"] == proposal.id
        assert item["action_type"] == "modify_stat"
        assert item["actor_id"] == "npc-1"
        assert item["target_id"] == "player-1"
        assert item["description"] == "Permanent strength boost"
        assert item["reasoning"] == "Character earned a wish"

    def test_auto_approved_actions_not_in_pending(self):
        """Auto-approved safe actions don't appear in pending list."""
        app_state = MagicMock()
        workflow = ApprovalWorkflow()

        # Mix of safe and consequential
        workflow.propose("narrate", description="Story narration")
        workflow.propose("grant_item", description="Magic sword")

        listener = MagicMock()
        listener._approval_workflow = workflow
        app_state.chat_listener = listener

        app = FastAPI()
        router = create_approval_router(app_state)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/approval/pending")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["action_type"] == "grant_item"
