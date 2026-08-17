"""Regression test: npc/persistence.py's save() was wired into "/gm end
session" but load() was never called anywhere in application code — the
registry was always created fresh at boot with nothing rehydrating it, so
NPC goals/relationships never actually survived a restart despite the
module's docstring promising they would. Fixed by loading into the existing
NPCRegistry (in place, since other components hold the same reference) at
"/gm start session". This test simulates a real restart: one ChatListener
ends a session (saving), a second ChatListener with a FRESH NPCRegistry
starts a new session for the same campaign and must rehydrate."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.chat_listener import ChatListener
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
    listener.state_tracker.save = AsyncMock()
    listener.sync_active_scene = AsyncMock()
    listener._process_proactive_action = AsyncMock()  # opening narration, irrelevant here
    return listener


def _patch_vault(tmp_path):
    import campaign.obsidian_sync as obsidian_sync
    orig_resolve = obsidian_sync.resolve_vault_path
    orig_folder = obsidian_sync.get_campaign_folder
    obsidian_sync.resolve_vault_path = lambda _p: tmp_path
    obsidian_sync.get_campaign_folder = lambda _vault, _name: tmp_path
    return obsidian_sync, orig_resolve, orig_folder


def test_npc_goals_survive_a_simulated_restart(tmp_path):
    async def run():
        db_path = str(tmp_path / "t.db")
        db = Database(db_path)
        await db.init()

        # --- "First process": play a session, then end it (saves NPCs) ---
        reg_a = NPCRegistry()
        reg_a.register_npc("n1", "Mara", "A vengeful knight")
        reg_a.add_goal("n1", Goal(description="seek revenge on the party", priority=5))
        listener_a = _make_listener(db, reg_a)

        obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
        try:
            await listener_a._cmd_start_session("Test Campaign")
            await listener_a._handle_gm_command("GM", "/gm end session")
        finally:
            obsidian_sync.resolve_vault_path = orig_resolve
            obsidian_sync.get_campaign_folder = orig_folder
        await db.close()

        # --- "Second process": fresh DB connection, brand-new empty registry,
        # exactly what main.py does cold at boot ---
        db2 = Database(db_path)
        await db2.init()
        reg_b = NPCRegistry()  # cold, like main.py's npc_registry = NPCRegistry()
        assert reg_b.list_npcs() == []  # confirms this really is empty beforehand

        listener_b = _make_listener(db2, reg_b)
        obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
        try:
            await listener_b._cmd_start_session("Test Campaign")
        finally:
            obsidian_sync.resolve_vault_path = orig_resolve
            obsidian_sync.get_campaign_folder = orig_folder

        rehydrated = reg_b.get_npc("n1")
        assert rehydrated is not None, "NPC did not survive the simulated restart"
        assert rehydrated.npc_name == "Mara"
        assert rehydrated.goals[0].description == "seek revenge on the party"

        await db2.close()

    asyncio.run(run())


def test_start_session_with_no_prior_save_leaves_registry_untouched(tmp_path):
    """First-ever session for a campaign: nothing persisted yet, so
    start_session must not wipe out NPCs already registered by other
    bootstrap logic (e.g. context/loader.py's campaign NPC seeding)."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()

        reg = NPCRegistry()
        reg.register_npc("n1", "Bartender", "A gruff dwarf")
        listener = _make_listener(db, reg)

        obsidian_sync, orig_resolve, orig_folder = _patch_vault(tmp_path)
        try:
            await listener._cmd_start_session("Brand New Campaign")
        finally:
            obsidian_sync.resolve_vault_path = orig_resolve
            obsidian_sync.get_campaign_folder = orig_folder

        assert reg.get_npc("n1") is not None
        assert reg.get_npc("n1").npc_name == "Bartender"

        await db.close()

    asyncio.run(run())
