"""Tests for action approval workflow.

Tests the approval gate for consequential mutations (items, stats, levels).

Run:
    cd ai-engine && python -m pytest tests/test_action_approval.py -v
"""

import pytest
from actions.approval import (
    ApprovalWorkflow,
    ApprovalStatus,
    CONSEQUENTIAL_ACTIONS,
    SAFE_ACTIONS,
)


class TestActionApproval:
    """Tests for approval workflow."""

    def test_approve_consequential_action(self):
        """Consequential actions (grant_item) require approval."""
        workflow = ApprovalWorkflow()

        proposal = workflow.propose(
            action_type="grant_item",
            actor_id="gm",
            target_id="player-1",
            parameters={"item": "magic_sword", "quantity": 1},
            description="Give player a magic sword",
        )

        assert proposal.status == ApprovalStatus.PENDING
        assert proposal.requires_approval is True
        assert proposal in workflow.get_pending()

    def test_auto_approve_safe_action(self):
        """Safe actions (narrate) auto-approve without GM review."""
        workflow = ApprovalWorkflow()

        proposal = workflow.propose(
            action_type="narrate",
            actor_id="gm",
            description="Describe the scene",
        )

        assert proposal.status == ApprovalStatus.APPROVED_AUTO
        assert proposal.requires_approval is False
        assert proposal not in workflow.get_pending()
        assert proposal in workflow.get_approved()

    def test_gm_approves_proposal(self):
        """GM can approve a pending proposal."""
        workflow = ApprovalWorkflow()

        proposal = workflow.propose(
            action_type="level_up",
            target_id="player-1",
            description="Level player to 5",
        )

        proposal_id = proposal.id

        # Approve it
        success = workflow.approve(proposal_id)
        assert success is True

        # Verify state
        assert proposal_id not in workflow.pending
        assert proposal in workflow.get_approved()
        assert proposal.status == ApprovalStatus.APPROVED

    def test_gm_rejects_proposal(self):
        """GM can reject a pending proposal."""
        workflow = ApprovalWorkflow()

        proposal = workflow.propose(
            action_type="grant_item",
            target_id="player-1",
            description="Grant forbidden item",
        )

        proposal_id = proposal.id

        # Reject it
        success = workflow.reject(proposal_id)
        assert success is True

        # Verify state
        assert proposal_id not in workflow.pending
        assert proposal in workflow.get_rejected()
        assert proposal.status == ApprovalStatus.REJECTED

    def test_approve_nonexistent_proposal(self):
        """Approving nonexistent proposal returns False."""
        workflow = ApprovalWorkflow()

        success = workflow.approve("nonexistent-id")
        assert success is False

    def test_reject_nonexistent_proposal(self):
        """Rejecting nonexistent proposal returns False."""
        workflow = ApprovalWorkflow()

        success = workflow.reject("nonexistent-id")
        assert success is False

    def test_consequential_actions_list(self):
        """Verify all consequential action types are defined."""
        expected = {
            "grant_item",
            "remove_item",
            "modify_stat",
            "heal",
            "damage",
            "level_up",
            "apply_condition",
            "remove_condition",
            "grant_spell",
            "modify_currency",
        }

        assert CONSEQUENTIAL_ACTIONS == expected

    def test_safe_actions_list(self):
        """Verify all safe action types are defined."""
        safe = {
            "narrate",
            "describe_scene",
            "cast_spell",
            "move_token",
            "trigger_sound",
            "update_vision",
            "place_sounds",
            "environmental_save",
            "execute_macro",
        }

        assert SAFE_ACTIONS == safe

    def test_pending_proposals_accumulate(self):
        """Multiple pending proposals accumulate."""
        workflow = ApprovalWorkflow()

        p1 = workflow.propose("grant_item", target_id="p1", description="Item 1")
        p2 = workflow.propose("level_up", target_id="p2", description="Level 2")
        p3 = workflow.propose("modify_stat", target_id="p3", description="Stat 3")

        pending = workflow.get_pending()
        assert len(pending) == 3
        assert p1 in pending
        assert p2 in pending
        assert p3 in pending

    def test_get_approved_history(self):
        """get_approved returns recent approved actions."""
        workflow = ApprovalWorkflow()

        # Safe actions auto-approve
        proposals = [
            workflow.propose("narrate", description=f"Action {i}") for i in range(25)
        ]

        approved = workflow.get_approved(limit=10)
        assert len(approved) == 10
        assert approved[-1].description == "Action 24"

    def test_proposal_summary(self):
        """Proposal.summary() formats clearly for GM display."""
        proposal = ActionProposal(
            id="test-1",
            action_type="grant_item",
            actor_id="npc-mara",
            target_id="player-1",
            description="Magic sword",
        )

        summary = proposal.summary()
        assert "grant_item" in summary
        assert "npc-mara" in summary
        assert "player-1" in summary
        assert "Magic sword" in summary

    def test_is_consequential_check(self):
        """is_consequential() correctly identifies action types."""
        workflow = ApprovalWorkflow()

        assert workflow.is_consequential("grant_item") is True
        assert workflow.is_consequential("level_up") is True
        assert workflow.is_consequential("narrate") is False
        assert workflow.is_consequential("move_token") is False

    def test_clear_history(self):
        """clear_history removes approved/rejected but keeps pending."""
        workflow = ApprovalWorkflow()

        # Mix of actions
        pending = workflow.propose("grant_item", target_id="p1", description="Pending")
        workflow.propose("narrate", description="Auto-approved")
        workflow.propose("grant_item", target_id="p2", description="Later rejected")
        workflow.reject(workflow.get_pending()[1].id)

        # Clear history
        workflow.clear_history()

        assert len(workflow.get_pending()) == 1
        assert len(workflow.get_approved()) == 0
        assert len(workflow.get_rejected()) == 0


# Test dataclass
from actions.approval import ActionProposal


class TestActionProposal:
    """Tests for ActionProposal dataclass."""

    def test_proposal_creation(self):
        """ActionProposal can be created with basic fields."""
        proposal = ActionProposal(
            id="test-1",
            action_type="grant_item",
            description="Test action",
        )

        assert proposal.id == "test-1"
        assert proposal.action_type == "grant_item"
        assert proposal.status == ApprovalStatus.PENDING
        assert proposal.parameters == {}

    def test_proposal_with_parameters(self):
        """ActionProposal can store complex parameters."""
        params = {"item": "sword", "quantity": 2, "enchantment": "fire"}
        proposal = ActionProposal(
            id="test-1",
            action_type="grant_item",
            parameters=params,
        )

        assert proposal.parameters == params

    def test_proposal_defaults(self):
        """ActionProposal has sensible defaults."""
        proposal = ActionProposal(id="test-1", action_type="narrate")

        assert proposal.actor_id is None
        assert proposal.target_id is None
        assert proposal.description == ""
        assert proposal.reasoning == ""
        assert proposal.parameters == {}
