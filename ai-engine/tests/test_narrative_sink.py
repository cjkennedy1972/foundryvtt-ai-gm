"""Tests for the named outbound narrative boundary."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.narrative import FoundryNarrativeSink
from events.store import EventStore
from npc.registry import NPCRegistry
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
            {"name": "World log", "pages": [{"name": "World log", "text": {"content": "The door opened."}}]},
        )
        foundry.add_effect.assert_awaited_once_with("Actor.1", "poisoned")
        foundry.remove_effect.assert_awaited_once_with("Actor.1", "poisoned")

    asyncio.run(run())


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
