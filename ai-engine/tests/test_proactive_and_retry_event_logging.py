"""Regression tests for event completeness fixes.

Issue #4 (Proactive/retry actions bypass event log): Proactive beats
(_run_proactive_action) and LLM retry paths (_notify_llm_of_failures) were
dispatching actions without recording them as ACTION_RESOLVED events.
This meant session replay missed ~20-30% of world-state changes.

Fixed by wiring both paths through _record_action_resolved_events.

Run:
    cd ai-engine && python -m pytest tests/test_proactive_and_retry_event_logging.py -v
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from events.store import EventStore
from events.types import ACTION_RESOLVED
from foundry.chat_listener import ChatListener
from persistence.db import Database


def _make_listener(db, llm):
    """Create a ChatListener with minimal mocks for testing event recording."""
    listener = ChatListener(
        foundry=MagicMock(),
        llm=llm,
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=db,
    )
    listener.foundry.get_scene_tokens = AsyncMock(return_value=[])
    listener.foundry.get_actors = AsyncMock(return_value=[])
    listener.foundry.execute_js = AsyncMock(return_value={"result": None})
    listener.state_tracker.get_snapshot = MagicMock(return_value="game state")
    listener.state_tracker.state.current_scene = "Test Scene"
    listener._campaign_loader = None
    listener._scene_awareness = None
    listener._npc_registry = None
    listener._pick_idle_beat_style = MagicMock(return_value="")
    listener.dispatcher.execute_batch = AsyncMock(
        side_effect=lambda actions: [{"type": a.get("type"), "success": True} for a in actions]
    )
    listener._on_results_callback = None
    listener._record_actions = AsyncMock()
    return listener


def test_proactive_actions_are_event_logged(tmp_path):
    """Proactive beats (idle/pacing/session_start) now record their dispatched
    actions as ACTION_RESOLVED events, so session replay sees them."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        session_id = "s1"
        await db.create_session(session_id, campaign="Test Campaign")

        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "narrate", "text": "The wind howls."}]
        })

        listener = _make_listener(db, llm)
        listener._event_store = EventStore(db)
        # Mock context building to avoid complex setup
        listener._get_npc_context = AsyncMock(return_value="")

        await listener._run_proactive_action(reason="idle")

        # Check that the narrate action was recorded as an ACTION_RESOLVED event
        events = await db.get_events_full("Test Campaign")
        assert any(e["type"] == ACTION_RESOLVED for e in events), \
            "proactive action was not recorded as ACTION_RESOLVED event"
        action_events = [e for e in events if e["type"] == ACTION_RESOLVED]
        assert action_events[0]["payload"]["action_type"] == "narrate"

        await db.close()

    asyncio.run(run())


def test_retry_actions_are_event_logged(tmp_path):
    """LLM retry actions (_notify_llm_of_failures) now record their dispatched
    actions as ACTION_RESOLVED events, so session replay sees retries too."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        session_id = "s1"
        await db.create_session(session_id, campaign="Test Campaign")

        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "narrate", "text": "Corrected action"}]
        })

        listener = _make_listener(db, llm)
        listener._event_store = EventStore(db)

        # Simulate a failed action that triggers retry-notify
        failed_actions = [{"type": "invalid_action", "error": "Unknown action type"}]
        retry_results = await listener._notify_llm_of_failures(failed_actions)

        # Check that the retry action was recorded as an ACTION_RESOLVED event
        events = await db.get_events_full("Test Campaign")
        action_events = [e for e in events if e["type"] == ACTION_RESOLVED]
        assert len(action_events) > 0, "retry action was not recorded as ACTION_RESOLVED event"
        assert action_events[0]["payload"]["action_type"] == "narrate"

        await db.close()

    asyncio.run(run())


def test_proactive_and_retry_pass_trigger_npcs_false(tmp_path):
    """Verify that proactive and retry paths pass trigger_npcs=False to prevent
    infinite loops when NPC actions would re-trigger other NPCs."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        session_id = "s1"
        await db.create_session(session_id, campaign="Test Campaign")

        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "narrate", "text": "The scene opens."}]
        })

        listener = _make_listener(db, llm)
        listener._event_store = EventStore(db)
        listener._get_npc_context = AsyncMock(return_value="")

        # The recording call is what carries the flag, so watch that rather
        # than _maybe_trigger_npc_agents, which trigger_npcs=False is meant
        # to stop reaching at all.
        recorded = []
        real_record = listener._record_action_resolved_events

        async def watch_record(results, **kwargs):
            recorded.append(kwargs)
            return await real_record(results, **kwargs)

        listener._record_action_resolved_events = watch_record
        triggered = []

        async def watch_trigger(*a, **k):
            triggered.append(a)

        listener._maybe_trigger_npc_agents = watch_trigger

        await listener._run_proactive_action(reason="idle")

        assert recorded, "the proactive path recorded no resolved events"
        assert all(k.get("trigger_npcs") is False for k in recorded), (
            f"the GM's own proactive actions would re-trigger NPCs: {recorded}"
        )
        assert triggered == [], "an NPC agent was triggered by the GM's own action"

        await db.close()

    asyncio.run(run())


