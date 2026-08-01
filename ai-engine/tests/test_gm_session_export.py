"""Regression tests for /gm end session — Conversation to Journal Export.

Covers: recap generation via ContextReinforcementManager.summarize_context(),
dual write (Foundry JournalEntry + vault recap file), the session always
getting closed even if recap export fails, and the "no active session" guard.
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
    listener.foundry.create_entity = AsyncMock(return_value={"uuid": "JournalEntry.abc"})
    listener.db.get_active_session_info = AsyncMock(
        return_value={"session_id": "s1", "campaign": "Test Campaign"}
    )
    listener.db.close_session = AsyncMock()
    return listener


def _patch_vault(tmp_path):
    import campaign.obsidian_sync as obsidian_sync
    orig_resolve = obsidian_sync.resolve_vault_path
    orig_folder = obsidian_sync.get_campaign_folder
    obsidian_sync.resolve_vault_path = lambda _p: tmp_path
    obsidian_sync.get_campaign_folder = lambda _vault, _name: tmp_path
    return obsidian_sync, orig_resolve, orig_folder


def test_end_session_requires_active_session():
    listener = _make_listener()
    listener.db.get_active_session_info = AsyncMock(return_value=None)

    asyncio.run(listener._handle_gm_command("GM", "/gm end session"))

    listener.foundry.create_entity.assert_not_called()
    listener.db.close_session.assert_not_called()


def test_end_session_writes_journal_and_vault_recap_then_closes(tmp_path):
    reinforcement_mgr = MagicMock()
    reinforcement_mgr.summarize_context = AsyncMock(return_value="Key events: the dragon fled.")
    listener = _make_listener(reinforcement_mgr=reinforcement_mgr)

    obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm end session"))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    # Foundry journal write
    listener.foundry.create_entity.assert_called_once()
    entity_type, data = listener.foundry.create_entity.call_args[0]
    assert entity_type == "JournalEntry"
    assert "the dragon fled." in data["pages"][0]["text"]["content"]

    # Vault recap write
    recap_files = list((tmp_path / "Journal").glob("Session Recap*.md"))
    assert len(recap_files) == 1
    assert "the dragon fled." in recap_files[0].read_text(encoding="utf-8")

    # Session closed
    listener.db.close_session.assert_called_once_with("s1")


def test_end_session_still_closes_session_when_recap_export_fails(tmp_path):
    reinforcement_mgr = MagicMock()
    reinforcement_mgr.summarize_context = AsyncMock(return_value="Some recap")
    listener = _make_listener(reinforcement_mgr=reinforcement_mgr)
    listener.foundry.create_entity = AsyncMock(side_effect=RuntimeError("Foundry unreachable"))

    obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm end session"))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    listener.db.close_session.assert_called_once_with("s1")
    messages = [c[0][0] for c in listener.foundry.chat_message.call_args_list]
    assert any("failed" in m.lower() for m in messages)
    assert any("ended" in m.lower() for m in messages)


def test_end_session_falls_back_to_placeholder_without_reinforcement_mgr(tmp_path):
    """No reinforcement_mgr wired (e.g. minimal test harness) must not crash —
    it should fall back to a placeholder recap rather than erroring."""
    listener = _make_listener()  # no reinforcement_mgr override -> None

    obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm end session"))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    listener.foundry.create_entity.assert_called_once()
    _, data = listener.foundry.create_entity.call_args[0]
    assert "No session highlights recorded." in data["pages"][0]["text"]["content"]
    listener.db.close_session.assert_called_once_with("s1")
