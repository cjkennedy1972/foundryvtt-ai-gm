"""End-to-end test: a cast_spell action the LLM proposes with no available
slot is rejected by RefereeAgent inside ChatListener._process_player_input
— the real gap the Referee scope-limit plan called out (checked live
against Foundry's actor sheet, not a duplicate ledger) — and, like a
genuine dispatch failure, gets one same-turn retry-notify chance for the
LLM to self-correct (see _notify_llm_of_failures)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foundry.chat_listener import ChatListener
from persistence.db import Database


def _make_listener(db, foundry, llm):
    listener = ChatListener(
        foundry=foundry,
        llm=llm,
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=db,
    )
    listener.dispatcher.execute_batch = AsyncMock(
        side_effect=lambda actions: [{"type": a.get("type"), "success": True} for a in actions]
    )
    return listener


def test_cast_spell_with_no_slot_is_rejected_and_gets_one_retry_chance(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        foundry = MagicMock()
        foundry.get_spell_slots = AsyncMock(return_value={"1": {"value": 0, "max": 4}})

        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=[
            {"actions": [{"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Magic Missile", "spell_level": 1}]},
            # Retry: the LLM self-corrects to a legal action instead of
            # repeating the rejected cast.
            {"actions": [{"type": "narrate", "text": "The spell fizzles — no magic left to give."}]},
        ])

        listener = _make_listener(db, foundry, llm)
        actions, results = await listener._process_player_input(
            "I cast Magic Missile", "Alice", "game state", ""
        )

        # Original cast_spell never reached the dispatcher unchanged...
        assert llm.generate.call_count == 2, "referee rejection should trigger the same retry-notify path as a dispatch failure"
        dispatched_types = [c.args[0][0]["type"] for c in listener.dispatcher.execute_batch.call_args_list]
        assert "cast_spell" not in dispatched_types
        # ...but the corrected retry action DID get dispatched.
        assert "narrate" in dispatched_types
        assert any(r.get("success") is False and "level 1" in (r.get("error") or "") for r in results)

        await db.close()

    asyncio.run(run())


def test_cast_spell_with_available_slot_reaches_dispatcher(tmp_path):
    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        await db.create_session("s1", campaign="Test Campaign")

        foundry = MagicMock()
        foundry.get_spell_slots = AsyncMock(return_value={"1": {"value": 2, "max": 4}})

        llm = MagicMock()
        llm.generate = AsyncMock(return_value={
            "actions": [{"type": "cast_spell", "actor_uuid": "a1", "spell_name": "Magic Missile", "spell_level": 1}]
        })

        listener = _make_listener(db, foundry, llm)
        actions, results = await listener._process_player_input(
            "I cast Magic Missile", "Alice", "game state", ""
        )

        listener.dispatcher.execute_batch.assert_called_once()
        assert results[0]["success"] is True

        await db.close()

    asyncio.run(run())
