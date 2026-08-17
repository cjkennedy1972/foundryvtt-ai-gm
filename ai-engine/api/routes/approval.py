"""Approval workflow API endpoints — GM approval gates for consequential actions.

Provides endpoints for viewing pending proposals and approving/rejecting them.
"""

from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


class ActionProposalResponse(BaseModel):
    """Proposal response model."""
    id: str
    action_type: str
    actor_id: Optional[str] = None
    target_id: Optional[str] = None
    description: str = ""
    reasoning: str = ""
    status: str = "pending"


class ApprovalDecisionRequest(BaseModel):
    """Request to approve or reject a proposal."""
    decision: str  # "approve" or "reject"
    notes: Optional[str] = None


def create_approval_router(app_state) -> APIRouter:
    """Create approval workflow endpoints.

    Args:
        app_state: AppState instance with access to ChatListener, etc.

    Returns:
        APIRouter with approval endpoints
    """
    router = APIRouter(prefix="/api/approval", tags=["approval"])

    @router.get("/pending", response_model=List[ActionProposalResponse])
    async def get_pending_proposals():
        """Get all pending proposals awaiting GM approval."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener or not hasattr(listener, "_approval_workflow"):
                return []

            pending = listener._approval_workflow.get_pending()
            return [
                ActionProposalResponse(
                    id=p.id,
                    action_type=p.action_type,
                    actor_id=p.actor_id,
                    target_id=p.target_id,
                    description=p.description,
                    reasoning=p.reasoning,
                    status=p.status.value if hasattr(p.status, 'value') else str(p.status),
                )
                for p in pending
            ]
        except Exception as e:
            logger.error(f"Failed to get pending proposals: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/{proposal_id}/approve")
    async def approve_proposal(proposal_id: str):
        """GM approves a proposal."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener or not hasattr(listener, "_approval_workflow"):
                raise HTTPException(status_code=400, detail="Approval system not available")

            success = listener._approval_workflow.approve(proposal_id)
            if not success:
                raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")

            logger.info(f"Approved proposal {proposal_id} via API")
            return {"status": "approved", "proposal_id": proposal_id}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to approve proposal: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/{proposal_id}/reject")
    async def reject_proposal(proposal_id: str):
        """GM rejects a proposal."""
        try:
            listener = getattr(app_state, "chat_listener", None)
            if not listener or not hasattr(listener, "_approval_workflow"):
                raise HTTPException(status_code=400, detail="Approval system not available")

            success = listener._approval_workflow.reject(proposal_id)
            if not success:
                raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")

            logger.info(f"Rejected proposal {proposal_id} via API")
            return {"status": "rejected", "proposal_id": proposal_id}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to reject proposal: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
