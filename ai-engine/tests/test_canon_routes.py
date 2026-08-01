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
    state.db.get_canon_proposal = AsyncMock(side_effect=[pending, rejected])
    state.db.reject_canon_proposal = AsyncMock()

    result = asyncio.run(reject_canon_proposal(1, state))

    state.db.reject_canon_proposal.assert_awaited_once_with(1)
    assert result == rejected


def test_approve_without_final_text_writes_original_fact(tmp_path, monkeypatch):
    import api.routes.canon as canon_module

    proposal = {"id": 1, "campaign": "Test Campaign", "fact": "The bridge is out.", "status": "pending"}
    approved = {**proposal, "status": "approved"}
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(side_effect=[proposal, approved])
    state.db.approve_canon_proposal = AsyncMock()

    monkeypatch.setattr(canon_module, "resolve_vault_path", lambda _p: tmp_path)
    monkeypatch.setattr(canon_module, "get_campaign_folder", lambda _vault, _name: tmp_path)

    result = asyncio.run(approve_canon_proposal(1, CanonApproveRequest(), state))

    state.db.approve_canon_proposal.assert_awaited_once_with(1, None)
    content = (tmp_path / "Canon.md").read_text(encoding="utf-8")
    assert "The bridge is out." in content
    # set_dynamic_canon_context must be called synchronously (not a coroutine left un-awaited)
    state.llm_manager.set_dynamic_canon_context.assert_called_once()
    assert "The bridge is out." in state.llm_manager.set_dynamic_canon_context.call_args[0][0]
    assert result == approved


def test_approve_with_final_text_writes_edited_wording(tmp_path, monkeypatch):
    import api.routes.canon as canon_module

    proposal = {"id": 2, "campaign": "Test Campaign", "fact": "draft wording", "status": "pending"}
    approved = {**proposal, "fact": "GM-edited wording", "status": "approved"}
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(side_effect=[proposal, approved])
    state.db.approve_canon_proposal = AsyncMock()

    monkeypatch.setattr(canon_module, "resolve_vault_path", lambda _p: tmp_path)
    monkeypatch.setattr(canon_module, "get_campaign_folder", lambda _vault, _name: tmp_path)

    result = asyncio.run(
        approve_canon_proposal(2, CanonApproveRequest(final_text="GM-edited wording"), state)
    )

    state.db.approve_canon_proposal.assert_awaited_once_with(2, "GM-edited wording")
    content = (tmp_path / "Canon.md").read_text(encoding="utf-8")
    assert "GM-edited wording" in content
    assert "draft wording" not in content
    assert "GM-edited wording" in state.llm_manager.set_dynamic_canon_context.call_args[0][0]
    assert result == approved


def test_approve_survives_vault_write_failure(monkeypatch):
    """DB-level approval must still succeed even if the vault write fails
    (fail-open, matching the /gm end session philosophy)."""
    import api.routes.canon as canon_module

    proposal = {"id": 3, "campaign": "Test Campaign", "fact": "fact", "status": "pending"}
    approved = {**proposal, "status": "approved"}
    state = _make_state()
    state.db.get_canon_proposal = AsyncMock(side_effect=[proposal, approved])
    state.db.approve_canon_proposal = AsyncMock()

    def _boom(_p):
        raise OSError("vault unreachable")

    monkeypatch.setattr(canon_module, "resolve_vault_path", _boom)

    result = asyncio.run(approve_canon_proposal(3, CanonApproveRequest(), state))

    state.db.approve_canon_proposal.assert_awaited_once_with(3, None)
    assert result == approved
