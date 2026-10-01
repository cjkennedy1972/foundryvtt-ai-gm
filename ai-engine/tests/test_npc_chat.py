"""Players talking to an NPC directly: /npc parsing, the NPC's reply context, the listener command."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from foundry.chat_listener import ChatListener
from llm.usage import TokenBudgetExceeded
from npc.chat import NPCChat, parse_npc_chat
from npc.registry import NPCRegistry


def _registry():
    reg = NPCRegistry()
    reg.register_npc("n1", "Warden Vael", "An undead sentinel who speaks in riddles.")
    reg.register_npc("n2", "Mira", "The tavern keeper of the Drowned Rat.")
    return reg


def test_parse_colon_form_uses_fuzzy_name_and_keeps_the_message():
    npc, msg = parse_npc_chat(_registry(), " warden vael: who rings the bell? ")
    assert (npc.npc_name, msg) == ("Warden Vael", "who rings the bell?")


def test_parse_without_colon_prefers_the_longest_name_and_handles_spaces():
    npc, msg = parse_npc_chat(_registry(), "Warden Vael let us pass")
    assert (npc.npc_name, msg) == ("Warden Vael", "let us pass")
    npc, msg = parse_npc_chat(_registry(), "Mira any rooms free?")
    assert (npc.npc_name, msg) == ("Mira", "any rooms free?")


@pytest.mark.parametrize("text", ["", "Nobody: hello", "Mira:", "hello there", "Warden"])
def test_parse_returns_nothing_for_unknown_names_or_empty_messages(text):
    assert parse_npc_chat(_registry(), text) == (None, "")


def _chat(reply="  \"The bell tolls for you.\"  ", events=None, lore="## VAULT LORE\n- The bell hangs in the drowned chapel."):
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value=reply)
    router = MagicMock()
    router.get = MagicMock(return_value=llm)
    memory = MagicMock()
    memory.recall = AsyncMock(return_value=events or [])
    reg = _registry()
    return NPCChat(reg, memory, router, AsyncMock(return_value=lore)), reg, llm


def test_reply_is_clean_dialogue_built_from_record_memory_lore_and_history():
    chat, reg, llm = _chat(events=[{"description": "Thorin paid for a room"}])
    vael = reg.get_npc("n1")

    first = asyncio.run(chat.reply("Saltmarsh", vael, "Thorin", "Who rings the bell?"))
    assert first == "The bell tolls for you."                      # quotes and padding stripped
    kwargs = llm.generate_text.call_args.kwargs
    assert kwargs["user_message"] == "Thorin says to you: Who rings the bell?"
    assert "Warden Vael" in kwargs["system_prompt"] and "no JSON" in kwargs["system_prompt"]
    for expected in ("undead sentinel", "Thorin paid for a room", "drowned chapel"):
        assert expected in kwargs["context"]

    asyncio.run(chat.reply("Saltmarsh", vael, "Elara", "And the second bell?"))
    assert "Thorin: Who rings the bell?\nWarden Vael: The bell tolls for you." in llm.generate_text.call_args.kwargs["context"]


def test_a_memory_failure_does_not_stop_the_npc_answering():
    chat, reg, llm = _chat()
    chat.memory.recall = AsyncMock(side_effect=RuntimeError("db"))
    assert asyncio.run(chat.reply("c", reg.get_npc("n2"), "Thorin", "Rooms?")) == "The bell tolls for you."


def _listener(**overrides):
    reg = _registry()
    kwargs = dict(foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(), state_tracker=MagicMock(),
                  db=MagicMock(), npc_registry=reg)
    kwargs.update(overrides)
    listener = ChatListener(**kwargs)
    listener.db.get_active_session_info = AsyncMock(return_value={"campaign": "Saltmarsh"})
    listener.narrative_sink = SimpleNamespace(narration=AsyncMock())
    listener._dispatch_narration_now = AsyncMock(return_value=([], True))
    return listener


def test_npc_command_speaks_the_reply_as_that_npc():
    listener = _listener()
    listener._npc_chat.reply = AsyncMock(return_value="Mind the water, traveller.")

    asyncio.run(listener._handle_npc_chat("Thorin", "/npc Mira: any news?"))

    listener._npc_chat.reply.assert_awaited_once()
    assert listener._npc_chat.reply.call_args.args[0] == "Saltmarsh"
    assert listener._npc_chat.reply.call_args.args[3] == "any news?"
    listener._dispatch_narration_now.assert_awaited_once_with(
        {"type": "speak", "npc_name": "Mira", "text": "Mind the water, traveller."})


def test_unknown_npc_gets_usage_with_the_names_present():
    listener = _listener()
    listener._npc_chat.reply = AsyncMock()

    asyncio.run(listener._handle_npc_chat("Thorin", "/npc Nobody: hello"))

    said = listener.narrative_sink.narration.call_args.args[0]
    assert "/npc <name>" in said and "Mira" in said and "Warden Vael" in said
    listener._npc_chat.reply.assert_not_awaited()
    listener._dispatch_narration_now.assert_not_awaited()


def test_llm_failure_is_in_fiction_and_budget_exhaustion_is_silent():
    listener = _listener()
    listener._npc_chat.reply = AsyncMock(side_effect=RuntimeError("down"))
    asyncio.run(listener._handle_npc_chat("Thorin", "/npc Mira: hi"))
    assert listener.narrative_sink.narration.call_args.args[0] == "*Mira says nothing.*"

    listener = _listener()
    listener._npc_chat.reply = AsyncMock(side_effect=TokenBudgetExceeded("session", 1, 1, 1))
    asyncio.run(listener._handle_npc_chat("Thorin", "/npc Mira: hi"))
    listener.narrative_sink.narration.assert_not_awaited()
    listener._dispatch_narration_now.assert_not_awaited()


def test_without_an_npc_registry_the_command_explains_instead_of_crashing():
    listener = _listener(npc_registry=None)
    asyncio.run(listener._handle_npc_chat("Thorin", "/npc Mira: hi"))
    assert "No one here has been introduced" in listener.narrative_sink.narration.call_args.args[0]


def _routed(content, *, session="s1", player=True, running=True):
    listener = _listener()
    listener._handle_npc_chat = AsyncMock()
    listener._run_turn = AsyncMock()
    listener.db.get_active_session = AsyncMock(return_value=session)
    listener._is_player_message = AsyncMock(return_value=player)
    listener._running = running
    asyncio.run(listener.handle_message({"content": content, "speaker": "Thorin"}))
    return listener


def test_a_players_npc_message_goes_to_npc_chat_not_a_gm_turn():
    listener = _routed("/npc Mira: any news?")
    listener._handle_npc_chat.assert_awaited_once()
    listener._run_turn.assert_not_awaited()


@pytest.mark.parametrize("kwargs", [{"session": None}, {"player": False}, {"running": False}])
def test_npc_chat_respects_session_player_and_pause_gates(kwargs):
    listener = _routed("/npc Mira: any news?", **kwargs)
    listener._handle_npc_chat.assert_not_awaited()


def test_a_conversation_is_remembered_by_that_npc_in_a_later_session():
    """Round trip through the real event store: /npc writes the exchange, NPCMemory recalls it for that NPC only."""
    from events.store import EventStore
    from npc.memory import NPCMemory
    from persistence.db import Database

    async def run():
        db = Database(":memory:")
        await db.init()
        store = EventStore(db)
        listener = _listener(db=db, event_store=store)
        listener._npc_chat.reply = AsyncMock(return_value="Mind the water, traveller.")
        await db.create_session("s1", "Saltmarsh")
        listener.db.get_active_session_info = AsyncMock(return_value={"session_id": "s1", "campaign": "Saltmarsh"})

        await listener._handle_npc_chat("Thorin", "/npc Mira: any news?")

        memory = NPCMemory(store)
        mira = await memory.recall("Saltmarsh", "n2")
        assert len(mira) == 1
        assert mira[0]["description"] == 'Thorin said "any news?" and you answered "Mind the water, traveller."'
        assert mira[0]["payload"]["speaker"] == "Thorin"
        assert await memory.recall("Saltmarsh", "n1") == []          # Warden Vael heard nothing
        assert (await store.replay("Saltmarsh")) == {}               # memory only: projected state untouched
        await db.close()

    asyncio.run(run())


def test_a_failed_memory_write_does_not_lose_the_reply():
    listener = _listener()
    listener._npc_chat.reply = AsyncMock(return_value="Hello.")
    listener._event_store = MagicMock()
    listener._event_store.append = AsyncMock(side_effect=RuntimeError("db locked"))
    listener.db.get_active_session_info = AsyncMock(return_value={"session_id": "s1", "campaign": "c"})

    asyncio.run(listener._handle_npc_chat("Thorin", "/npc Mira: hi"))

    listener._dispatch_narration_now.assert_awaited_once()
