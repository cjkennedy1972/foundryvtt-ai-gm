"""Tests for the /gm end session -> WorldClockAgent.advance() + NPC
persistence wiring added in Phase 4 (worldclock/agent.py)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.chat_listener import ChatListener
from npc import persistence as npc_persistence
from npc.goals import Goal
from npc.registry import NPCRegistry
from persistence.db import Database


def _make_listener(db, npc_registry, **overrides):
    kwargs = dict(
        foundry=MagicMock(),
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=db,
        npc_registry=npc_registry,
    )
    kwargs.update(overrides)
    listener = ChatListener(**kwargs)
    listener.foundry.chat_message = AsyncMock()
    listener.foundry.create_entity = AsyncMock(return_value={"uuid": "JournalEntry.abc"})
    return listener


def _patch_vault(tmp_path):
    import campaign.obsidian_sync as obsidian_sync
    orig_resolve = obsidian_sync.resolve_vault_path
    orig_folder = obsidian_sync.get_campaign_folder
    obsidian_sync.resolve_vault_path = lambda _p: tmp_path
    obsidian_sync.get_campaign_folder = lambda _vault, _name: tmp_path
    return obsidian_sync, orig_resolve, orig_folder


def test_end_session_advances_world_clock_and_persists_npcs(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(
            description="seek revenge on the party",
            trigger_conditions={"event_type": "time_advanced"},
        ))

        listener = _make_listener(db, reg)
        obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
        try:
            await listener._handle_gm_command("GM", "/gm end session")
        finally:
            obsidian_sync.resolve_vault_path = orig_resolve
            obsidian_sync.get_campaign_folder = orig_folder

        # World clock advanced and the goal activated in-memory.
        assert reg.get_npc("n1").goals[0].status == "active"

        # And that change was persisted, not just held in memory.
        loaded = await npc_persistence.load(db, "Test Campaign")
        assert loaded.get_npc("n1").goals[0].status == "active"

        state = await listener._event_store.replay("s1")
        assert state["world_time_elapsed_seconds"] > 0

        await db.close()

    asyncio.run(run())


def test_end_session_actually_acts_on_time_triggered_goal(tmp_path):
    """Regression: WorldClockAgent.advance() only *activates* a matching
    goal — it has no LLM access to act on it. Before this fix, nothing was
    ever invoked with a time_advanced event, so a goal gated on
    {"event_type": "time_advanced"} sat 'active' forever and NPCAgent never
    ran for it. This proves the NPC's turn actually happens end-to-end."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(
            description="seek revenge on the party",
            trigger_conditions={"event_type": "time_advanced"},
        ))

        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "narrate", "text": "Mara sharpens her blade in the dark."}]
        })
        listener = _make_listener(db, reg, llm=llm)
        listener.dispatcher.execute_batch = AsyncMock(
            side_effect=lambda actions: [{"type": a.get("type"), "success": True} for a in actions]
        )

        obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
        try:
            await listener._handle_gm_command("GM", "/gm end session")
        finally:
            obsidian_sync.resolve_vault_path = orig_resolve
            obsidian_sync.get_campaign_folder = orig_folder

        # The NPC actually got a turn and dispatched an action, not just a
        # goal flipped to 'active' with nothing ever consuming it.
        listener.dispatcher.execute_batch.assert_called_once()
        assert reg.get_npc("n1").goals[0].status == "done"

        await db.close()

    asyncio.run(run())


def test_end_session_without_npc_registry_skips_world_clock(tmp_path):
    """No npc_registry passed -> self._world_clock is None -> end session
    still completes normally (existing behavior unaffected)."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        listener = _make_listener(db, npc_registry=None)
        assert listener._world_clock is None

        obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
        try:
            await listener._handle_gm_command("GM", "/gm end session")
        finally:
            obsidian_sync.resolve_vault_path = orig_resolve
            obsidian_sync.get_campaign_folder = orig_folder

        session_info = await db.get_active_session_info()
        assert session_info is None  # session was closed successfully

        await db.close()

    asyncio.run(run())
