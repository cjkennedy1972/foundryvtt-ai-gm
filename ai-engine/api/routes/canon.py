"""Canon proposal review endpoints: list pending, approve, reject."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import AppState, get_app_state
from config import settings
from context.canon import approve_canon_proposal_with_vault_write, reject_canon_proposal_safely

logger = logging.getLogger("ai-gm")

router = APIRouter(tags=["canon"])


class CanonApproveRequest(BaseModel):
    final_text: Optional[str] = None


@router.get("/api/canon/pending")
async def get_pending_canon_proposals(state: AppState = Depends(get_app_state)):
    """List all pending canon proposals awaiting GM review."""
    proposals = await state.db.get_pending_canon_proposals()
    return {"proposals": proposals}


@router.post("/api/canon/{proposal_id}/approve")
async def approve_canon_proposal(
    proposal_id: int, request: CanonApproveRequest, state: AppState = Depends(get_app_state)
):
    """Approve a pending canon proposal and push the fact to the vault + live context.

    Approval is atomic (compare-and-swap on status='pending' — a proposal
    can never be approved/written twice even if two requests race) and the
    vault write is reverted back to 'pending' on failure rather than left
    permanently 'approved' with the fact never actually written anywhere.
    See context.canon.approve_canon_proposal_with_vault_write.
    """
    existing = await state.db.get_canon_proposal(proposal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Canon proposal not found")

    success, message = await approve_canon_proposal_with_vault_write(
        state.db, state.llm_manager, proposal_id, settings.campaign_vault_path, request.final_text,
    )
    if not success:
        raise HTTPException(status_code=409, detail=message)

    return await state.db.get_canon_proposal(proposal_id)


@router.post("/api/canon/{proposal_id}/reject")
async def reject_canon_proposal(proposal_id: int, state: AppState = Depends(get_app_state)):
    """Reject a pending canon proposal."""
    existing = await state.db.get_canon_proposal(proposal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Canon proposal not found")

    success, message = await reject_canon_proposal_safely(state.db, proposal_id)
    if not success:
        raise HTTPException(status_code=409, detail=message)

    return await state.db.get_canon_proposal(proposal_id)
