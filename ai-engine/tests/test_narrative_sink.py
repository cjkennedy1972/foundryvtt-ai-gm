"""Tests for the named outbound narrative boundary."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.narrative import FoundryNarrativeSink
from foundry.chat_listener import GameLoop
from events.store import EventStore
from npc.registry import NPCRegistry
from npc.goals import Goal
from worldclock.agent import WorldClockAgent
from persistence.db import Database


def test_foundry_narrative_sink_maps_artifacts_to_foundry_client():
    async def run():
        foundry = MagicMock()
        foundry.chat_message = AsyncMock(return_value={"ok": True})
        foundry.create_entity = AsyncMock(return_value={"uuid": "JournalEntry.1"})
        foundry.add_effect = AsyncMock(return_value={"ok": True})
        foundry.remove_effect = AsyncMock(return_value={"ok": True})
        sink = FoundryNarrativeSink(foundry)

        await sink.narration("A door opens.")
        await sink.journal_entry("World log", "The door opened.")
        await sink.chat_card("<strong>Initiative</strong>")
        await sink.effect("Actor.1", "poisoned")
        await sink.effect("Actor.1", "poisoned", active=False)

        foundry.chat_message.assert_any_await("A door opens.", speaker="GM", whisper=None)
        foundry.chat_message.assert_any_await("<strong>Initiative</strong>", speaker="GM")
        foundry.create_entity.assert_awaited_once_with(
            "JournalEntry",
            {
                "name": "World log",
                "pages": [{
                    "name": "World log",
                    "type": "text",
                    "text": {"content": "The door opened.", "format": 1},
                }],
            },
        )
        foundry.add_effect.assert_awaited_once_with("Actor.1", "poisoned")
        foundry.remove_effect.assert_awaited_once_with("Actor.1", "poisoned")

    asyncio.run(run())


def test_game_loop_preserves_falsy_injected_narrative_sink():
    class FalsySink:
        def __bool__(self):
            return False

    sink = FalsySink()
    registry = NPCRegistry()
    event_store = MagicMock()
    loop = GameLoop(
        foundry=MagicMock(),
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
        event_store=event_store,
        npc_registry=registry,
        narrative_sink=sink,
    )

    assert loop.narrative_sink is sink
    assert loop._world_clock.narrative_sink is sink


def test_world_clock_delivers_time_advance_to_sink():
    async def run():
        db = Database(":memory:")
        await db.init()
        sink = MagicMock()
        sink.narration = AsyncMock()
        clock = WorldClockAgent(EventStore(db), NPCRegistry(), narrative_sink=sink)

        await clock.advance("s1", 604800)

        sink.narration.assert_awaited_once()
        assert "604800 seconds" in sink.narration.await_args.args[0]
        await db.close()

    asyncio.run(run())


def test_world_clock_state_survives_narrative_delivery_failure():
    async def run():
        db = Database(":memory:")
        await db.init()
        sink = MagicMock()
        sink.narration = AsyncMock(side_effect=ConnectionError("Foundry unavailable"))
        reg = NPCRegistry()
        reg.register_npc("n1", "Mara", "A knight")
        reg.add_goal(reg.get_npc("n1").npc_id, Goal(
            description="seek revenge", trigger_conditions={"event_type": "time_advanced"}
        ))
        clock = WorldClockAgent(EventStore(db), reg, narrative_sink=sink)

        activated = await clock.advance("s1", 3600)

        assert activated == ["n1:seek revenge"]
        assert reg.get_npc("n1").goals[0].status == "active"
        await db.close()

    asyncio.run(run())
