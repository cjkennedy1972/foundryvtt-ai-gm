"""Canon proposal review endpoints: list pending, approve, reject."""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import AppState, get_app_state
from campaign.obsidian_sync import append_canon_fact, get_campaign_folder, resolve_vault_path
from config import settings

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
    """Approve a pending canon proposal and push the fact to the vault + live context."""
    proposal = await state.db.get_canon_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Canon proposal not found")

    await state.db.approve_canon_proposal(proposal_id, request.final_text)

    try:
        vault_path = resolve_vault_path(settings.campaign_vault_path)
        campaign_folder = get_campaign_folder(vault_path, proposal["campaign"])
        fact_text = request.final_text or proposal["fact"]
        canon_file = await append_canon_fact(campaign_folder, fact_text)

        if state.llm_manager:
            # Read the fresh write directly — get_canon_context_sync() would
            # return the campaign loader's stale in-memory snapshot from
            # campaign-load time, not this just-written fact.
            canon_content = await asyncio.to_thread(canon_file.read_text, encoding="utf-8")
            state.llm_manager.set_dynamic_canon_context(f"## Canon / Established Facts ##\n{canon_content}")
    except Exception as e:
        logger.warning(f"Failed to push approved canon fact to vault: {e}")

    return await state.db.get_canon_proposal(proposal_id)


@router.post("/api/canon/{proposal_id}/reject")
async def reject_canon_proposal(proposal_id: int, state: AppState = Depends(get_app_state)):
    """Reject a pending canon proposal."""
    proposal = await state.db.get_canon_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Canon proposal not found")

    await state.db.reject_canon_proposal(proposal_id)
    return await state.db.get_canon_proposal(proposal_id)
