"""Event reducers, projection error handling, and the transcript formatter."""

import logging
from unittest.mock import AsyncMock

import pytest

from events.replay import SessionReplay
from events.store import EventStore
from events.types import (
    ACTION_RESOLVED, FACT_CANONIZED, FACTION_CREATED, FACTION_DELETED, FACTION_UPDATED, LEGACY_NOTE,
    NPC_CONVERSED, NPC_MOVED, PLAYER_DOWNTIME_NARRATED, PLAYER_DOWNTIME_RESOLVED, RELATIONSHIP_CHANGED,
    REDUCERS, SOLO_DEATH_SETBACK, TIME_ADVANCED, describe_action_resolved,
)


def _store():
    return EventStore(db=AsyncMock())


def _project(events):
    store, state = _store(), {}
    for t, p in events:
        state = store.project(state, {"id": 1, "type": t, "payload": p})
    return state


def test_every_event_type_has_a_reducer():
    for t in (NPC_MOVED, RELATIONSHIP_CHANGED, FACT_CANONIZED, TIME_ADVANCED, FACTION_CREATED, FACTION_UPDATED,
              FACTION_DELETED, ACTION_RESOLVED, SOLO_DEATH_SETBACK, PLAYER_DOWNTIME_RESOLVED,
              PLAYER_DOWNTIME_NARRATED, NPC_CONVERSED, LEGACY_NOTE):
        assert t in REDUCERS


def test_reducers_never_mutate_the_input_state():
    store = _store()
    before = {"factions": {"f": {"a": 1}}, "resolved_actions": [{"x": 1}], "solo_death_setbacks": [1],
              "downtime_outcomes": [1], "downtime_narrated_event_ids": [1], "npcs": {}, "canon_facts": ["a"],
              "relationships": {}, "world_time_elapsed_seconds": 5}
    import copy
    snapshot = copy.deepcopy(before)
    for t, p in [(FACTION_CREATED, {"faction_id": "g", "data_json": {}}),
                 (FACTION_UPDATED, {"faction_id": "f", "data_json": {"b": 2}}),
                 (FACTION_DELETED, {"faction_id": "f"}), (ACTION_RESOLVED, {"y": 2}),
                 (SOLO_DEATH_SETBACK, {"z": 1}), (PLAYER_DOWNTIME_RESOLVED, {"w": 1}),
                 (PLAYER_DOWNTIME_NARRATED, {"event_ids": [2]}), (NPC_MOVED, {"npc_id": "n", "location": "l"}),
                 (FACT_CANONIZED, {"fact": "b"}), (RELATIONSHIP_CHANGED, {"source_id": "a", "target_id": "b"}),
                 (TIME_ADVANCED, {"duration_seconds": 1})]:
        store.project(before, {"type": t, "payload": p})
    assert before == snapshot


def test_faction_lifecycle_merges_updates_and_ignores_unknown_ids():
    s = _project([
        (FACTION_CREATED, {"faction_id": "f1", "data_json": {"name": "Guild", "power": 1}}),
        (FACTION_UPDATED, {"faction_id": "f1", "data_json": {"power": 5}}),
        (FACTION_UPDATED, {"faction_id": "ghost", "data_json": {"power": 9}}),
    ])
    assert s["factions"] == {"f1": {"name": "Guild", "power": 5}}
    s = _project([(FACTION_CREATED, {"faction_id": "f1", "data_json": {}}), (FACTION_DELETED, {"faction_id": "f1"}),
                  (FACTION_DELETED, {"faction_id": "never-existed"})])
    assert s["factions"] == {}


