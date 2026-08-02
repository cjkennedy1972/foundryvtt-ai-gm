"""Tests for the canon proposal review API endpoints (api/routes/canon.py).

Calls the async route functions directly against a mocked AppState, matching
the direct-call convention used in test_gm_canon_command.py and
test_canon_proposals_db.py (no FastAPI TestClient in this codebase).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routes.canon import CanonApproveRequest, approve_canon_proposal, get_pending_canon_proposals, reject_canon_proposal


def _make_state(**overrides):
    state = SimpleNamespace(db=AsyncMock(), llm_manager=MagicMock())
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def test_get_pending_canon_proposals_passthrough():
    proposals = [{"id": 1, "fact": "The king is dead."}]
    state = _make_state()
    state.db.get_pending_canon_proposals = AsyncMock(return_value=proposals)

    result = asyncio.run(get_pending_canon_proposals(state))

    assert result == {"proposals": proposals}


def test_approve_nonexistent_proposal_returns_404():
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(approve_canon_proposal(1, CanonApproveRequest(), state))
    assert exc_info.value.status_code == 404


def test_reject_nonexistent_proposal_returns_404():
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(reject_canon_proposal(1, state))
    assert exc_info.value.status_code == 404


def test_reject_marks_rejected_and_removes_from_pending():
    state = _make_state()
    pending = {"id": 1, "fact": "f", "status": "pending"}
    rejected = {"id": 1, "fact": "f", "status": "rejected"}
    state.db.get_canon_proposal = AsyncMock(side_effect=[pending, pending, rejected])
    state.db.reject_canon_proposal = AsyncMock(return_value=True)

    result = asyncio.run(reject_canon_proposal(1, state))

    state.db.reject_canon_proposal.assert_awaited_once_with(1)
    assert result == rejected


def test_reject_already_reviewed_returns_409():
    """reject_canon_proposal's compare-and-swap (WHERE status='pending')
    returning False means someone else already reviewed it — must be a
    reported conflict, not a silent success."""
    state = _make_state()
    proposal = {"id": 1, "fact": "f", "status": "approved"}
    state.db.get_canon_proposal = AsyncMock(return_value=proposal)
    state.db.reject_canon_proposal = AsyncMock(return_value=False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(reject_canon_proposal(1, state))
    assert exc_info.value.status_code == 409


def test_approve_without_final_text_writes_original_fact(tmp_path, monkeypatch):
    proposal = {"id": 1, "campaign": "Test Campaign", "fact": "The bridge is out.", "status": "pending"}
    approved = {**proposal, "status": "approved"}
    state = _make_state()
    # 3 calls: the route's own existence pre-check, one inside the shared
    # approve_canon_proposal_with_vault_write helper, and the route's final
    # re-fetch of the now-approved row.
    state.db.get_canon_proposal = AsyncMock(side_effect=[proposal, proposal, approved])
    state.db.approve_canon_proposal = AsyncMock(return_value=True)

    import campaign.obsidian_sync as obsidian_sync
    monkeypatch.setattr(obsidian_sync, "resolve_vault_path", lambda _p: tmp_path)
    monkeypatch.setattr(obsidian_sync, "get_campaign_folder", lambda _vault, _name: tmp_path)

    result = asyncio.run(approve_canon_proposal(1, CanonApproveRequest(), state))

    state.db.approve_canon_proposal.assert_awaited_once_with(1, None)
    content = (tmp_path / "Canon.md").read_text(encoding="utf-8")
    assert "The bridge is out." in content
    # set_dynamic_canon_context must be called synchronously (not a coroutine left un-awaited)
    state.llm_manager.set_dynamic_canon_context.assert_called_once()
    assert "The bridge is out." in state.llm_manager.set_dynamic_canon_context.call_args[0][0]
    assert result == approved


def test_approve_with_final_text_writes_edited_wording(tmp_path, monkeypatch):
    proposal = {"id": 2, "campaign": "Test Campaign", "fact": "draft wording", "status": "pending"}
    approved = {**proposal, "fact": "GM-edited wording", "status": "approved"}
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(side_effect=[proposal, proposal, approved])
    state.db.approve_canon_proposal = AsyncMock(return_value=True)

    import campaign.obsidian_sync as obsidian_sync
    monkeypatch.setattr(obsidian_sync, "resolve_vault_path", lambda _p: tmp_path)
    monkeypatch.setattr(obsidian_sync, "get_campaign_folder", lambda _vault, _name: tmp_path)

    result = asyncio.run(
        approve_canon_proposal(2, CanonApproveRequest(final_text="GM-edited wording"), state)
    )

    state.db.approve_canon_proposal.assert_awaited_once_with(2, "GM-edited wording")
    content = (tmp_path / "Canon.md").read_text(encoding="utf-8")
    assert "GM-edited wording" in content
    assert "draft wording" not in content
    assert "GM-edited wording" in state.llm_manager.set_dynamic_canon_context.call_args[0][0]
    assert result == approved


def test_approve_already_reviewed_returns_409_without_writing_vault(tmp_path, monkeypatch):
    """approve_canon_proposal's compare-and-swap returning False (someone
    else already reviewed it) must stop BEFORE any vault write — not
    silently succeed and duplicate the fact."""
    proposal = {"id": 3, "campaign": "Test Campaign", "fact": "fact", "status": "approved"}
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(return_value=proposal)
    state.db.approve_canon_proposal = AsyncMock(return_value=False)

    import campaign.obsidian_sync as obsidian_sync
    write_calls = []
    monkeypatch.setattr(obsidian_sync, "resolve_vault_path", lambda _p: tmp_path)
    monkeypatch.setattr(obsidian_sync, "get_campaign_folder", lambda _vault, _name: tmp_path)
    orig_append = obsidian_sync.append_canon_fact
    async def _tracking_append(folder, text):
        write_calls.append(text)
        return await orig_append(folder, text)
    monkeypatch.setattr(obsidian_sync, "append_canon_fact", _tracking_append)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(approve_canon_proposal(3, CanonApproveRequest(), state))
    assert exc_info.value.status_code == 409
    assert write_calls == []


def test_approve_reverts_to_pending_and_returns_409_when_vault_write_fails(monkeypatch):
    """A vault-write failure must NOT leave the DB permanently 'approved'
    with the fact never actually written — it must revert to 'pending' and
    report the failure, so the GM can retry instead of losing the fact
    silently while seeing a success response."""
    proposal = {"id": 4, "campaign": "Test Campaign", "fact": "fact", "status": "pending"}
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(return_value=proposal)
    state.db.approve_canon_proposal = AsyncMock(return_value=True)
    state.db.revert_canon_proposal_to_pending = AsyncMock()

    import campaign.obsidian_sync as obsidian_sync

    def _boom(_p):
        raise OSError("vault unreachable")

    monkeypatch.setattr(obsidian_sync, "resolve_vault_path", _boom)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(approve_canon_proposal(4, CanonApproveRequest(), state))

    assert exc_info.value.status_code == 409
    state.db.approve_canon_proposal.assert_awaited_once_with(4, None)
    state.db.revert_canon_proposal_to_pending.assert_awaited_once_with(4)
