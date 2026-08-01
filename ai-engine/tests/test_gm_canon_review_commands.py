"""Tests for /gm canon review|approve|reject — the lightweight chat-based
alternative to the admin panel's Canon Review page.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.chat_listener import ChatListener


def _make_listener(**overrides):
    kwargs = dict(
        foundry=MagicMock(),
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
    )
    kwargs.update(overrides)
    listener = ChatListener(**kwargs)
    listener.foundry.chat_message = AsyncMock()
    return listener


def _proposal(id_, fact="A fact.", confidence="high", rationale="r", contradiction_note=None, campaign="Test Campaign"):
    return {
        "id": id_, "session_id": "s1", "campaign": campaign, "fact": fact,
        "confidence": confidence, "rationale": rationale,
        "contradiction_note": contradiction_note, "status": "pending",
        "created_at": "2026-01-01T00:00:00", "reviewed_at": None,
    }


def _patch_vault(tmp_path):
    import campaign.obsidian_sync as obsidian_sync
    orig_resolve = obsidian_sync.resolve_vault_path
    orig_folder = obsidian_sync.get_campaign_folder
    obsidian_sync.resolve_vault_path = lambda _p: tmp_path
    obsidian_sync.get_campaign_folder = lambda _vault, _name: tmp_path
    return obsidian_sync, orig_resolve, orig_folder


def test_canon_review_lists_pending_proposals_with_contradiction_flagged():
    listener = _make_listener()
    listener.db.get_pending_canon_proposals = AsyncMock(return_value=[
        _proposal(1, fact="The king is dead.", contradiction_note="conflicts with: king was crowned last week"),
        _proposal(2, fact="The bridge is out."),
    ])

    asyncio.run(listener._handle_gm_command("GM", "/gm canon review"))

    message = listener.foundry.chat_message.call_args[0][0]
    assert "The king is dead." in message
    assert "conflicts with" in message
    assert "The bridge is out." in message
    assert listener._canon_review_ids == [1, 2]


def test_canon_review_empty_queue_says_so():
    listener = _make_listener()
    listener.db.get_pending_canon_proposals = AsyncMock(return_value=[])

    asyncio.run(listener._handle_gm_command("GM", "/gm canon review"))

    message = listener.foundry.chat_message.call_args[0][0]
    assert "no pending" in message.lower()


def test_canon_approve_requires_review_first():
    listener = _make_listener()

    asyncio.run(listener._handle_gm_command("GM", "/gm canon approve 1"))

    message = listener.foundry.chat_message.call_args[0][0]
    assert "review" in message.lower()


def test_canon_approve_rejects_out_of_range_index():
    listener = _make_listener()
    listener._canon_review_ids = [5]

    asyncio.run(listener._handle_gm_command("GM", "/gm canon approve 2"))

    message = listener.foundry.chat_message.call_args[0][0]
    assert "invalid" in message.lower()


def test_canon_approve_writes_to_vault_and_pushes_live(tmp_path):
    listener = _make_listener()
    listener._canon_review_ids = [42]
    listener.db.get_canon_proposal = AsyncMock(return_value=_proposal(42, fact="The tower fell."))
    listener.db.approve_canon_proposal = AsyncMock()

    obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm canon approve 1"))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    listener.db.approve_canon_proposal.assert_called_once_with(42)
    assert "The tower fell." in (tmp_path / "Canon.md").read_text(encoding="utf-8")
    # set_dynamic_canon_context must be called synchronously (not awaited).
    listener.llm.set_dynamic_canon_context.assert_called_once()
    pushed = listener.llm.set_dynamic_canon_context.call_args[0][0]
    assert "The tower fell." in pushed

    message = listener.foundry.chat_message.call_args[0][0]
    assert "approved" in message.lower()


def test_canon_reject_does_not_touch_vault(tmp_path):
    listener = _make_listener()
    listener._canon_review_ids = [7]
    listener.db.reject_canon_proposal = AsyncMock()

    asyncio.run(listener._handle_gm_command("GM", "/gm canon reject 1"))

    listener.db.reject_canon_proposal.assert_called_once_with(7)
    assert not (tmp_path / "Canon.md").exists()
    message = listener.foundry.chat_message.call_args[0][0]
    assert "rejected" in message.lower()
