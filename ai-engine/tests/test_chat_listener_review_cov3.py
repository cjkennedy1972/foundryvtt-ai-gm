"""Behavioural coverage for foundry/chat_listener.py, part 3: proactive
beats, session start, downtime commands, scene sync and small edge cases."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import foundry.chat_listener as cl
from config import settings
from foundry.chat_listener import ChatListener
from immersion.cinema import CinemaDirector


def make(**overrides):
    kwargs = dict(
        foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    )
    kwargs.update(overrides)
    ll = ChatListener(**kwargs)
    ll.foundry._ai_name = "AI GM"
    ll.narrative_sink = SimpleNamespace(narration=AsyncMock())
    ll.db.get_active_session = AsyncMock(return_value="sess-1")
    ll.db.get_active_session_info = AsyncMock(return_value={"session_id": "sess-1", "campaign": "Camp"})
    ll.state_tracker.state.mode = "explore"
    ll.state_tracker.get_snapshot = MagicMock(return_value="SNAP")
    return ll


def said(ll):
    return [c.args[0] for c in ll.narrative_sink.narration.call_args_list]


run = asyncio.run


def test_cinema_of_returns_only_a_real_director():
    director = MagicMock(spec=CinemaDirector)
    assert cl._cinema_of(SimpleNamespace(cinema=director)) is director
    assert cl._cinema_of(SimpleNamespace(cinema="nope")) is None
    assert cl._cinema_of(SimpleNamespace()) is None


def test_flush_with_nothing_pending_runs_no_turn():
    ll = make()
    ll._run_turn = AsyncMock()
    run(ll._flush_input_batch(0))
    ll._run_turn.assert_not_awaited()


def test_stream_drops_an_identical_repeated_action_within_one_turn():
    ll = make()
    ll.llm._extract_json = None
    twice = '{"actions":[{"type":"narrate","text":"Boom"},{"type":"narrate","text":"Boom"}]}'

    async def gen(**kw):
        for ch in twice:
            yield ch

    ll.llm.generate_stream = gen
    ll._referee = MagicMock()
    ll._referee.adjudicate_batch = AsyncMock(side_effect=lambda a: [SimpleNamespace(action=x, approved=True, reason="") for x in a])
    ll.dispatcher.execute_batch = AsyncMock(return_value=[{"type": "narrate", "success": True}])
    ll._notify_llm_of_failures = AsyncMock(return_value=[])
    ll._place_referenced_combatants = AsyncMock()
    ll._handle_generated_npcs = AsyncMock()
    ll._update_immersion_state = AsyncMock()
    ll._record_action_resolved_events = AsyncMock()
    actions, _ = run(ll._process_player_input("go", "A", "S", "C"))
    assert len(actions) == 1 and ll.dispatcher.execute_batch.await_count == 1


def test_pause_hook_with_malformed_payload_is_swallowed():
    ll = make()
    ll._running = True
    run(ll._handle_hook_event({"hook": "pauseGame", "data": None}))
    assert ll._running is True


def test_npc_context_survives_registry_lookup_failure():
    registry = MagicMock()
    registry.get_npc_by_name = MagicMock(side_effect=RuntimeError("x"))
    ll = make(npc_registry=registry)
    ll.state_tracker.state.player_actors = {"Aria": "u1"}
    ll.state_tracker.state.current_scene = ""
    ll.state_tracker.get_encounter_context = MagicMock(return_value="")
    ll.foundry.get_actors = AsyncMock(return_value=[{"name": "Aria", "uuid": "A1", "hp": 1, "max_hp": 2}])
    ll.foundry.get_scene_tokens = AsyncMock(return_value=[])
    ll.foundry.get_scene_details = AsyncMock(return_value=None)
    blocks = run(ll._get_npc_context(""))
    assert any("Aria" in b.text for b in blocks)


def test_allies_named_with_a_quantity_are_still_not_dropped_on_the_battlefield():
    ll = make()
    ll.foundry.get_actors = AsyncMock(return_value=[{"name": "Guard"}])
    ll.foundry.get_scene_tokens = AsyncMock(return_value=[])
    ll.foundry.get_actor_dispositions = AsyncMock(return_value={"Guard": 1})
    ll.foundry.place_token = AsyncMock()
    ll.register_ai_speaker = AsyncMock()
    run(ll._place_referenced_combatants([{"type": "narrate", "text": "Two Guards watch from the wall."}]))
    ll.foundry.place_token.assert_not_awaited()


def test_idle_countdown_does_nothing_without_a_session_or_when_stopped():
    ll = make()
    ll._running = True
    ll.db.get_active_session = AsyncMock(return_value=None)
    ll._process_proactive_action = AsyncMock()
    run(ll._idle_countdown(0))
    ll._process_proactive_action.assert_not_awaited()
    ll.db.get_active_session = AsyncMock(return_value="s")
    ll._running = False
    run(ll._idle_countdown(0))
    ll._process_proactive_action.assert_not_awaited()


# ------------------------------------------------------------ scene sync

def test_sync_active_scene_pushes_scene_into_tracker_and_awareness():
    awareness = MagicMock()
    awareness.on_scene_change = AsyncMock()
    ll = make(scene_awareness=awareness)
    ll.state_tracker.set_scene = AsyncMock()
    ll.foundry._get_active_scene_name = AsyncMock(return_value="Crypt")
    run(ll.sync_active_scene())
    ll.state_tracker.set_scene.assert_awaited_once_with("Crypt")
    awareness.on_scene_change.assert_awaited_once_with("Crypt")
    ll.state_tracker.set_scene.reset_mock()
    ll.foundry._get_active_scene_name = AsyncMock(return_value="")
    run(ll.sync_active_scene())
    ll.state_tracker.set_scene.assert_not_awaited()
    ll.state_tracker = None
    run(ll.sync_active_scene())


# -------------------------------------------------------------- downtime

def test_downtime_command_usage_receipt_and_each_stop_reason(monkeypatch):
    ll = make()
    run(ll._cmd_resolve_downtime("no colon here"))
    assert said(ll)[-1].startswith("Usage: /gm downtime")

    ll._downtime = MagicMock()
    ll._downtime.resolve = AsyncMock(return_value={"resolved": True, "player": "Bob"})
    run(ll._cmd_resolve_downtime("Bob: forge a blade"))
    ll._downtime.resolve.assert_awaited_once_with("Camp", "Bob", " forge a blade")
    assert "Downtime recorded for **Bob**" in said(ll)[-1]

    expected = {
        "empty_action": "Usage: /gm downtime",
        "no_session": "'Camp' has no session history yet",
        "llm_error": "could not be resolved; nothing was recorded",
        "no_outcome": "produced nothing to narrate",
        "weird": "could not be resolved.",
    }
    for reason, text in expected.items():
        ll._downtime.resolve = AsyncMock(return_value={"resolved": False, "stopped_reason": reason})
        run(ll._cmd_resolve_downtime("Bob: x"))
        assert text in said(ll)[-1], reason

    ll.db.get_active_session_info = AsyncMock(return_value=None)
    monkeypatch.setattr(settings, "default_campaign", "Fallback")
    run(ll._cmd_resolve_downtime("Bob: x"))
    assert ll._downtime.resolve.await_args.args[0] == "Fallback"


def test_narrate_pending_downtime_marks_only_what_was_delivered():
    ll = make()
    ll._downtime = MagicMock()
    ll._downtime.pending = AsyncMock(return_value=[
        {"id": 1, "player": "Bob", "outcome": "Forged a sword."},
        {"id": 2, "player": "Al", "outcome": "Found a map."},
    ])
    ll._downtime.mark_narrated = AsyncMock()
    run(ll._narrate_pending_downtime("sess-1", "Camp"))
    assert said(ll) == ["*While the party was apart — Bob:* Forged a sword.",
                        "*While the party was apart — Al:* Found a map."]
    ll._downtime.mark_narrated.assert_awaited_once_with("sess-1", "Camp", [1, 2])

    # delivery fails on the 2nd: nothing is marked, so it is retried next session
    ll._downtime.mark_narrated.reset_mock()
    ll.narrative_sink.narration = AsyncMock(side_effect=[None, RuntimeError("relay")])
    run(ll._narrate_pending_downtime("sess-1", "Camp"))
    ll._downtime.mark_narrated.assert_not_awaited()

    ll._downtime.pending = AsyncMock(return_value=[])
    run(ll._narrate_pending_downtime("sess-1", "Camp"))
    ll._downtime.mark_narrated.assert_not_awaited()


# --------------------------------------------------------- session start

def test_start_session_refuses_when_one_is_active():
    ll = make()
    ll.db.create_session = AsyncMock()
    ll.db.get_active_session = AsyncMock(return_value="abcdef1234567890")
    run(ll._cmd_start_session("Camp"))
    ll.db.create_session.assert_not_awaited()
    assert "already active (ID: abcdef12…)" in said(ll)[-1]


def test_start_session_creates_resets_state_rehydrates_npcs_and_opens(monkeypatch):
    registry = MagicMock()
    ll = make(npc_registry=registry)
    ll.db.get_active_session = AsyncMock(return_value=None)
    ll.db.create_session = AsyncMock()
    ll._npc_llm = MagicMock()
    ll._running = False
    ll._player_message_count = 4
    ll.state_tracker.save = AsyncMock()
    ll.sync_active_scene = AsyncMock()
    ll.load_campaign_settlements = AsyncMock()
    ll._narrate_pending_downtime = AsyncMock()
    ll._process_proactive_action = AsyncMock()
    ll._reset_idle_timer = MagicMock()
    loaded = SimpleNamespace(npcs={"n": 1}, relationships={"r": 2})
    monkeypatch.setattr(cl.npc_persistence, "load", AsyncMock(return_value=loaded))
    run(ll._cmd_start_session("Camp"))
    sid = ll.db.create_session.await_args.args[0]
    assert ll.db.create_session.await_args.args[1] == "Camp"
    ll.llm.set_usage_context.assert_called_once_with(sid, "Camp")
    ll._npc_llm.set_usage_context.assert_called_once_with(sid, "Camp")
    assert ll._running is True and ll._player_message_count == 0
    assert registry.npcs == {"n": 1} and registry.relationships == {"r": 2}   # updated in place
    assert ll.state_tracker.state.current_scene == "" and ll.state_tracker.state.scene_data == {}
    ll.state_tracker.save.assert_awaited_once()
    ll.sync_active_scene.assert_awaited_once()
    ll._narrate_pending_downtime.assert_awaited_once_with(sid, "Camp")
    ll._process_proactive_action.assert_awaited_once_with(reason="session_start")
    assert "Session started" in said(ll)[0]

    monkeypatch.setattr(cl.npc_persistence, "load", AsyncMock(side_effect=RuntimeError("db")))
    run(ll._cmd_start_session("Camp"))                                          # rehydration failure is non-fatal
    assert ll._process_proactive_action.await_count == 2


# ------------------------------------------------------- proactive beats

def _proactive(reason="idle"):
    ll = make()
    ll._turn_context = AsyncMock(return_value="CTX")
    ll.llm.generate = AsyncMock(return_value={"actions": [{"type": "narrate", "text": "x"}]})
    ll._record_actions = AsyncMock()
    ll._record_exchange = AsyncMock()
    ll.dispatcher.execute_batch = AsyncMock(return_value=[{"type": "narrate", "success": True}])
    ll._record_action_resolved_events = AsyncMock()
    ll._on_results_callback = AsyncMock()
    return ll


def test_pacing_beat_prompt_counts_exchanges_and_results_reach_dispatcher_and_callback():
    ll = _proactive()
    ll._player_message_count = 12
    scene = MagicMock()
    scene.get_context_summary = MagicMock(return_value="Goblins near")
    ll._scene_awareness = scene
    run(ll._run_proactive_action("pacing"))
    kw = ll.llm.generate.await_args.kwargs
    assert "After 12 player exchanges" in kw["user_message"]
    assert kw["extra_context"] == "CTX\n\n## SCENE\nGoblins near"
    ll._record_exchange.assert_awaited_once_with(None, [{"type": "narrate", "text": "x"}])
    ll._record_action_resolved_events.assert_awaited_once_with([{"type": "narrate", "success": True}], trigger_npcs=False)
    ll._on_results_callback.assert_awaited_once()

    ll = _proactive()
    run(ll._run_proactive_action("idle"))
    assert "NO PLAYER INPUT RECEIVED" in ll.llm.generate.await_args.kwargs["user_message"]


def test_idle_beats_are_dropped_while_a_turn_runs_or_right_after_another_beat():
    ll = _proactive()
    ll._run_proactive_action = AsyncMock()

    async def locked():
        async with ll._turn_lock:
            await ll._process_proactive_action("idle")
    run(locked())
    ll._run_proactive_action.assert_not_awaited()

    ll._last_proactive_beat_at = cl.time.monotonic()
    run(ll._process_proactive_action("pacing"))
    ll._run_proactive_action.assert_not_awaited()
    run(ll._process_proactive_action("session_start"))        # never subject to the gap
    ll._run_proactive_action.assert_awaited_once_with("session_start")


def _opening(ll, scenes=None, actors=None, prologue=None, scene_js=None):
    async def execute_js(js):
        if "game.scenes" in js:
            return {"result": scenes or []}
        return {"result": scene_js}

    ll.foundry.execute_js = execute_js
    ll.foundry.get_actors = AsyncMock(return_value=actors or [])
    ll.foundry.load_prologue = None
    return ll


def test_session_opening_prompt_includes_live_world_and_act_one_hint(monkeypatch):
    ll = _proactive()
    monkeypatch.setattr(cl, "load_prologue_entry", AsyncMock(return_value=None))
    _opening(ll,
             scenes=[{"name": "Zeta", "active": False}, {"name": "Monastery Gate", "active": True}, {"name": ""}],
             actors=[{"name": "Aria", "uuid": "A1", "has_player_owner": True}, {"name": "Orc", "uuid": "A2"}],
             scene_js={"name": "Zeta", "bg": ""})
    run(ll._run_proactive_action("session_start"))
    prompt = ll.llm.generate.await_args.kwargs["user_message"]
    assert prompt.startswith("[SESSION OPENING]")
    assert "Active scene: Zeta. Background image: NONE" in prompt
    assert 'Available Foundry scenes (all have maps): "Zeta", "Monastery Gate" (ACTIVE)' in prompt
    assert "Aria (uuid=A1)" in prompt and "Orc" not in prompt
    assert 'Switch to and narrate from "Monastery Gate"' in prompt


def test_session_opening_falls_back_to_active_scene_and_shows_bg(monkeypatch):
    ll = _proactive()
    monkeypatch.setattr(cl, "load_prologue_entry", AsyncMock(side_effect=RuntimeError("no journal")))
    _opening(ll, scenes=[{"name": "Alpha", "active": False}, {"name": "Beta", "active": True}],
             scene_js={"name": "Beta", "bg": "maps/b.webp"})
    run(ll._run_proactive_action("session_start"))
    prompt = ll.llm.generate.await_args.kwargs["user_message"]
    assert "Background image: maps/b.webp" in prompt
    assert 'Begin at "Beta"' in prompt
    # no active scene -> the first listed
    ll = _proactive()
    monkeypatch.setattr(cl, "load_prologue_entry", AsyncMock(return_value=None))
    _opening(ll, scenes=[{"name": "Alpha", "active": False}], scene_js=None)
    run(ll._run_proactive_action("session_start"))
    assert 'Begin at "Alpha"' in ll.llm.generate.await_args.kwargs["user_message"]


def test_session_opening_plays_prologue_then_cleans_up_interrupt_event(monkeypatch):
    ll = _proactive()
    entry = {"uuid": "J.1"}
    monkeypatch.setattr(cl, "load_prologue_entry", AsyncMock(return_value=entry))
    monkeypatch.setattr(cl, "describe_prologue", lambda e: "PROLOGUE SUMMARY")
    seen = {}

    async def present(foundry, say, uuid, interrupt_event=None, entry=None, cinema=None):
        seen["uuid"] = uuid
        seen["event_attr"] = foundry._prologue_interrupt_event
        seen["event"] = interrupt_event
        await say("Once upon a time")

    monkeypatch.setattr(cl, "present_prologue", present)
    _opening(ll, scenes=[], scene_js=None)
    run(ll._run_proactive_action("session_start"))
    assert seen["uuid"] == "J.1" and seen["event_attr"] is seen["event"]
    assert not isinstance(getattr(ll.foundry, "_prologue_interrupt_event", None), asyncio.Event)
    assert said(ll) == ["Once upon a time"]
    assert "PROLOGUE SUMMARY" in ll.llm.generate.await_args.kwargs["user_message"]


def test_failed_session_opening_retries_once_but_failed_idle_beat_does_not(monkeypatch):
    sleeps = []

    async def fake_sleep(n):
        sleeps.append(n)

    monkeypatch.setattr(cl.asyncio, "sleep", fake_sleep)
    ll = _proactive()
    monkeypatch.setattr(cl, "load_prologue_entry", AsyncMock(return_value=None))
    _opening(ll)
    ll.llm.generate = AsyncMock(side_effect=RuntimeError("llm down"))
    run(ll._run_proactive_action("session_start"))
    assert ll.llm.generate.await_count == 2 and sleeps == [5]

    ll = _proactive()
    ll.llm.generate = AsyncMock(side_effect=RuntimeError("llm down"))
    run(ll._run_proactive_action("idle"))
    assert ll.llm.generate.await_count == 1
