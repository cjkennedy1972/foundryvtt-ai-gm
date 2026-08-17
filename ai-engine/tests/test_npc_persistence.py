#!/usr/bin/env python3
"""Tests for npc.persistence.save/load — NPCRegistry surviving a restart.

Run:
    cd ai-engine && python -m pytest tests/test_npc_persistence.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npc import persistence
from npc.goals import Goal
from npc.registry import NPCRegistry
from persistence.db import Database


def test_save_then_load_restores_full_record(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight", class_name="Fighter", level=5, alignment="LG")
        reg.add_relationship("n1", "pc-1", "Aria", "enemy", strength=0.1)
        reg.add_goal("n1", Goal(description="seek revenge on the party", priority=10))

        await persistence.save(db, "Test Campaign", reg)

        loaded = await persistence.load(db, "Test Campaign")
        npc = loaded.get_npc("n1")
        assert npc.npc_name == "Mara"
        assert npc.class_name == "Fighter"
        assert npc.goals[0].description == "seek revenge on the party"
        assert loaded.get_relationship("n1", "pc-1").relationship_type == "enemy"
        await db.close()

    asyncio.run(run())


def test_load_with_no_stored_npcs_returns_empty_registry(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        loaded = await persistence.load(db, "Fresh Campaign")
        assert loaded.list_npcs() == []
        await db.close()

    asyncio.run(run())


def test_save_upserts_on_second_call(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        await persistence.save(db, "Test Campaign", reg)

        reg.add_goal("n1", Goal(description="new goal"))
        await persistence.save(db, "Test Campaign", reg)

        loaded = await persistence.load(db, "Test Campaign")
        assert len(loaded.list_npcs()) == 1
        assert loaded.get_npc("n1").goals[0].description == "new goal"
        await db.close()

    asyncio.run(run())


def test_scoped_to_campaign(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        await persistence.save(db, "Campaign A", reg)

        loaded = await persistence.load(db, "Campaign B")
        assert loaded.list_npcs() == []
        await db.close()

    asyncio.run(run())


def test_same_npc_id_in_two_campaigns_does_not_collide(tmp_path):
    """Regression: npc_id is derived from the NPC's display name
    (chat_listener.py register_npc(npc_id=npc_name, ...)), so two campaigns
    can plausibly reuse the same generic name (e.g. "Bartender"). Before
    the composite (npc_id, campaign) primary key, saving campaign B's NPC
    silently overwrote campaign A's row for the same npc_id."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()

        reg_a = NPCRegistry()
        reg_a.register_npc("bartender", "Old Tom", "A gruff dwarf")
        await persistence.save(db, "Campaign A", reg_a)

        reg_b = NPCRegistry()
        reg_b.register_npc("bartender", "Young Pete", "A nervous halfling")
        await persistence.save(db, "Campaign B", reg_b)

        loaded_a = await persistence.load(db, "Campaign A")
        loaded_b = await persistence.load(db, "Campaign B")
        assert loaded_a.get_npc("bartender").npc_name == "Old Tom"
        assert loaded_b.get_npc("bartender").npc_name == "Young Pete"

        await db.close()

    asyncio.run(run())
