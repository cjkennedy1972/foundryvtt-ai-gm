"""End-to-end test for Phase 5: an NPC with an active goal self-initiates
an action within the same turn a player's action is dispatched and
recorded — the scenario the implementation plan called out as the thing to
verify ("an NPC with a 'seek revenge' goal takes a self-initiated action
within N triggering events, without being player-prompted")."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.chat_listener import ChatListener
from npc.goals import Goal
from npc.registry import NPCRegistry
from persistence.db import Database


def _make_listener(db, npc_registry, llm):
    listener = ChatListener(
        foundry=MagicMock(),
        llm=llm,
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=db,
        npc_registry=npc_registry,
    )
    listener.dispatcher.execute_batch = AsyncMock(
        side_effect=lambda actions: [{"type": a.get("type"), "success": True} for a in actions]
    )
    return listener


def test_npc_self_initiates_after_player_action_resolves(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A vengeful knight")
        reg.add_goal("n1", Goal(
            description="seek revenge on the party",
            trigger_conditions={"event_type": "action_resolved"},
        ))

        # First call is the player's own turn (no actions here matter);
        # second call is Mara's NPCAgent turn, routed through the same
        # LLMManager since no second model tier is configured.
        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "narrate", "text": "Mara draws her blade."}]
        })

        listener = _make_listener(db, reg, llm)

        # Simulate a player action having just been dispatched and recorded —
        # this is the call site _process_player_input already makes.
        await listener._record_action_resolved_events([{"type": "move_token", "success": True}])

        # Mara's goal fired without any player message aimed at her.
        assert reg.get_npc("n1").goals[0].status == "done"

        # Both the player's action and Mara's self-initiated one were logged
        # as ACTION_RESOLVED events in the same event log.
        state = await listener._event_store.replay("s1")
        resolved = state.get("resolved_actions", [])
        assert any(r["action_type"] == "move_token" for r in resolved)
        assert any(r["action_type"] == "narrate" for r in resolved)

        await db.close()

    asyncio.run(run())


def test_two_npcs_matching_same_event_do_not_both_fire(tmp_path):
    """Phase 6 (SceneDirector) contention scenario: two NPCs have a goal
    matching the same event. Only the higher-priority one acts this tick;
    the other stays 'active', deferred to the next tick rather than both
    firing at once."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(
            description="low priority reaction",
            priority=1,
            trigger_conditions={"event_type": "action_resolved"},
        ))
        reg.register_npc("n2", "Kael", "A rogue")
        reg.add_goal("n2", Goal(
            description="seek revenge on the party",
            priority=10,
            trigger_conditions={"event_type": "action_resolved"},
        ))

        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "narrate", "text": "Kael lunges."}]
        })
        listener = _make_listener(db, reg, llm)

        await listener._record_action_resolved_events([{"type": "move_token", "success": True}])

        # Only Kael (higher priority) acted; Mara's goal is left untouched
        # (still pending, not prematurely activated) — reconsidered fresh
        # next time a matching event occurs, instead of carrying a stale
        # 'active' status into some later, unrelated turn.
        assert llm.generate.call_count == 1
        assert reg.get_npc("n2").goals[0].status == "done"
        assert reg.get_npc("n1").goals[0].status == "pending"

        await db.close()

    asyncio.run(run())


def test_npc_without_matching_goal_stays_silent(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal("n1", Goal(description="idle goal"))  # no trigger_conditions

        llm = MagicMock()
        llm.generate = AsyncMock()
        listener = _make_listener(db, reg, llm)

        await listener._record_action_resolved_events([{"type": "move_token", "success": True}])

        llm.generate.assert_not_called()
        assert reg.get_npc("n1").goals[0].status == "pending"

        await db.close()

    asyncio.run(run())
