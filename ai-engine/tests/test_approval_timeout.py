"""Tests for approval workflow timeout functionality (unattended mode)."""

import time
import pytest
from actions.approval import ApprovalWorkflow, ApprovalStatus


class TestApprovalTimeout:
    """Timeout-based auto-approval for unattended/autonomous mode."""

    def test_timeout_mode_auto_approves_after_delay(self):
        """In timeout mode, proposals auto-approve after timeout."""
        workflow = ApprovalWorkflow(mode="timeout", timeout_seconds=0.1)

        # Propose a consequential action
        proposal = workflow.propose(
            action_type="grant_item",
            actor_id="npc1",
            description="Grant a magic sword"
        )
        assert proposal.status == ApprovalStatus.PENDING
        assert proposal.id in workflow.pending

        # Immediately get_pending should still show it
        pending = workflow.get_pending()
        assert len(pending) == 1

        # Wait for timeout
        time.sleep(0.15)

        # Now get_pending should auto-approve and return empty
        pending = workflow.get_pending()
        assert len(pending) == 0

        # Should be in approved list
        approved = workflow.get_approved()
        assert len(approved) == 1
        assert approved[0].status == ApprovalStatus.APPROVED_AUTO

    def test_strict_mode_never_auto_approves(self):
        """In strict mode, proposals never auto-approve."""
        workflow = ApprovalWorkflow(mode="strict", timeout_seconds=0.1)

        proposal = workflow.propose(
            action_type="level_up",
            actor_id="player1",
            description="Level up to 5"
        )
        assert proposal.status == ApprovalStatus.PENDING

        # Wait past timeout
        time.sleep(0.15)

        # Should still be pending (strict mode ignores timeout)
        pending = workflow.get_pending()
        assert len(pending) == 1
        assert pending[0].id == proposal.id
        assert pending[0].status == ApprovalStatus.PENDING

    def test_gm_can_approve_before_timeout(self):
        """GM can explicitly approve before timeout expires."""
        workflow = ApprovalWorkflow(mode="timeout", timeout_seconds=1.0)

        proposal = workflow.propose(
            action_type="grant_item",
            actor_id="npc1",
            description="Grant a magic sword"
        )

        # GM approves immediately (before timeout)
        success = workflow.approve(proposal.id)
        assert success
        assert proposal.id not in workflow.pending

        # Wait past timeout
        time.sleep(0.2)

        # Should still be in approved (not moved to auto-approved)
        approved = workflow.get_approved()
        assert len(approved) == 1
        assert approved[0].status == ApprovalStatus.APPROVED

    def test_gm_can_reject_before_timeout(self):
        """GM can explicitly reject before timeout expires."""
        workflow = ApprovalWorkflow(mode="timeout", timeout_seconds=1.0)

        proposal = workflow.propose(
            action_type="modify_stat",
            actor_id="pc1",
            description="Lower AC to 5"
        )

        # GM rejects immediately
        success = workflow.reject(proposal.id)
        assert success
        assert proposal.id not in workflow.pending

        # Proposal should be rejected, not auto-approved
        rejected = workflow.get_rejected()
        assert len(rejected) == 1
        assert rejected[0].status == ApprovalStatus.REJECTED

    def test_multiple_proposals_with_different_ages(self):
        """Only proposals past timeout are auto-approved."""
        workflow = ApprovalWorkflow(mode="timeout", timeout_seconds=0.1)

        # Propose first action
        prop1 = workflow.propose(
            action_type="grant_item",
            actor_id="npc1",
            description="Item 1"
        )

        time.sleep(0.15)

        # Propose second action (not yet timed out)
        prop2 = workflow.propose(
            action_type="grant_item",
            actor_id="npc2",
            description="Item 2"
        )

        # get_pending should auto-approve first, keep second
        pending = workflow.get_pending()
        assert len(pending) == 1
        assert pending[0].id == prop2.id

        # First should be auto-approved
        approved = workflow.get_approved()
        assert len(approved) == 1
        assert approved[0].id == prop1.id

    def test_timeout_logged_as_warning(self, caplog):
        """Auto-approval logs as warning (not silent)."""
        import logging
        caplog.set_level(logging.WARNING)

        workflow = ApprovalWorkflow(mode="timeout", timeout_seconds=0.05)
        workflow.propose(
            action_type="grant_item",
            actor_id="npc1",
            description="Item"
        )

        time.sleep(0.1)
        workflow.get_pending()

        # Should have logged auto-approval warning
        assert any("Auto-approved" in record.message for record in caplog.records)
        assert any("timeout" in record.message.lower() for record in caplog.records)
