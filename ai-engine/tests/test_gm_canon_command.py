"""Regression tests for the /gm rule|canonize GM-directive commands and the
append_canon_fact vault writer.

Covers the two bugs caught in review before this landed:
- set_dynamic_canon_context must be called synchronously, not awaited.
- the fresh write must be read back directly from disk, not via
  campaign_loader.get_canon_context_sync() (which reflects an in-memory
  snapshot from campaign-load time and would miss the just-written fact).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from campaign.obsidian_sync import append_canon_fact
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


def test_append_canon_fact_creates_file_with_header(tmp_path):
    result = asyncio.run(append_canon_fact(tmp_path, "The bridge collapsed."))

    assert result == tmp_path / "Canon.md"
    content = result.read_text(encoding="utf-8")
    assert content.startswith("# Canon\n\n")
    assert "The bridge collapsed." in content


def test_append_canon_fact_preserves_prior_entries(tmp_path):
    asyncio.run(append_canon_fact(tmp_path, "First fact."))
    asyncio.run(append_canon_fact(tmp_path, "Second fact."))

    content = (tmp_path / "Canon.md").read_text(encoding="utf-8")
    assert "First fact." in content
    assert "Second fact." in content


def test_gm_rule_command_requires_active_session():
    listener = _make_listener()
    listener.db.get_active_session_info = AsyncMock(return_value=None)

    asyncio.run(listener._handle_gm_command("GM", "/gm rule The king is dead."))

    message = listener.foundry.chat_message.call_args[0][0]
    assert "active session" in message.lower()


def test_gm_rule_command_pushes_fresh_content_not_stale_snapshot(tmp_path):
    """set_dynamic_canon_context must receive the just-written fact, not
    whatever campaign_loader.get_canon_context_sync() (a stale in-memory
    snapshot) would have returned."""
    stale_loader = MagicMock()
    stale_loader.get_canon_context_sync.return_value = "## Canon ##\n(nothing yet)"

    listener = _make_listener(campaign_loader=stale_loader)
    listener.db.get_active_session_info = AsyncMock(
        return_value={"session_id": "s1", "campaign": "Test Campaign"}
    )

    import campaign.obsidian_sync as obsidian_sync
    orig_resolve = obsidian_sync.resolve_vault_path
    orig_folder = obsidian_sync.get_campaign_folder
    obsidian_sync.resolve_vault_path = lambda _p: tmp_path
    obsidian_sync.get_campaign_folder = lambda _vault, _name: tmp_path
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm rule The bridge is out."))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    # set_dynamic_canon_context must be called synchronously (not awaited —
    # it's a plain method), and with content that includes the fresh fact.
    listener.llm.set_dynamic_canon_context.assert_called_once()
    pushed_content = listener.llm.set_dynamic_canon_context.call_args[0][0]
    assert "The bridge is out." in pushed_content
    assert "(nothing yet)" not in pushed_content

    confirmation = listener.foundry.chat_message.call_args[0][0]
    assert "Canon updated" in confirmation


def test_gm_canonize_command_is_an_alias_for_rule(tmp_path):
    listener = _make_listener()
    listener.db.get_active_session_info = AsyncMock(
        return_value={"session_id": "s1", "campaign": "Test Campaign"}
    )

    import campaign.obsidian_sync as obsidian_sync
    orig_resolve = obsidian_sync.resolve_vault_path
    orig_folder = obsidian_sync.get_campaign_folder
    obsidian_sync.resolve_vault_path = lambda _p: tmp_path
    obsidian_sync.get_campaign_folder = lambda _vault, _name: tmp_path
    try:
        asyncio.run(listener._handle_gm_command("GM", "/gm canonize The oracle was right."))
    finally:
        obsidian_sync.resolve_vault_path = orig_resolve
        obsidian_sync.get_campaign_folder = orig_folder

    assert "The oracle was right." in (tmp_path / "Canon.md").read_text(encoding="utf-8")


def test_gm_end_session_command_requires_active_session():
    """Full end-session behavior (recap export, close_session) is covered in
    test_gm_session_export.py — this only checks the same "no active session"
    guard used by /gm rule|canonize."""
    listener = _make_listener()
    listener.db.get_active_session_info = AsyncMock(return_value=None)

    asyncio.run(listener._handle_gm_command("GM", "/gm end session"))

    message = listener.foundry.chat_message.call_args[0][0]
    assert "no active session" in message.lower()
