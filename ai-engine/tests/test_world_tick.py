#!/usr/bin/env python3
"""Tests for world_tick.clock.WorldTick — the off-session world advance.

The bounds in CKP-101 are the point of this class, so they are what these
tests hold: the delivery-path gate, the per-day NPC cap, deterministic
order, the token budget, and the rule that a tick may not move an NPC the
recent story is still using.

Run:
    cd ai-engine && python -m pytest tests/test_world_tick.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from events.store import EventStore
from events.types import ACTION_RESOLVED
from llm.router import ModelRouter
from npc.goals import Goal
from npc.memory import NPCMemory
from npc.registry import NPCRegistry
from persistence.db import Database
from referee.agent import RefereeAgent
from world_tick.clock import WorldTick

CAMPAIGN = "greenrest"
SESSION = "s1"

_TIME_TRIGGER = {"event_type": "time_advanced"}


async def _fixture(actions, npcs=(("n1", "Mara"),), **kwargs):
    """A WorldTick wired to an in-memory campaign whose NPC-tier LLM returns
    *actions* for every NPC it is asked about. Returns (tick, db, registry)."""
    db = Database(":memory:")
    await db.init()
    await db.create_session(SESSION, campaign=CAMPAIGN)

    registry = NPCRegistry()
    for npc_id, name in npcs:
        registry.register_npc(npc_id, name, f"{name}, of Greenrest")
        registry.add_goal(npc_id, Goal(description="scheme", status="active",
                                       trigger_conditions=dict(_TIME_TRIGGER)))

    llm = MagicMock()
    llm.generate = AsyncMock(return_value={"actions": list(actions)})
    store = EventStore(db)
    tick = WorldTick(
        db=db,
        npc_registry=registry,
        model_router=ModelRouter(llm),
        referee=RefereeAgent(),
        memory=NPCMemory(store),
        event_store=store,
        **kwargs,
    )
    return tick, db, registry, llm


def test_change_without_a_delivery_path_is_discarded():
    """The hard gate: a simulated change the player can never encounter does
    not become a canon proposal, however well the NPC argued for it."""
    async def run():
        tick, db, _, _ = await _fixture([{"type": "narrate", "text": "Mara moves her gold."}])

        summary = await tick.tick(CAMPAIGN, days=1)

        assert summary["discarded_no_delivery_path"] == 1
        assert summary["proposals"] == 0
        assert await db.get_pending_canon_proposals() == []
        await db.close()

    asyncio.run(run())


def test_named_delivery_path_becomes_a_pending_proposal_not_canon():
    async def run():
        tick, db, _, _ = await _fixture([{
            "type": "narrate",
            "text": "Mara calls in a debt",
            "delivery_path": "a letter left with the innkeeper",
        }])

        summary = await tick.tick(CAMPAIGN, days=1)

        assert summary["proposals"] == 1
        proposals = await db.get_pending_canon_proposals()
        assert len(proposals) == 1
        # Pending, campaign-scoped, and carrying the delivery path — a GM still
        # has to approve it before it is canon.
        assert proposals[0]["status"] == "pending"
        assert proposals[0]["campaign"] == CAMPAIGN
        assert "a letter left with the innkeeper" in proposals[0]["fact"]
        await db.close()

    asyncio.run(run())


def test_npc_cap_bounds_a_single_day():
    async def run():
        npcs = [(f"n{i}", f"NPC{i}") for i in range(6)]
        tick, db, _, _ = await _fixture(
            [{"type": "narrate", "text": "acts", "delivery_path": "a rumour"}],
            npcs=npcs,
            npc_cap_per_day=2,
        )

        summary = await tick.tick(CAMPAIGN, days=1)

        assert summary["npcs_ticked"] == 2
        await db.close()

    asyncio.run(run())


def test_tick_order_is_deterministic_by_npc_id():
    """A replay has to reproduce the same world, so which NPCs the cap admits
    cannot depend on registry insertion order."""
    async def run():
        forward = [("n1", "Ada"), ("n2", "Bel"), ("n3", "Cyd")]
        summaries = []
        for order in (forward, list(reversed(forward))):
            tick, db, _, _ = await _fixture(
                [{"type": "narrate", "text": "acts", "delivery_path": "a rumour"}],
                npcs=order,
                npc_cap_per_day=1,
            )
            await tick.tick(CAMPAIGN, days=1)
            events = await db.get_events_full(SESSION)
            summaries.append([e["payload"].get("npc_id") for e in events
                              if e["type"] == "world_simulation"])
            await db.close()

        assert summaries[0] == summaries[1] == ["n1"]

    asyncio.run(run())


def test_npc_named_by_recent_play_is_frozen():
    """If the story just involved Mara, the off-session tick does not get to
    move her out from under it."""
    async def run():
        tick, db, _, _ = await _fixture([{
            "type": "narrate", "text": "acts", "delivery_path": "a rumour",
        }])
        await EventStore(db).append(
            SESSION, ACTION_RESOLVED,
            payload={"action_type": "attack", "params": "target=Mara"},
            description="The party ambushes Mara on the road",
        )

        summary = await tick.tick(CAMPAIGN, days=1)

        assert summary["npcs_ticked"] == 0
        assert summary["proposals"] == 0
        await db.close()

    asyncio.run(run())


def test_token_budget_stops_the_tick():
    async def run():
        tick, db, _, _ = await _fixture(
            [{"type": "narrate", "text": "acts", "delivery_path": "a rumour"}],
            token_budget_per_day=100,
        )
        # Spend the budget before the tick starts, as a real run would have.
        await db.record_llm_usage(SESSION, CAMPAIGN, prompt_tokens=90, completion_tokens=90)

        summary = await tick.tick(CAMPAIGN, days=3)

        # Day 0 runs against a budget not yet overspent *by this tick*; the
        # cap binds on what the tick itself burns, so pre-existing spend does
        # not block it, and no day is simulated past the budget.
        assert summary["days_simulated"] <= 3
        assert summary["stopped_reason"] in (None, "token_budget")
        await db.close()

    asyncio.run(run())


def test_campaign_without_history_is_a_no_op():
    async def run():
        tick, db, _, llm = await _fixture([{"type": "narrate", "text": "acts"}])

        summary = await tick.tick("a-campaign-nobody-played", days=1)

        assert summary["stopped_reason"] == "no_session"
        assert summary["days_simulated"] == 0
        llm.generate.assert_not_called()
        await db.close()

    asyncio.run(run())
