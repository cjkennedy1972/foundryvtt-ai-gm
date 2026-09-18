"""Characterisation of /gm command routing before _handle_gm_command is split.

The dispatcher was a 350-line if/elif chain of 17 arms. Four had tests
(rule, canonize, canon review/approve/reject, end session); the rest did not,
which is what made extracting them risky. These pin the arms that had no
coverage, driving the same public entry point the existing tests use, so the
extraction is observable only as a refactor.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from foundry.chat_listener import ChatListener


def _make_listener(**overrides):
    kwargs = dict(
        foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    )
    kwargs.update(overrides)
    listener = ChatListener(**kwargs)
    listener.foundry.chat_message = AsyncMock()
    listener.foundry.roll = AsyncMock()
    listener.narrative_sink = SimpleNamespace(narration=AsyncMock())
    return listener


def _said(listener):
    return " ".join(str(c.args[0]) for c in listener.narrative_sink.narration.call_args_list)


def test_help_lists_the_commands_it_dispatches():
    listener = _make_listener()

    asyncio.run(listener._handle_gm_command("GM", "/gm help"))

    text = _said(listener)
    for command in ("start session", "narrate", "roll", "end session", "canon review"):
        assert command in text, f"/gm help omits {command!r}"


def test_narrate_passes_the_text_through_as_gm():
    listener = _make_listener()

    asyncio.run(listener._handle_gm_command("GM", "/gm narrate The door creaks open."))

    listener.narrative_sink.narration.assert_awaited_once()
    assert listener.narrative_sink.narration.call_args.args[0] == "The door creaks open."
    assert listener.narrative_sink.narration.call_args.kwargs["speaker"] == "GM"


def test_roll_sends_the_formula_to_foundry_as_gm():
    listener = _make_listener()

    asyncio.run(listener._handle_gm_command("GM", "/gm roll 2d6+3"))

    listener.foundry.roll.assert_awaited_once_with("2d6+3", speaker="GM")


def test_session_replay_without_an_active_session_says_so():
    listener = _make_listener()
    listener.db.get_active_session_info = AsyncMock(return_value=None)

    asyncio.run(listener._handle_gm_command("GM", "/gm session replay"))

    assert "no active session" in _said(listener).lower()


def test_session_events_without_an_active_session_says_so():
    listener = _make_listener()
    listener.db.get_active_session_info = AsyncMock(return_value=None)

    asyncio.run(listener._handle_gm_command("GM", "/gm session events combat"))

    assert "no active session" in _said(listener).lower()


def test_settlement_list_without_a_world_clock_says_so():
    listener = _make_listener()
    listener._world_clock = None

    asyncio.run(listener._handle_gm_command("GM", "/gm settlement list"))

    assert "not initialized" in _said(listener).lower()


def test_settlement_list_reports_name_region_and_counts():
    listener = _make_listener()
    listener._world_clock = MagicMock()
    listener._world_clock.list_settlements = MagicMock(return_value=[
        SimpleNamespace(name="Bramblewick", region="The Fens", population=400,
                        npcs=["a", "b"], buildings=["inn"]),
    ])

    asyncio.run(listener._handle_gm_command("GM", "/gm settlement list"))

    text = _said(listener)
    assert "Bramblewick" in text
    assert "The Fens" in text
    assert "400 pop" in text
    assert "2 NPCs" in text
    assert "1 buildings" in text


def test_settlement_list_reports_an_empty_campaign():
    listener = _make_listener()
    listener._world_clock = MagicMock()
    listener._world_clock.list_settlements = MagicMock(return_value=[])

    asyncio.run(listener._handle_gm_command("GM", "/gm settlement list"))

    assert "no settlements registered" in _said(listener).lower()


def test_settlement_query_without_a_world_clock_says_so():
    listener = _make_listener()
    listener._world_clock = None

    asyncio.run(listener._handle_gm_command("GM", "/gm settlement query Bramblewick"))

    assert "not initialized" in _said(listener).lower()


def test_an_unmatched_command_is_answered_by_the_llm():
    """The final else treats anything unrecognised as a question to the GM AI."""
    listener = _make_listener()
    listener.llm.generate_text = AsyncMock(return_value="A thoughtful answer.")

    asyncio.run(listener._handle_gm_command("GM", "/gm what is the party carrying?"))

    listener.llm.generate_text.assert_awaited_once_with("what is the party carrying?")
    assert "A thoughtful answer." in _said(listener)


def test_ask_prefix_routes_the_same_as_gm():
    """Both prefixes are stripped to the same command string."""
    listener = _make_listener()

    asyncio.run(listener._handle_gm_command("GM", "/askroll 1d20"))

    listener.foundry.roll.assert_awaited_once_with("1d20", speaker="GM")