def test_proactive_and_retry_events_isolate_from_npc_triggers(tmp_path):
    """Proactive and retry actions pass trigger_npcs=False to avoid re-triggering
    NPC agents with their own actions (would cause infinite loops)."""
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        session_id = "s1"
        await db.create_session(session_id, campaign="Test Campaign")

        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "narrate", "text": "The scene opens."}]
        })

        listener = _make_listener(db, llm)
        listener._event_store = EventStore(db)
        listener._scene_director = MagicMock()
        listener._npc_registry = None  # No NPCs to trigger

        triggered = []

        async def watch_trigger(*a, **k):
            triggered.append(a)

        listener._maybe_trigger_npc_agents = watch_trigger

        # The retry path feeds the model its own failures. If that recording
        # triggered NPCs, each NPC action would record, retry and trigger
        # again.
        await listener._notify_llm_of_failures(
            [{"type": "narrate", "success": False, "error": "relay timeout"}]
        )
        await listener._run_proactive_action(reason="idle")

        assert triggered == [], (
            f"the retry and proactive paths triggered NPC agents: {triggered}"
        )

        await db.close()

    asyncio.run(run())


def test_an_npcs_own_action_does_not_wake_the_npcs_again(tmp_path):
    """The third site carrying this flag, and the one with the worst failure
    mode: recording an NPC's own action with trigger_npcs=True re-enters
    _maybe_trigger_npc_agents, which dispatches, which records again. Flipping
    it does not fail the suite, it hangs it, so nothing would report the
    regression.

    Bounded here, so a regression fails in a second instead of stalling CI.
    """
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        listener = _make_listener(db, MagicMock())
        listener._event_store = EventStore(db)

        goal = MagicMock()
        goal.status = "pending"
        goal.matches = MagicMock(return_value=True)
        npc = MagicMock(npc_name="Halda", goals=[goal])
        listener._npc_registry = MagicMock(list_npcs=MagicMock(return_value=[npc]))
        listener._scene_director = MagicMock(
            next_turn=MagicMock(return_value=MagicMock(npc=npc, matched_goals=[goal]))
        )

        entries = 0
        real = listener._maybe_trigger_npc_agents

        async def counted(*args, **kwargs):
            nonlocal entries
            entries += 1
            if entries > 3:
                raise AssertionError("_maybe_trigger_npc_agents recursed into itself")
            return await real(*args, **kwargs)

        listener._maybe_trigger_npc_agents = counted

        with patch("foundry.chat_listener.NPCAgent") as agent_cls:
            agent_cls.return_value.act = AsyncMock(
                return_value=[MagicMock(action={"type": "narrate", "text": "Halda spits."})]
            )
            await counted("s1", "Test Campaign", {"type": ACTION_RESOLVED, "payload": {}})

        assert entries == 1, f"the NPC's own action woke the NPCs again ({entries} entries)"

        await db.close()

    asyncio.run(run())