def test_append_only_logs_accumulate_in_order():
    s = _project([
        (ACTION_RESOLVED, {"n": 1}), (ACTION_RESOLVED, {"n": 2}),
        (SOLO_DEATH_SETBACK, {"actor_name": "Thalia"}),
        (PLAYER_DOWNTIME_RESOLVED, {"id": "d1"}),
        (PLAYER_DOWNTIME_NARRATED, {"event_ids": [4, 5]}), (PLAYER_DOWNTIME_NARRATED, {}),
        (PLAYER_DOWNTIME_NARRATED, {"event_ids": [6]}),
        (TIME_ADVANCED, {"duration_seconds": 60}), (TIME_ADVANCED, {"duration_seconds": 30}),
        (NPC_CONVERSED, {"npc_id": "x"}), (LEGACY_NOTE, {}),
        ("some_future_type", {"a": 1}),
    ])
    assert s["resolved_actions"] == [{"n": 1}, {"n": 2}]
    assert s["solo_death_setbacks"] == [{"actor_name": "Thalia"}]
    assert s["downtime_outcomes"] == [{"id": "d1"}]
    assert s["downtime_narrated_event_ids"] == [4, 5, 6]
    assert s["world_time_elapsed_seconds"] == 90
    assert "npcs" not in s  # noop events add nothing


def test_a_malformed_event_is_logged_and_skipped_without_losing_state(caplog):
    store = _store()
    state = {"canon_facts": ["kept"]}
    with caplog.at_level(logging.ERROR):
        out = store.project(state, {"id": 7, "type": FACT_CANONIZED, "payload": {}})  # missing "fact"
    assert out is state
    assert "Failed to project event 7" in caplog.text
    assert store.project(state, {"type": None}) == state  # unknown/None type is a no-op


@pytest.mark.asyncio
async def test_store_delegates_to_db_and_replay_projects_oldest_first():
    db = AsyncMock()
    db.record_typed_event.return_value = 42
    store = EventStore(db)
    assert await store.append("s", "c", NPC_MOVED, {"a": 1}, "desc") == 42
    db.record_typed_event.assert_awaited_once_with("s", "c", NPC_MOVED, {"a": 1}, "desc")
    db.get_events_full.return_value = [
        {"id": 1, "type": NPC_MOVED, "payload": {"npc_id": "n", "location": "inn"}},
        {"id": 2, "type": NPC_MOVED, "payload": {"npc_id": "n", "location": "keep"}},
        {"id": 3, "type": FACT_CANONIZED, "payload": None},  # broken row must not abort replay
    ]
    state = await store.replay("c")
    assert state == {"npcs": {"n": {"location": "keep"}}}
    await store.get_events("c", session_id="s", limit=3)
    db.get_events_full.assert_awaited_with("c", session_id="s", limit=3)


def test_describe_action_resolved_variants():
    assert describe_action_resolved({"action_type": "roll", "success": False, "error": "boom"}) == "roll failed: boom"
    assert describe_action_resolved({"success": False}) == "action failed: unknown error"
    assert describe_action_resolved({"action_type": "narrate", "params": '{"npc_name": "Mara", "text": "hi"}'}) \
        == "narrate: Mara: hi"
    assert describe_action_resolved({"action_type": "narrate", "params": '{"text": "hi"}'}) == "narrate: hi"
    assert describe_action_resolved({"action_type": "narrate", "params": '{"speaker": "Mara"}'}) == "narrate: Mara"
    assert describe_action_resolved({"action_type": "narrate", "params": '{"text": "cut off'}) == "narrate"
    assert describe_action_resolved({}) == "action"
    assert len(describe_action_resolved({"action_type": "x", "params": '{"text": "' + "a" * 500 + '"}'})) == 200


# ── replay ──────────────────────────────────────────────────────────────────

def _replay_with(events):
    store = _store()
    store.db.get_events_full.return_value = events
    return SessionReplay(store)


