"""Behavioural coverage for foundry/chat_listener.py: helpers, lifecycle,
authorisation and echo guards, message intake, the streaming turn pipeline
and the degraded/combat paths."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import foundry.chat_listener as cl
from config import settings
from foundry.chat_listener import ChatListener, FoundryChatTransport
from llm.usage import TokenBudgetExceeded


def make(**overrides):
    kwargs = dict(
        foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    )
    kwargs.update(overrides)
    listener = ChatListener(**kwargs)
    listener.foundry._ai_name = "AI GM"
    listener.foundry.chat_message = AsyncMock()
    listener.narrative_sink = SimpleNamespace(narration=AsyncMock())
    listener.db.get_active_session = AsyncMock(return_value="sess-1")
    listener.db.get_active_session_info = AsyncMock(
        return_value={"session_id": "sess-1", "campaign": "Camp"}
    )
    listener.state_tracker.state.mode = "explore"
    listener.state_tracker.get_snapshot = MagicMock(return_value="SNAP")
    return listener


def said(listener):
    return [c.args[0] for c in listener.narrative_sink.narration.call_args_list]


# ---------------------------------------------------------------- helpers

def test_unwrap_chat_event_descends_until_content_and_stops_on_cycles():
    inner = {"content": "hi"}
    assert FoundryChatTransport.unwrap_chat_event({"data": {"data": inner}}) is inner
    loop = {}
    loop["data"] = loop
    assert FoundryChatTransport.unwrap_chat_event(loop) is loop
    # a non-dict payload ends the walk at the last dict-or-value seen
    assert FoundryChatTransport.unwrap_chat_event({"data": "str"}) == "str"
    assert FoundryChatTransport.unwrap_chat_event({"data": None}) == {"data": None}


@pytest.mark.parametrize("text,name,req,expected", [
    ("two towering Revenants lunge", "Revenant", False, 2),
    ("a horde of Skeletons", "Skeleton", False, 6),
    ("Shadows cling to the walls", "Shadow", True, 0),      # bare mention needs a quantity word
    ("Shadows cling to the walls", "Shadow", False, 3),     # plural capitalised -> 3
    ("A Goblin waits", "Goblin", False, 1),                 # singular capitalised -> 1
    ("it retreats into the shadows", "Shadow", False, 0),   # lowercase common noun
    ("", "Goblin", False, 0),
    ("text", "", False, 0),
])
def test_mention_count(text, name, req, expected):
    assert cl._mention_count(text, name, require_quantity=req) == expected


def test_mention_count_number_does_not_leak_across_a_competing_noun():
    text = "three Goblins and Skeletons"
    assert cl._mention_count(text, "Goblin", require_quantity=True) == 3
    assert cl._mention_count(text, "Skeleton", require_quantity=True) == 0


def test_plain_text_strips_html_and_unescapes():
    assert cl._plain_text("<p>Hi &amp; <b>bye</b></p>") == "Hi & bye"
    assert cl._plain_text(None) == ""


def test_parse_actions_variants():
    ll = make()
    ll.llm._extract_json = None
    assert ll._parse_actions('{"actions":[{"type":"narrate","text":"x"}]}') == [{"type": "narrate", "text": "x"}]
    # prose-only reply is narrated
    assert ll._parse_actions("  The wind howls.  ") == [{"type": "narrate", "text": "The wind howls."}]
    # truncated JSON must NOT be read aloud
    with pytest.raises(ValueError):
        ll._parse_actions('{"actions":[{"type":"narr')
    with pytest.raises(ValueError):
        ll._parse_actions("   ")
    # non-dict JSON gives no actions
    assert ll._parse_actions("[1,2]") == []
    # extractor fallback (model prepended thinking text)
    ll.llm._extract_json = lambda s: '{"actions":[{"type":"roll"}]}'
    assert ll._parse_actions("thinking... {junk") == [{"type": "roll"}]


def test_find_actions_array_start_and_decode_next_action():
    ll = make()
    text = 'x "actions" is a word, then {"actions" :\n [ {"a":1}, {"b":2}'
    start = ll._find_actions_array_start(text)
    assert text[start:].lstrip().startswith('{"a":1}')
    assert ll._find_actions_array_start('{"actions": ') is None
    obj, pos = ll._decode_next_action(text, start)
    assert obj == {"a": 1}
    obj2, pos2 = ll._decode_next_action(text, pos)
    assert obj2 == {"b": 2}
    assert ll._decode_next_action(text, pos2) == (None, pos2)
    # closing bracket, partial object and non-dict values are not actions
    assert ll._decode_next_action("]", 0) == (None, 0)
    assert ll._decode_next_action('{"a":', 0) == (None, 0)
    assert ll._decode_next_action("5", 0) == (None, 0)


# -------------------------------------------------------------- lifecycle

def test_start_subscribes_to_every_channel_and_arms_idle_timer():
    ll = make()
    ll.foundry.subscribe_to_channel = AsyncMock()
    ll.foundry.subscribe = MagicMock()
    ll._update_player_actors = AsyncMock()
    ll._update_gm_users = AsyncMock()
    ll._restore_history = AsyncMock()

    async def run():
        await ll.start()
        armed = ll._idle_timer_task is not None
        ll._cancel_idle_timer()
        return armed

    assert asyncio.run(run()) is True
    assert ll._running is True
    channels = [c.args[0] for c in ll.foundry.subscribe_to_channel.await_args_list]
    assert channels == ["chat-events", "roll-events", "combat-events", "scene-events", "hooks"]
    handlers = {c.args[0]: c.args[1] for c in ll.foundry.subscribe.call_args_list}
    assert handlers["roll-events"] == ll._handle_roll_event
    assert handlers["hooks"] == ll._handle_hook_event
    ll._update_gm_users.assert_awaited_once()
    ll._restore_history.assert_awaited_once()


def test_stop_halts_combat_loop_and_idle_timer_and_pause_resume_toggle():
    loop = MagicMock()
    loop.stop = AsyncMock()
    ll = make(combat_loop=loop)
    ll._running = True

    async def run():
        ll._reset_idle_timer()
        await ll.stop()
        assert ll._idle_timer_task is None
        await ll.resume()
        r = ll._running
        await ll.pause()
        return r, ll._running

    assert asyncio.run(run()) == (True, False)
    loop.stop.assert_awaited_once()


def test_update_player_actors_sets_mapping_and_swallows_failure():
    ll = make()
    ll.foundry.get_player_actor_mapping = AsyncMock(return_value={"actor_names": {"Aria": "u1"}})
    asyncio.run(ll._update_player_actors())
    ll.state_tracker.state.set_player_actors.assert_called_once_with({"Aria": "u1"})

    ll2 = make()
    ll2.foundry.get_player_actor_mapping = AsyncMock(side_effect=RuntimeError("relay down"))
    asyncio.run(ll2._update_player_actors())          # must not raise
    ll2.state_tracker.state.set_player_actors.assert_not_called()


def test_update_gm_users_caches_ids_and_lowercased_names():
    ll = make()
    ll.foundry.execute_js = AsyncMock(return_value={"result": [
        {"id": "u1", "name": "Chris"}, {"id": "", "name": "NoId"}, {"id": "u3"},
    ]})
    asyncio.run(ll._update_gm_users())
    assert ll._gm_user_ids == {"u1", "u3"}
    assert ll._gm_user_names == {"chris", "noid"}

    # a malformed reply leaves the previous authorisation list untouched
    ll.foundry.execute_js = AsyncMock(return_value={"result": "nope"})
    asyncio.run(ll._update_gm_users())
    assert ll._gm_user_ids == {"u1", "u3"}
    ll.foundry.execute_js = AsyncMock(side_effect=RuntimeError("x"))
    asyncio.run(ll._update_gm_users())
    assert ll._gm_user_ids == {"u1", "u3"}


def test_load_campaign_settlements_replaces_previous_campaigns_settlements(monkeypatch):
    import campaign.vault as vault
    import campaign.settlement_integration as si

    ll = make()
    ll._world_clock = MagicMock()
    ll._world_clock.settlements = {"old": object()}

    class Store:
        exists = True

        def __init__(self, name):
            self.name = name

        async def load(self):
            return {"settlements": {"a": {}}}

    monkeypatch.setattr(vault, "CampaignStore", Store)
    monkeypatch.setattr(si, "deserialize_settlements", lambda d: {"a": "S1", "b": "S2"})
    asyncio.run(ll.load_campaign_settlements("Camp"))
    assert ll._world_clock.settlements == {}
    registered = [c.args[0] for c in ll._world_clock.register_settlement.call_args_list]
    assert registered == ["S1", "S2"]


def test_load_campaign_settlements_missing_campaign_and_errors(monkeypatch):
    import campaign.vault as vault

    ll = make()
    ll._world_clock = MagicMock()
    ll._world_clock.settlements = {"old": 1}

    class Missing:
        exists = False

        def __init__(self, name):
            pass

    monkeypatch.setattr(vault, "CampaignStore", Missing)
    asyncio.run(ll.load_campaign_settlements("Nope"))
    assert ll._world_clock.settlements == {}          # still cleared
    ll._world_clock.register_settlement.assert_not_called()

    class Boom:
        def __init__(self, name):
            raise RuntimeError("vault gone")

    monkeypatch.setattr(vault, "CampaignStore", Boom)
    asyncio.run(ll.load_campaign_settlements("Nope"))  # swallowed


def test_load_campaign_settlements_without_clock_is_noop(monkeypatch):
    ll = make()
    from campaign import vault
    ll._world_clock = None
    opened = []
    monkeypatch.setattr(vault, "CampaignStore", lambda name: opened.append(name))
    asyncio.run(ll.load_campaign_settlements("x"))
    assert opened == []  # no clock: the vault is never even opened


# ------------------------------------------------------- authorisation

def test_is_gm_author_role_list_is_authoritative_once_loaded(monkeypatch):
    ll = make()
    monkeypatch.setattr(settings, "foundry_username", "")
    # not loaded yet: name fallbacks apply
    assert ll._is_gm_author({"author": {"name": "Gamemaster"}})
    assert not ll._is_gm_author({"author": {"name": "Alice"}})
    assert not ll._is_gm_author({"author": "garbage"})
    ll._gm_user_ids = {"gm1"}
    ll._gm_user_names = {"chris"}
    assert ll._is_gm_author({"author": {"id": "gm1"}})
    assert ll._is_gm_author({"user": {"_id": "gm1"}})
    assert ll._is_gm_author({"author": {"name": "Chris"}})
    # a player displaying "GM" gets nothing once the real list is known
    assert not ll._is_gm_author({"author": {"name": "GM"}})
    assert not ll._is_gm_author({})


def test_is_gm_author_configured_username_fallback(monkeypatch):
    ll = make()
    monkeypatch.setattr(settings, "foundry_username", "AIUser")
    assert ll._is_gm_author({"author": {"name": "aiuser"}})
    assert not ll._is_gm_author({"author": {"name": "other"}})


def test_speaker_name_alias_then_non_gm_author_fallback():
    ll = make()
    assert ll._speaker_name({"speaker": {"alias": "Aria"}}) == "Aria"
    assert ll._speaker_name({"speaker": "Raw"}) == "Raw"
    # role list not loaded: alias-less message stays anonymous
    assert ll._speaker_name({"author": {"name": "Bob"}}) == ""
    ll._gm_user_ids = {"gm"}
    assert ll._speaker_name({"author": {"name": "Bob", "id": "p1"}}) == "Bob"
    # the AI's own alias-less posts (GM-tier author) stay anonymous
    assert ll._speaker_name({"author": {"name": "AI", "id": "gm"}}) == ""
    assert ll._speaker_name({"author": "str"}) == ""


def _player_msg(**kw):
    base = {"content": "I open the door", "speaker": {"alias": "Aria"}, "author": {"name": "Alice", "id": "p1"}}
    base.update(kw)
    return base


def test_is_player_message_filters():
    ll = make()
    ll._gm_user_ids = {"gm"}
    check = lambda m: asyncio.run(ll._is_player_message(m))  # noqa: E731
    assert check(_player_msg()) is True
    assert check(_player_msg(content="")) is False
    assert check(_player_msg(type="system")) is False
    assert check(_player_msg(speaker={"alias": ""}, author={"name": "AI", "id": "gm"})) is False  # programmatic echo
    assert check(_player_msg(speaker={"alias": "GM"})) is False
    assert check(_player_msg(author={"name": "Gamemaster"})) is False
    ll._ai_controlled_speakers.add("Bartender")
    assert check(_player_msg(speaker={"alias": "Bartender"})) is False
    assert check(_player_msg(author={"name": "Bartender"})) is False
    # whispers: to the GM is read, between players is private, REST echo dropped
    assert check(_player_msg(whisper=["gm"])) is True
    assert check(_player_msg(whisper=["gm", "p2"])) is False
    assert check(_player_msg(whisper=["p2"])) is False
    assert check(_player_msg(whisper=["gm"], author={"name": "REST API Module"})) is False
    ll._gm_user_ids = set()
    assert check(_player_msg(whisper=["gm"])) is False                   # no role list -> never trust


def test_is_player_message_drops_echo_of_our_own_text():
    ll = make()
    asyncio.run(ll._record_sent("The door creaks open slowly."))
    assert asyncio.run(ll._is_player_message(_player_msg(content="The door creaks open slowly."))) is False
    assert asyncio.run(ll._is_player_message(_player_msg(content="something else"))) is True


def test_reset_ai_speakers_forgets_npcs_keeps_ai_names():
    ll = make()
    ll._ai_controlled_speakers.add("Old NPC")
    ll.reset_ai_speakers()
    assert ll._ai_controlled_speakers == {settings.ai_name, "AI GM"}


@pytest.mark.parametrize("lookup,registered", [(False, True), (True, False), (None, False)])
def test_register_ai_speaker_only_when_lookup_says_not_a_player(monkeypatch, lookup, registered):
    ll = make()
    monkeypatch.setattr(cl, "_is_player_character", AsyncMock(return_value=lookup))
    asyncio.run(ll.register_ai_speaker("Gruk"))
    assert ("Gruk" in ll._ai_controlled_speakers) is registered
    asyncio.run(ll.register_ai_speaker(""))
    assert "" not in ll._ai_controlled_speakers


def test_record_sent_expires_entries_older_than_five_minutes():
    ll = make()
    ll._sent_messages_with_timestamp = [("stale", time.monotonic() - 301), ("fresh", time.monotonic() - 10)]
    asyncio.run(ll._record_sent("x" * 200))
    texts = [m for m, _ in ll._sent_messages_with_timestamp]
    assert "stale" not in texts and "fresh" in texts
    assert "x" * 120 in texts and "x" * 121 not in texts     # snippets are 120 chars


def test_record_actions_records_text_embedded_narrate_and_registers_speaker(monkeypatch):
    ll = make()
    monkeypatch.setattr(cl, "_is_player_character", AsyncMock(return_value=False))
    asyncio.run(ll._record_actions([
        {"type": "speak", "text": "Halt!", "npc_name": "Guard"},
        {"type": "setup_scene", "narrate": "A cold hall."},
        {"type": "roll", "text": "ignored"},
    ]))
    texts = [m for m, _ in ll._sent_messages_with_timestamp]
    assert texts == ["Halt!", "A cold hall."]
    assert "Guard" in ll._ai_controlled_speakers


# ---------------------------------------------------------- handle_message

def _wired(ll):
    ll._running = True
    ll._run_turn = AsyncMock()
    ll._is_player_message = AsyncMock(return_value=True)
    return ll


def test_handle_message_truncates_overlong_input(monkeypatch):
    monkeypatch.setattr(settings, "chat_message_max_length", 10)
    monkeypatch.setattr(cl.playback, "stop_playback", AsyncMock())
    ll = _wired(make())
    asyncio.run(ll.handle_message({"content": "<p>" + "a" * 50 + "</p>", "speaker": {"alias": "Aria"}}))
    args = ll._run_turn.await_args.args
    assert args[0] == "a" * 10 + " ... (truncated)"
    assert args[1] == "Aria"


def test_gm_command_requires_gm_author():
    ll = _wired(make())
    ll._handle_gm_command = AsyncMock()
    ll._gm_user_ids = {"gm"}
    asyncio.run(ll.handle_message({"content": "/gm end session", "author": {"id": "p1", "name": "Bob"}}))
    ll._handle_gm_command.assert_not_awaited()
    asyncio.run(ll.handle_message({"content": "/gm end session", "author": {"id": "gm"}, "speaker": {"alias": "Chris"}}))
    ll._handle_gm_command.assert_awaited_once_with("Chris", "/gm end session")
    ll._run_turn.assert_not_awaited()


def test_message_ignored_without_session_or_while_paused_or_for_non_players():
    ll = _wired(make())
    ll.db.get_active_session = AsyncMock(return_value=None)
    asyncio.run(ll.handle_message({"content": "hi", "speaker": {"alias": "A"}}))
    ll._run_turn.assert_not_awaited()

    ll = _wired(make())
    ll._is_player_message = AsyncMock(return_value=False)
    asyncio.run(ll.handle_message({"content": "hi", "speaker": {"alias": "A"}}))
    ll._run_turn.assert_not_awaited()

    ll = _wired(make())
    ll._running = False
    asyncio.run(ll.handle_message({"content": "hi", "speaker": {"alias": "A"}}))
    ll._run_turn.assert_not_awaited()


def test_player_message_interrupts_prologue_and_npc_chat_is_routed(monkeypatch):
    monkeypatch.setattr(cl.playback, "stop_playback", AsyncMock())
    ll = _wired(make())
    ll._handle_npc_chat = AsyncMock()

    async def run():
        ev = asyncio.Event()
        ll.foundry._prologue_interrupt_event = ev
        await ll.handle_message({"content": "/npc Bob: hello", "speaker": {"alias": "A"}})
        return ev.is_set()

    assert asyncio.run(run()) is True
    ll._handle_npc_chat.assert_awaited_once_with("A", "/npc Bob: hello")
    ll._run_turn.assert_not_awaited()


def test_handle_message_failure_tells_the_table(monkeypatch):
    monkeypatch.setattr(cl.playback, "stop_playback", AsyncMock())
    ll = _wired(make())
    ll._run_turn = AsyncMock(side_effect=RuntimeError("boom"))
    asyncio.run(ll.handle_message({"content": "hi", "speaker": {"alias": "A"}}))
    assert any("collect their thoughts" in t for t in said(ll))


def test_simultaneous_players_are_merged_into_one_table_turn(monkeypatch):
    monkeypatch.setattr(cl.playback, "stop_playback", AsyncMock())
    monkeypatch.setattr(settings, "input_batch_debounce_seconds", 0.02)
    ll = _wired(make())

    async def run():
        await ll.handle_message({"content": "I attack", "speaker": {"alias": "A"}})   # solo: immediate
        await ll.handle_message({"content": "I hide", "speaker": {"alias": "B"}})
        await ll.handle_message({"content": "I run", "speaker": {"alias": "A"}})
        await ll._input_batch_task
        ll._cancel_idle_timer()

    asyncio.run(run())
    calls = [c.args for c in ll._run_turn.await_args_list]
    assert calls[0] == ("I attack", "A")
    assert calls[1] == ("B: I hide\nA: I run", "Table")
    assert len(calls) == 2


def test_flush_input_batch_single_message_keeps_speaker_and_cancel_drops_nothing():
    ll = _wired(make())
    ll._pending_batch_inputs = [("A", "only")]

    async def run():
        await ll._flush_input_batch(0)
        ll._cancel_idle_timer()

    asyncio.run(run())
    ll._run_turn.assert_awaited_once_with("only", "A")

    ll = _wired(make())
    ll._pending_batch_inputs = [("A", "keep me")]

    async def cancelled():
        t = asyncio.create_task(ll._flush_input_batch(5))
        await asyncio.sleep(0)
        t.cancel()
        await t

    asyncio.run(cancelled())
    assert ll._pending_batch_inputs == [("A", "keep me")]      # a cancelled flush must not pop
    ll._run_turn.assert_not_awaited()


def test_track_active_speaker_drops_speakers_outside_the_window():
    ll = make()
    ll._recent_speakers = {"old": time.time() - 601, "recent": time.time() - 5}
    assert ll._track_active_speaker("new") == 2
    assert "old" not in ll._recent_speakers


def test_run_turn_routes_to_combat_or_normal_and_cancels_compaction(monkeypatch):
    loop = MagicMock()
    loop.is_running = True
    ll = make(combat_loop=loop)
    ll._turn_context = AsyncMock(return_value="CTX")
    ll._process_combat_input = AsyncMock()
    ll._process_normal_input = AsyncMock()
    compaction = MagicMock()
    compaction.done.return_value = False
    ll._compaction = compaction
    monkeypatch.setattr(settings, "llm_concurrent_requests", False)

    ll.state_tracker.state.mode = "combat"
    asyncio.run(ll._run_turn("hit it", "A", owner=False))
    ll._process_combat_input.assert_awaited_once_with("hit it", "A", "SNAP", "CTX", owner=False)
    ll._process_normal_input.assert_not_awaited()
    compaction.cancel.assert_called_once()

    ll.state_tracker.state.mode = "explore"
    asyncio.run(ll._run_turn("look", "A"))
    ll._process_normal_input.assert_awaited_once_with("look", "A", "SNAP", "CTX")


# ----------------------------------------------------- streaming pipeline

def _stream(tokens):
    async def gen(**kwargs):
        for t in tokens:
            yield t
    return gen


def _ruling(action, approved=True, reason=""):
    return SimpleNamespace(action=action, approved=approved, reason=reason)


def _pipeline(tokens, rule=None):
    ll = make()
    ll.llm.generate_stream = _stream(tokens)
    ll.llm._extract_json = None
    ll._referee = MagicMock()

    async def adjudicate(actions):
        return [(rule or (lambda a: _ruling(a)))(a) for a in actions]

    ll._referee.adjudicate_batch = adjudicate
    ll.dispatcher.execute_batch = AsyncMock(side_effect=lambda acts: [{"type": a["type"], "success": True} for a in acts])
    ll._notify_llm_of_failures = AsyncMock(return_value=[])
    ll._place_referenced_combatants = AsyncMock()
    ll._handle_generated_npcs = AsyncMock()
    ll._update_immersion_state = AsyncMock()
    ll._record_action_resolved_events = AsyncMock()
    return ll


JSON = '{"actions": [{"type": "narrate", "text": "Hello"}, {"type": "update_hp", "amount": 3}]}'


def test_stream_dispatches_narration_once_then_mechanical_with_player_provenance():
    ll = _pipeline([JSON[:20], JSON[20:60], JSON[60:]])
    actions, results = asyncio.run(ll._process_player_input("go", "Aria", "S", "C"))
    assert [a["type"] for a in actions] == ["narrate", "update_hp"]
    assert all(a["source"] == "player_turn" for a in actions)
    sent = [c.args[0] for c in ll.dispatcher.execute_batch.await_args_list]
    assert [[a["type"] for a in b] for b in sent] == [["narrate"], ["update_hp"]]   # narrate not replayed
    assert len(results) == 2
    assert ll._last_stream_metrics["first_narration_s"] is not None
    ll._place_referenced_combatants.assert_awaited_once()
    ll._record_action_resolved_events.assert_awaited_once()


def test_stream_prose_only_reply_is_narrated_via_the_defensive_pass():
    ll = _pipeline(["The wind ", "howls."])
    actions, _ = asyncio.run(ll._process_player_input("go", "Aria", "S", "C"))
    assert actions == [{"type": "narrate", "text": "The wind howls.", "source": "player_turn"}]
    assert ll._last_stream_metrics["tokens"] == 2


def test_stream_referee_rejection_becomes_error_result_and_is_not_dispatched():
    def rule(a):
        return _ruling(a, approved=a["type"] != "update_hp", reason="not allowed")

    ll = _pipeline([JSON], rule=rule)
    actions, results = asyncio.run(ll._process_player_input("go", "Aria", "S", "C"))
    assert [a["type"] for a in actions] == ["narrate"]
    assert {"type": "update_hp", "error": "not allowed", "success": False} in results
    assert ll.dispatcher.execute_batch.await_count == 1
    ll._notify_llm_of_failures.assert_awaited_once()
    assert ll._notify_llm_of_failures.await_args.kwargs == {"source": "player_turn"}


def test_stream_rejected_narration_is_not_delivered():
    ll = _pipeline(['{"actions":[{"type":"narrate","text":"Nope"}]}'],
                   rule=lambda a: _ruling(a, approved=False))
    actions, results = asyncio.run(ll._process_player_input("go", "Aria", "S", "C"))
    assert actions == []
    assert results[0]["error"] == "Action rejected by referee"
    ll.dispatcher.execute_batch.assert_not_awaited()


def test_stream_advances_combat_turn_only_when_asked():
    loop = MagicMock()
    loop.is_running = True
    ll = _pipeline([JSON])
    ll._combat_loop = loop
    asyncio.run(ll._process_player_input("go", "A", "S", "C", advance_turn=True))
    loop.advance_pc_turn.assert_called_once()
    loop.advance_pc_turn.reset_mock()
    asyncio.run(ll._process_player_input("go", "A", "S", "C", advance_turn=False))
    loop.advance_pc_turn.assert_not_called()


def test_stream_budget_exhaustion_is_silent_but_other_errors_are_announced():
    ll = _pipeline([JSON])

    async def exhausted(**kw):
        raise TokenBudgetExceeded("session", 10, 5, 12)
        yield  # pragma: no cover

    ll.llm.generate_stream = exhausted
    assert asyncio.run(ll._process_player_input("go", "A", "S", "C")) == ([], [])
    ll.narrative_sink.narration.assert_not_awaited()

    async def broken(**kw):
        raise RuntimeError("net")
        yield  # pragma: no cover

    ll.llm.generate_stream = broken
    assert asyncio.run(ll._process_player_input("go", "A", "S", "C")) == ([], [])
    assert any("scene holding its breath" in t for t in said(ll))

    ll.narrative_sink.narration = AsyncMock(side_effect=RuntimeError("sink down"))
    assert asyncio.run(ll._process_player_input("go", "A", "S", "C")) == ([], [])   # still no raise


# ------------------------------------------------- degraded + budget paths

def test_handle_budget_exhausted_outside_combat_enters_degraded_mode_and_announces():
    ll = make()
    asyncio.run(ll.handle_budget_exhausted(RuntimeError("cap")))
    assert ll._degraded_mode_active is True
    assert "budget is exhausted" in ll.foundry.chat_message.await_args.args[0]
    ll.foundry.chat_message = AsyncMock(side_effect=RuntimeError("relay"))
    asyncio.run(ll.handle_budget_exhausted(RuntimeError("cap")))       # announcement failure swallowed
    assert ll._degraded_mode_active is True


def test_handle_budget_exhausted_in_combat_delegates_to_combat_loop():
    loop = MagicMock()
    loop.is_running = True
    loop.enter_degraded_mode = AsyncMock()
    ll = make(combat_loop=loop)
    err = RuntimeError("cap")
    asyncio.run(ll.handle_budget_exhausted(err))
    loop.enter_degraded_mode.assert_awaited_once_with(err)
    assert ll._degraded_mode_active is False


def test_budget_available_and_leave_degraded_mode():
    ll = make()
    assert asyncio.run(ll._is_budget_available()) is True            # no tracker -> never blocks
    ll._token_usage = MagicMock()
    ll._token_usage.budget_available = AsyncMock(return_value=False)
    assert asyncio.run(ll._is_budget_available()) is False
    ll._token_usage.budget_available.assert_awaited_once_with("sess-1")

    asyncio.run(ll._leave_degraded_mode())                           # not degraded: silent
    ll.foundry.chat_message.assert_not_awaited()
    ll._degraded_mode_active = True
    ll.foundry.chat_message = AsyncMock(side_effect=RuntimeError("x"))
    asyncio.run(ll._leave_degraded_mode())
    assert ll._degraded_mode_active is False                          # flag flips even if announce fails


def test_normal_input_degraded_echoes_escaped_without_llm_unless_budget_returns():
    ll = make()
    ll._degraded_mode_active = True
    ll._is_budget_available = AsyncMock(return_value=False)
    ll._process_player_input = AsyncMock()
    asyncio.run(ll._process_normal_input("<b>hit</b>", "Al<ice>", "S", "C"))
    ll._process_player_input.assert_not_awaited()
    assert said(ll) == ["*Al&lt;ice&gt;: &lt;b&gt;hit&lt;/b&gt;*"]

    ll._is_budget_available = AsyncMock(return_value=True)
    ll._leave_degraded_mode = AsyncMock(side_effect=lambda: setattr(ll, "_degraded_mode_active", False))
    ll._process_player_input = AsyncMock(return_value=([], []))
    ll._record_exchange = AsyncMock()
    asyncio.run(ll._process_normal_input("go", "A", "S", "C"))
    ll._process_player_input.assert_awaited_once()


def test_degraded_echo_failure_is_swallowed(caplog):
    ll = make()
    ll.narrative_sink.narration = AsyncMock(side_effect=RuntimeError("x"))
    with caplog.at_level("WARNING"):
        asyncio.run(ll._process_degraded_input("a", "b"))
    ll.narrative_sink.narration.assert_awaited_once_with("*b: a*", speaker="GM")
    assert "Could not echo player action: x" in caplog.text


def test_normal_input_records_reinforcement_flags_pacing_and_notifies():
    ll = make()
    ll._process_player_input = AsyncMock(return_value=([{"type": "narrate"}], [{"ok": 1}]))
    ll._record_exchange = AsyncMock()
    ll._reinforcement_mgr = MagicMock()
    ll._reinforcement_mgr.record_turn = AsyncMock()
    ll._on_results_callback = AsyncMock()
    ll._player_message_count = 10
    asyncio.run(ll._process_normal_input("go", "Aria", "S", "C"))
    ll._record_exchange.assert_awaited_once_with("go", [{"type": "narrate"}])
    ll._reinforcement_mgr.record_turn.assert_awaited_once_with("[Aria]: go", '[{"ok": 1}]')
    assert ll._pacing_due is True
    ll._on_results_callback.assert_awaited_once_with([{"ok": 1}])

    ll._pacing_due = False
    ll._player_message_count = 7
    asyncio.run(ll._process_normal_input("go", "Aria", "S", "C"))
    assert ll._pacing_due is False


def test_normal_input_error_falls_back_to_narration():
    ll = make()
    ll._process_player_input = AsyncMock(side_effect=RuntimeError("x"))
    asyncio.run(ll._process_normal_input("go", "A", "S", "C"))
    assert any("holding its breath" in t for t in said(ll))
    ll.narrative_sink.narration = AsyncMock(side_effect=RuntimeError("y"))
    asyncio.run(ll._process_normal_input("go", "A", "S", "C"))        # no raise


# ------------------------------------------------------- records / memory

def test_record_exchange_logs_turn_and_starts_compaction_once(monkeypatch):
    memory = MagicMock()
    memory.maybe_compact = MagicMock(return_value="coro")
    ll = make(campaign_memory=memory)
    ll.db.save_conversation = AsyncMock()
    spawned = []
    monkeypatch.setattr(cl, "spawn", lambda c: spawned.append(c) or MagicMock(done=lambda: False))
    asyncio.run(ll._record_exchange("hello", [{"type": "narrate"}]))
    saved = [c.args for c in ll.db.save_conversation.await_args_list]
    assert saved == [("sess-1", "Camp", "user", "hello"), ("sess-1", "Camp", "assistant", '{"type": "narrate"}')]
    assert spawned == ["coro"]
    asyncio.run(ll._record_exchange(None, []))                          # compaction still running
    assert spawned == ["coro"]

    ll.db.get_active_session_info = AsyncMock(return_value=None)
    ll.db.save_conversation.reset_mock()
    asyncio.run(ll._record_exchange("x", []))
    ll.db.save_conversation.assert_not_awaited()


def test_memory_context_and_restore_history():
    ll = make()
    assert asyncio.run(ll._memory_context("q")) == ""
    assert asyncio.run(ll._restore_history()) is None

    memory = MagicMock()
    memory.context_block = AsyncMock(return_value="BLOCK")
    memory.recent_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    ll = make(campaign_memory=memory)
    ll.llm.restore_history = AsyncMock()
    ll.state_tracker.state.current_scene = "Hall"
    assert asyncio.run(ll._memory_context("q")) == "BLOCK"
    memory.context_block.assert_awaited_once_with("Camp", "sess-1", "q\nHall")
    asyncio.run(ll._restore_history())
    ll.llm.restore_history.assert_awaited_once_with([{"role": "user", "content": "x"}])

    memory.context_block = AsyncMock(side_effect=RuntimeError("x"))
    memory.recent_messages = AsyncMock(side_effect=RuntimeError("x"))
    assert asyncio.run(ll._memory_context("q")) == ""
    asyncio.run(ll._restore_history())                                  # swallowed
    ll.db.get_active_session_info = AsyncMock(return_value=None)
    assert asyncio.run(ll._memory_context("q")) == ""


# ------------------------------------------------------------ combat input

def test_owns_current_turn():
    loop = SimpleNamespace(awaiting_pc={"name": "Aria"})
    ll = make(combat_loop=loop)
    ll.state_tracker.state.player_actors = {"Aria": "u1"}
    assert ll._owns_current_turn({"author": {"id": "u1"}}) is True
    assert ll._owns_current_turn({"author": {"id": "u2"}}) is False
    assert ll._owns_current_turn({"author": "u1"}) is True
    assert ll._owns_current_turn({}) is True            # unknown author: never wedge the fight
    loop.awaiting_pc = None
    assert ll._owns_current_turn({"author": {"id": "u1"}}) is False   # NPC turn: nobody owns it
    ll._combat_loop = None
    assert ll._owns_current_turn({}) is False


def test_schedule_turn_end_only_advances_the_turn_it_was_scheduled_for():
    loop = MagicMock()
    loop.is_running = True
    loop.turn_key = "t1"
    ll = make(combat_loop=loop)

    async def run():
        ll._schedule_turn_end(0)
        await ll._turn_end_task
        first = loop.advance_pc_turn.call_count
        ll._schedule_turn_end(0.01)
        loop.turn_key = "t2"                              # turn moved on before the timer fired
        await ll._turn_end_task
        second = loop.advance_pc_turn.call_count
        ll._schedule_turn_end(5)
        ll._cancel_turn_end()
        return first, second, ll._turn_end_task

    assert asyncio.run(run()) == (1, 1, None)
    loop.is_running = False
    ll._schedule_turn_end(0)
    assert ll._turn_end_task is None


def _combat(quiet, owner=True, content="I swing", **kw):
    loop = MagicMock()
    loop.is_running = True
    loop.reaction_declared = False
    ll = make(combat_loop=loop)
    ll._process_player_input = AsyncMock(return_value=([], []))
    ll._record_exchange = AsyncMock()
    ll._schedule_turn_end = MagicMock()
    return ll, loop


def test_combat_input_end_turn_phrase_advances_and_plain_message_schedules_quiet_timer(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 8.0)
    ll, loop = _combat(8.0)
    asyncio.run(ll._process_combat_input("I'm done.", "A", "S", "C"))
    assert ll._process_player_input.await_args.kwargs["advance_turn"] is True
    ll._schedule_turn_end.assert_not_called()

    asyncio.run(ll._process_combat_input("I swing my axe", "A", "S", "C"))
    assert ll._process_player_input.await_args.kwargs["advance_turn"] is False
    ll._schedule_turn_end.assert_called_once_with(8.0)

    ll._schedule_turn_end.reset_mock()
    asyncio.run(ll._process_combat_input("Can I reach him?", "A", "S", "C"))      # question: stay open
    ll._schedule_turn_end.assert_not_called()

    asyncio.run(ll._process_combat_input("a reaction", "B", "S", "C", owner=False))
    assert ll._process_player_input.await_args.kwargs["advance_turn"] is False
    ll._schedule_turn_end.assert_not_called()


def test_combat_input_quiet_zero_ends_turn_immediately(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0)
    ll, loop = _combat(0)
    asyncio.run(ll._process_combat_input("I swing", "A", "S", "C"))
    assert ll._process_player_input.await_args.kwargs["advance_turn"] is True


def test_combat_input_failure_waves_action_through_and_signals_reaction_done():
    ll, loop = _combat(8.0)
    ll._process_player_input = AsyncMock(side_effect=RuntimeError("x"))
    loop.reaction_declared = True
    asyncio.run(ll._process_combat_input("go", "A", "S", "C"))
    assert any("waves the action through" in t for t in said(ll))
    loop.advance_pc_turn.assert_called_once()          # no deadlock
    loop.reaction_processed.set.assert_called_once()

    ll, loop = _combat(8.0)
    ll._process_player_input = AsyncMock(side_effect=RuntimeError("x"))
    asyncio.run(ll._process_combat_input("go", "B", "S", "C", owner=False))
    loop.advance_pc_turn.assert_not_called()           # a non-owner's failure never skips the owner's turn


def test_combat_input_notifies_results_callback():
    ll, loop = _combat(8.0)
    ll._on_results_callback = AsyncMock()
    ll._process_player_input = AsyncMock(return_value=([], [{"r": 1}]))
    asyncio.run(ll._process_combat_input("go", "A", "S", "C"))
    ll._on_results_callback.assert_awaited_once_with([{"r": 1}])
