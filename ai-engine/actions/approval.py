"""Action approval workflow — gate consequential mutations with GM sign-off.

Implements optional approval flow for actions that modify player stats, items,
or levels. Prevents unintended autonomous changes while allowing flavor actions
to proceed without friction.

Actions requiring approval:
- grant_item / remove_item (inventory changes)
- modify_stat (ability/skill score changes)
- heal / damage (health changes)
- level_up (character advancement)
- apply_condition (status effects)

Actions that DON'T require approval:
- narrate (narrative descriptions)
- describe_scene (scene setup)
- cast_spell (spell effects if not damage)
- move_token (positioning)
- trigger_sound (audio cues)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import logging
import time

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    """Approval workflow states."""
    PENDING = "pending"      # Waiting for GM review
    APPROVED = "approved"    # GM approved, execute
    REJECTED = "rejected"    # GM rejected, don't execute
    APPROVED_AUTO = "approved_auto"  # Auto-approved (non-consequential)


# Actions that require GM approval before execution
CONSEQUENTIAL_ACTIONS = {
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

# Actions that auto-approve (never need GM review)
SAFE_ACTIONS = {
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


@dataclass
class ActionProposal:
    """An action awaiting GM approval."""

    id: str
    action_type: str
    actor_id: Optional[str] = None  # NPC or player ID
    target_id: Optional[str] = None  # Target NPC/player
    parameters: Dict[str, Any] = field(default_factory=dict)
    description: str = ""  # Human-readable summary
    reasoning: str = ""    # Why the AI chose this action
    requires_approval: bool = True
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=time.time)  # Timestamp for timeout

    def summary(self) -> str:
        """One-liner for GM display."""
        if self.target_id:
            return f"{self.action_type}: {self.actor_id} → {self.target_id} ({self.description})"
        return f"{self.action_type}: {self.description}"


class ApprovalWorkflow:
    """Manages action approval workflow.

    Supports two modes:
    - "timeout": Auto-approve consequential actions after timeout (unattended/autonomous)
    - "strict": Require explicit GM approval (attended mode)
    """

    def __init__(self, mode: str = "timeout", timeout_seconds: int = 20):
        """
        Args:
            mode: "timeout" (auto-approve after delay) or "strict" (require explicit approval)
            timeout_seconds: Seconds before auto-approving pending actions (timeout mode only)
        """
        self.pending: Dict[str, ActionProposal] = {}
        self.approved: List[ActionProposal] = []
        self.rejected: List[ActionProposal] = []
        self.mode = mode
        self.timeout_seconds = timeout_seconds

    def propose(
        self,
        action_type: str,
        actor_id: Optional[str] = None,
        target_id: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        description: str = "",
        reasoning: str = "",
    ) -> ActionProposal:
        """Submit an action for approval workflow.

        Args:
            action_type: Type of action (e.g., "grant_item")
            actor_id: Who is performing the action
            target_id: Who is affected
            parameters: Action parameters
            description: Human-readable description
            reasoning: Why this action was chosen

        Returns:
            ActionProposal with pending status
        """
        requires_approval = action_type in CONSEQUENTIAL_ACTIONS

        # Generate ID
        proposal_id = f"{action_type}-{actor_id or 'gm'}-{len(self.pending)}"

        proposal = ActionProposal(
            id=proposal_id,
            action_type=action_type,
            actor_id=actor_id,
            target_id=target_id,
            parameters=parameters or {},
            description=description,
            reasoning=reasoning,
            requires_approval=requires_approval,
            status=ApprovalStatus.APPROVED_AUTO if not requires_approval else ApprovalStatus.PENDING,
        )

        if requires_approval:
            self.pending[proposal_id] = proposal
            logger.info(f"Proposed action {proposal_id}: {proposal.summary()}")
        else:
            self.approved.append(proposal)
            logger.debug(f"Auto-approved action {proposal_id}: {proposal.summary()}")

        return proposal

    def approve(self, proposal_id: str) -> bool:
        """GM approves a proposal."""
        if proposal_id not in self.pending:
            logger.warning(f"Approval: proposal {proposal_id} not found")
            return False

        proposal = self.pending.pop(proposal_id)
        proposal.status = ApprovalStatus.APPROVED
        self.approved.append(proposal)
        logger.info(f"Approved: {proposal.summary()}")
        return True

    def reject(self, proposal_id: str) -> bool:
        """GM rejects a proposal."""
        if proposal_id not in self.pending:
            logger.warning(f"Rejection: proposal {proposal_id} not found")
            return False

        proposal = self.pending.pop(proposal_id)
        proposal.status = ApprovalStatus.REJECTED
        self.rejected.append(proposal)
        logger.info(f"Rejected: {proposal.summary()}")
        return True

    def _process_timeouts(self) -> None:
        """Auto-approve timed-out proposals in timeout mode."""
        if self.mode != "timeout":
            return

        now = time.time()
        timed_out = []

        for proposal_id, proposal in list(self.pending.items()):
            age = now - proposal.created_at
            if age > self.timeout_seconds:
                timed_out.append(proposal_id)

        for proposal_id in timed_out:
            proposal = self.pending.pop(proposal_id)
            proposal.status = ApprovalStatus.APPROVED_AUTO
            self.approved.append(proposal)
            logger.warning(
                f"Auto-approved (timeout {self.timeout_seconds}s): {proposal.summary()}"
            )

    def get_pending(self) -> List[ActionProposal]:
        """Get all pending proposals, auto-approving timed-out ones if in timeout mode."""
        self._process_timeouts()
        return list(self.pending.values())

    def get_approved(self, limit: int = 20) -> List[ActionProposal]:
        """Get recent approved actions."""
        return self.approved[-limit:]

    def get_rejected(self, limit: int = 20) -> List[ActionProposal]:
        """Get recent rejected actions."""
        return self.rejected[-limit:]

    def is_consequential(self, action_type: str) -> bool:
        """Check if action type requires approval."""
        return action_type in CONSEQUENTIAL_ACTIONS

    def clear_history(self) -> None:
        """Clear approved/rejected history (keep pending)."""
        self.approved.clear()
        self.rejected.clear()
        logger.info("Cleared approval history")