@pytest.mark.asyncio
async def test_state_at_time_bounds_and_inclusive_index():
    events = [{"type": NPC_MOVED, "payload": {"npc_id": "n", "location": "a"}},
              {"type": NPC_MOVED, "payload": {"npc_id": "n", "location": "b"}}]
    r = _replay_with(events)
    assert (await r.get_state_at_time("c", 0))["npcs"]["n"]["location"] == "a"
    assert (await r.get_state_at_time("c", 1))["npcs"]["n"]["location"] == "b"
    assert await r.get_state_at_time("c", 2) == {}
    assert await r.get_state_at_time("c", -1) == {}
    await r.get_state_at_time("c", 0, session_id="s9")
    r.event_store.db.get_events_full.assert_awaited_with("c", session_id="s9", limit=None)


@pytest.mark.asyncio
async def test_find_by_type_and_npc_filters():
    events = [
        {"type": NPC_MOVED, "payload": {"npc_id": "mara", "location": "inn"}},
        {"type": RELATIONSHIP_CHANGED, "payload": {"source_id": "x", "target_id": "mara", "relationship_type": "ally",
                                                   "strength": 3}},
        {"type": RELATIONSHIP_CHANGED, "payload": {"source_id": "mara", "target_id": "y"}},
        {"type": FACT_CANONIZED, "payload": {"fact": "Mara is a spy"}},
    ]
    r = _replay_with(events)
    assert [e["event"] for e in await r.find_events_by_type("c", RELATIONSHIP_CHANGED)] == ["relationship"] * 2
    assert await r.find_events_by_type("c", "nothing") == []
    npc = await r.find_events_by_npc("c", "mara")
    assert [e["event"] for e in npc] == ["npc_moved", "relationship", "relationship"]  # canon text is not a match
    assert npc[1] == {"event": "relationship", "source": "x", "target": "mara", "type": "ally", "strength": 3}


@pytest.mark.asyncio
async def test_humanized_shapes_for_each_event_type():
    events = [
        {"type": ACTION_RESOLVED, "payload": {"action_type": "roll", "success": False, "error": "e"}},
        {"type": ACTION_RESOLVED, "payload": {"action_type": "narrate"}},
        {"type": FACT_CANONIZED, "payload": {"fact": "f"}},
        {"type": TIME_ADVANCED, "payload": {"duration_seconds": 7200}},
        {"type": "mystery", "payload": {"k": 1}},
        {"type": None, "payload": {}},
    ]
    out = await _replay_with(events).get_session_transcript("c", limit=6)
    assert out[0] == {"event": "action", "type": "roll", "success": False, "error": "e"}
    assert out[1]["success"] is True  # success defaults to True when unreported
    assert out[2] == {"event": "canon", "fact": "f"}
    assert out[3] == {"event": "time_passed", "seconds": 7200}
    assert out[4] == {"event": "mystery", "type": "mystery", "payload": {"k": 1}}
    assert out[5]["event"] == "unknown"


def test_transcript_formatting_lines():
    r = _replay_with([])
    assert r.format_transcript_for_chat([]) == "No events found."
    text = r.format_transcript_for_chat([
        {"event": "action", "type": "roll", "success": True},
        {"event": "action", "type": "move", "success": False, "error": "blocked"},
        {"event": "npc_moved", "npc": "mara", "location": "inn"},
        {"event": "relationship", "source": "a", "target": "b", "type": "ally", "strength": 2},
        {"event": "canon", "fact": "The sun rose."},
        {"event": "time_passed", "seconds": 7300},
        {"event": "mystery"},
    ]).split("\n")
    assert text == [
        "**Session Transcript**:",
        "1. ✅ roll",
        "2. ❌ move — blocked",
        "3. 🚶 mara moved to inn",
        "4. 🤝 a → b: ally (2)",
        "5. 📜 The sun rose.",
        "6. ⏰ 2h passed",
        "7. mystery",
    ]


@pytest.mark.asyncio
async def test_a_time_event_without_a_duration_still_formats():
    """TIME_ADVANCED with no duration_seconds humanizes to seconds=None; the formatter must not crash."""
    r = _replay_with([{"type": TIME_ADVANCED, "payload": {}}])
    transcript = await r.get_session_transcript("c")
    assert "0h passed" in r.format_transcript_for_chat(transcript)
