"""Tests for canon-proposal generation firing during /gm end session — the
part of Conversation-to-Journal-Export that also seeds the canon review
queue with AI-proposed facts for the GM to approve/reject later.
"""

import asyncio
import json
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
    listener.foundry.create_entity = AsyncMock(return_value={"uuid": "JournalEntry.abc"})
    listener.db.get_active_session_info = AsyncMock(
        return_value={"session_id": "s1", "campaign": "Test Campaign"}
    )
    listener.db.close_session = AsyncMock()
    listener.db.create_canon_proposal = AsyncMock()
    return listener


def _patch_vault(tmp_path):
    import campaign.obsidian_sync as obsidian_sync
    orig_resolve = obsidian_sync.resolve_vault_path
    orig_folder = obsidian_sync.get_campaign_folder
    obsidian_sync.resolve_vault_path = lambda _p: tmp_path
    obsidian_sync.get_campaign_folder = lambda _vault, _name: tmp_path
    return obsidian_sync, orig_resolve, orig_folder


class _FakeLLMResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _reinforcement_mgr_with_highlights(highlights, summary="Some recap"):
    mgr = MagicMock()
    mgr.summarize_context = AsyncMock(return_value=summary)
    mgr.get_session_highlights = MagicMock(return_value=highlights)
    return mgr


def test_end_session_creates_canon_proposals_from_llm_response(tmp_path):
    reinforcement_mgr = _reinforcement_mgr_with_highlights(["The tower collapsed in the fight."])
    listener = _make_listener(reinforcement_mgr=reinforcement_mgr)
    listener.llm._http = MagicMock()
    listener.llm._http.post = AsyncMock(return_value=_FakeLLMResponse(json.dumps({
        "proposals": [
            {"fact": "The tower is destroyed.", "confidence": "high",
             "rationale": "Witnessed by the whole party.", "contradiction_note": None},
        ]
    })))

    obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm end session"))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    listener.db.create_canon_proposal.assert_called_once()
    kwargs = listener.db.create_canon_proposal.call_args.kwargs
    assert kwargs["fact"] == "The tower is destroyed."
    assert kwargs["confidence"] == "high"
    assert kwargs["session_id"] == "s1"
    assert kwargs["campaign"] == "Test Campaign"

    messages = [c[0][0] for c in listener.foundry.chat_message.call_args_list]
    assert any("canon proposal" in m.lower() for m in messages)


def test_end_session_skips_canon_generation_with_no_highlights(tmp_path):
    """No highlights recorded -> generate_canon_proposals short-circuits
    before ever calling the LLM (verified indirectly: no proposal message,
    no db writes)."""
    reinforcement_mgr = _reinforcement_mgr_with_highlights([])
    listener = _make_listener(reinforcement_mgr=reinforcement_mgr)
    listener.llm._http = MagicMock()
    listener.llm._http.post = AsyncMock()

    obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm end session"))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    listener.llm._http.post.assert_not_called()
    listener.db.create_canon_proposal.assert_not_called()


def test_end_session_survives_canon_generation_failure(tmp_path):
    """A canon-proposal generation failure must not block session close or
    the recap export that already succeeded."""
    reinforcement_mgr = _reinforcement_mgr_with_highlights(["Something happened."])
    listener = _make_listener(reinforcement_mgr=reinforcement_mgr)
    listener.llm._http = MagicMock()
    listener.llm._http.post = AsyncMock(side_effect=RuntimeError("LLM host unreachable"))

    obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm end session"))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    listener.foundry.create_entity.assert_called_once()  # recap still exported
    listener.db.close_session.assert_called_once_with("s1")  # session still closed
