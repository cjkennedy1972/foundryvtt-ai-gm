"""Behavioural coverage for foundry/chat_listener.py, part 2: /gm commands,
Foundry event handlers, turn context, failure retry, token reconciliation,
NPC wiring, idle pacing, proactive beats and session start."""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


import foundry.chat_listener as cl
from config import settings
from foundry.chat_listener import ChatListener
from foundry.turn_context import Block


def make(**overrides):
    kwargs = dict(
        foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    )
    kwargs.update(overrides)
    ll = ChatListener(**kwargs)
    ll.foundry._ai_name = "AI GM"
    ll.foundry.chat_message = AsyncMock()
    ll.narrative_sink = SimpleNamespace(narration=AsyncMock())
    ll.db.get_active_session = AsyncMock(return_value="sess-1")
    ll.db.get_active_session_info = AsyncMock(return_value={"session_id": "sess-1", "campaign": "Camp"})
    ll.state_tracker.state.mode = "explore"
    ll.state_tracker.get_snapshot = MagicMock(return_value="SNAP")
    return ll


def said(ll):
    return [c.args[0] for c in ll.narrative_sink.narration.call_args_list]


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- commands

def test_dispatcher_routes_each_subcommand_to_its_handler():
    ll = make()
    for name in ("_cmd_start_session", "_cmd_resolve_downtime", "_start_combat", "_cmd_canonize",
                 "_cmd_session_replay", "_cmd_session_events", "_cmd_settlement_list",
                 "_cmd_settlement_query", "_cmd_end_session", "_cmd_canon_review",
                 "_cmd_canon_decision"):
        setattr(ll, name, AsyncMock())
    ll._cmd_ask_gm_ai = AsyncMock()
    ll.dispatcher.undo_last = AsyncMock(return_value={"success": True, "label": "an HP change"})

    run(ll._handle_gm_command("GM", "/gm start session"))
    ll._cmd_start_session.assert_awaited_once_with(settings.default_campaign or "Adventure")
    run(ll._handle_gm_command("GM", "/gm start session Krynn"))
    ll._cmd_start_session.assert_awaited_with("Krynn")
    run(ll._handle_gm_command("GM", "/gm downtime Bob: forge"))
    ll._cmd_resolve_downtime.assert_awaited_once_with("Bob: forge")
    run(ll._handle_gm_command("GM", "/gm start combat"))
    ll._start_combat.assert_awaited_once()
    run(ll._handle_gm_command("GM", "/gm rule Dragons fear salt"))
    run(ll._handle_gm_command("GM", "/gm canonize The king died"))
    assert [c.args[0] for c in ll._cmd_canonize.await_args_list] == ["rule Dragons fear salt", "canonize The king died"]
    run(ll._handle_gm_command("GM", "/gm session replay 5"))
    ll._cmd_session_replay.assert_awaited_once_with("session replay 5")
    run(ll._handle_gm_command("GM", "/gm session events action_resolved"))
    ll._cmd_session_events.assert_awaited_once_with("session events action_resolved")
    run(ll._handle_gm_command("GM", "/gm settlement list"))
    ll._cmd_settlement_list.assert_awaited_once()
    run(ll._handle_gm_command("GM", "/gm settlement query town 8:00"))
    ll._cmd_settlement_query.assert_awaited_once_with("settlement query town 8:00")
    run(ll._handle_gm_command("GM", "/gm end session"))
    ll._cmd_end_session.assert_awaited_once()
    run(ll._handle_gm_command("GM", "/gm canon review"))
    ll._cmd_canon_review.assert_awaited_once()
    run(ll._handle_gm_command("GM", "/gm canon approve 1"))
    run(ll._handle_gm_command("GM", "/gm canon reject 2"))
    assert ll._cmd_canon_decision.await_count == 2
    run(ll._handle_gm_command("GM", "/gm undo"))
    assert said(ll)[-1] == "GM: undid an HP change."
    ll.dispatcher.undo_last = AsyncMock(return_value={"success": False, "error": "nothing to undo"})
    run(ll._handle_gm_command("GM", "/gm undo"))
    assert said(ll)[-1] == "GM: could not undo — nothing to undo"
    run(ll._handle_gm_command("GM", "/ask what is a lich"))
    ll._cmd_ask_gm_ai.assert_awaited_once_with("what is a lich", "/ask what is a lich")


def test_pause_resume_and_stop_combat_commands():
    loop = MagicMock()
    loop.stop = AsyncMock()
    ll = make(combat_loop=loop)
    ll._running = True
    run(ll._handle_gm_command("GM", "/gm pause ai"))
    assert ll._running is False and said(ll)[-1] == "GM: AI is paused."
    run(ll._handle_gm_command("GM", "/gm resume ai"))
    assert ll._running is True and said(ll)[-1] == "GM: AI is now active."
    run(ll._handle_gm_command("GM", "/gm stop combat"))
    loop.stop.assert_awaited_once()
    ll._combat_loop = None
    run(ll._handle_gm_command("GM", "/gm stop combat"))        # no loop: still answers
    assert said(ll)[-1] == "Combat loop stopped."


def test_ask_gm_ai_usage_answer_and_unknown():
    ll = make()
    ll.llm.generate_text = AsyncMock(return_value="A lich is an undead mage.")
    run(ll._handle_gm_command("GM", "/ask"))
    assert said(ll) == ["Usage: /ask <question>"]
    ll.llm.generate_text.assert_not_awaited()
    run(ll._handle_gm_command("GM", "/ask what is a lich"))
    ll.llm.generate_text.assert_awaited_once_with("what is a lich")
    assert said(ll)[-1] == "A lich is an undead mage."
    ll.llm.generate_text = AsyncMock(return_value="")
    run(ll._handle_gm_command("GM", "/gm frobnicate"))
    assert said(ll)[-1] == "Unknown command: frobnicate. Use /gm help."


def test_canonize_requires_session_pushes_fact_and_reports_failures(monkeypatch):
    ll = make()
    ll.db.get_active_session_info = AsyncMock(return_value=None)
    run(ll._cmd_canonize("rule x"))
    assert "active session is required" in said(ll)[-1]

    ll = make()
    pushed = AsyncMock()
    monkeypatch.setattr(cl.obsidian_sync, "resolve_vault_path", lambda p: Path("/vault"))
    monkeypatch.setattr(cl.obsidian_sync, "get_campaign_folder", lambda v, c: v / c)
    monkeypatch.setattr(cl.obsidian_sync, "push_canon_fact_live", pushed)
    run(ll._cmd_canonize("canonize  Salt repels dragons "))
    pushed.assert_awaited_once_with(Path("/vault/Camp"), "Salt repels dragons", ll.llm)
    assert said(ll)[-1].endswith("Canon updated: Salt repels dragons")

    pushed.side_effect = RuntimeError("disk full")
    run(ll._cmd_canonize("rule Salt"))
    assert "Failed to update canon: disk full" in said(ll)[-1]


def _fake_replay(monkeypatch, events=(), boom=None):
    calls = {}

    class SessionReplay:
        def __init__(self, store):
            calls["store"] = store

        async def get_session_transcript(self, campaign, session_id=None, limit=None):
            if boom:
                raise boom
            calls["transcript"] = (campaign, session_id, limit)
            return list(events)

        async def find_events_by_type(self, campaign, event_type, session_id=None):
            if boom:
                raise boom
            calls["find"] = (campaign, event_type, session_id)
            return list(events)

        def format_transcript_for_chat(self, evs):
            return f"TRANSCRIPT({len(evs)})"

    mod = types.ModuleType("events.replay")
    mod.SessionReplay = SessionReplay
    monkeypatch.setitem(sys.modules, "events.replay", mod)
    return calls


def test_session_replay_limit_default_and_failures(monkeypatch):
    ll = make()
    calls = _fake_replay(monkeypatch, events=[1, 2])
    run(ll._cmd_session_replay("session replay"))
    assert calls["transcript"] == ("Camp", "sess-1", 20)
    assert calls["store"] is ll._event_store
    assert said(ll)[-1] == "TRANSCRIPT(2)"
    run(ll._cmd_session_replay("session replay 5"))
    assert calls["transcript"][2] == 5
    run(ll._cmd_session_replay("session replay abc"))        # non-numeric limit is reported, not raised
    assert said(ll)[-1].startswith("❌ Replay failed")

    ll.db.get_active_session_info = AsyncMock(return_value=None)
    run(ll._cmd_session_replay("session replay"))
    assert said(ll)[-1].startswith("No active session")


def test_session_events_filters_by_type(monkeypatch):
    ll = make()
    calls = _fake_replay(monkeypatch, events=[])
    run(ll._cmd_session_events("session events combat_start"))
    assert calls["find"] == ("Camp", "combat_start", "sess-1")
    assert said(ll)[-1] == "No events of type 'combat_start' found."
    calls = _fake_replay(monkeypatch, events=[1])
    run(ll._cmd_session_events("session events combat_start"))
    assert said(ll)[-1] == "TRANSCRIPT(1)"
    _fake_replay(monkeypatch, boom=RuntimeError("db"))
    run(ll._cmd_session_events("session events x"))
    assert said(ll)[-1] == "❌ Lookup failed: db"
    ll.db.get_active_session_info = AsyncMock(return_value=None)
    run(ll._cmd_session_events("session events x"))
    assert said(ll)[-1].startswith("No active session")


def test_settlement_list_and_query():
    ll = make()
    ll._world_clock = None
    run(ll._cmd_settlement_list())
    run(ll._cmd_settlement_query("settlement query town"))
    assert said(ll) == ["Settlement system not initialized."] * 2

    clock = MagicMock()
    ll._world_clock = clock
    clock.list_settlements.return_value = []
    run(ll._cmd_settlement_list())
    assert "No settlements registered" in said(ll)[-1]
    clock.list_settlements.return_value = [
        SimpleNamespace(name="Oakvale", region="North", population=300, npcs=[1, 2], buildings=[1]),
    ]
    run(ll._cmd_settlement_list())
    assert "**Oakvale** (North): 300 pop, 2 NPCs, 1 buildings" in said(ll)[-1]
    clock.list_settlements.side_effect = RuntimeError("x")
    run(ll._cmd_settlement_list())
    assert said(ll)[-1] == "❌ Settlement list failed: x"

    run(ll._cmd_settlement_query("settlement query "))
    assert said(ll)[-1].startswith("Usage: /gm settlement query")
    clock.get_current_time.return_value = "noon"
    clock.query_location_at_time = AsyncMock(return_value={})
    run(ll._cmd_settlement_query("settlement query oakvale"))
    clock.query_location_at_time.assert_awaited_with("oakvale", None)
    assert said(ll)[-1] == "📍 No NPCs found in oakvale at noon"
    clock.query_location_at_time = AsyncMock(return_value={"Tavern": ["Bob", "Al"], "Smithy": ["Tom"]})
    run(ll._cmd_settlement_query("settlement query oakvale dusk"))
    clock.query_location_at_time.assert_awaited_with("oakvale", "dusk")
    out = said(ll)[-1]
    assert out.index("Smithy") < out.index("Tavern") and "Bob, Al" in out and "**dusk:**" in out
    clock.query_location_at_time = AsyncMock(side_effect=RuntimeError("y"))
    run(ll._cmd_settlement_query("settlement query oakvale"))
    assert said(ll)[-1] == "❌ Settlement query failed: y"


def test_end_session_without_session_and_full_close(monkeypatch):
    ll = make()
    ll.db.get_active_session_info = AsyncMock(return_value=None)
    run(ll._cmd_end_session())
    assert said(ll) == ["No active session to end."]

    memory = MagicMock()
    memory.close_session = AsyncMock(return_value="Recap text")
    clock = MagicMock()
    clock.advance = AsyncMock()
    ll = make(campaign_memory=memory)
    ll._world_clock = clock
    ll._npc_llm = MagicMock()
    ll.db.close_session = AsyncMock()
    ll._export_session_recap = AsyncMock()
    ll._generate_and_store_canon_proposals = AsyncMock()
    ll._maybe_trigger_npc_agents = AsyncMock()
    ll._npc_registry = MagicMock()
    saved = AsyncMock()
    monkeypatch.setattr(cl.npc_persistence, "save", saved)
    monkeypatch.setattr(cl.obsidian_sync, "resolve_vault_path", lambda p: Path("/vault"))
    monkeypatch.setattr(cl.obsidian_sync, "get_campaign_folder", lambda v, c: v / c)
    run(ll._cmd_end_session())
    ll._export_session_recap.assert_awaited_once_with("sess-1", Path("/vault/Camp"), "Recap text")
    ll._generate_and_store_canon_proposals.assert_awaited_once_with("sess-1", "Camp", Path("/vault/Camp"), "Recap text")
    clock.advance.assert_awaited_once_with("sess-1", "Camp", settings.world_clock_session_end_advance_seconds)
    saved.assert_awaited_once()
    ll.db.close_session.assert_awaited_once_with("sess-1")
    ll.llm.set_usage_context.assert_called_with(None, "")
    ll._npc_llm.set_usage_context.assert_called_with(None, "")
    assert said(ll)[-1] == "🛑 Session ended."


def test_end_session_survives_memory_clock_and_recap_failures(monkeypatch):
    memory = MagicMock()
    memory.close_session = AsyncMock(side_effect=RuntimeError("llm down"))
    clock = MagicMock()
    clock.advance = AsyncMock(side_effect=RuntimeError("clock"))
    ll = make(campaign_memory=memory)
    ll._world_clock = clock
    ll.db.close_session = AsyncMock()
    ll._export_session_recap = AsyncMock(side_effect=RuntimeError("recap"))
    ll._generate_and_store_canon_proposals = AsyncMock(side_effect=RuntimeError("canon"))
    ll.db.get_active_session_info = AsyncMock(return_value={"session_id": "sess-1", "campaign": ""})
    run(ll._cmd_end_session())
    ll._export_session_recap.assert_awaited_once_with("sess-1", None, "No session highlights recorded.")
    ll.db.close_session.assert_awaited_once_with("sess-1")          # ending is never blocked
    assert said(ll)[-1] == "🛑 Session ended."


def test_export_session_recap_writes_journal_and_vault(monkeypatch):
    ll = make()
    ll.foundry.create_entity = AsyncMock()
    monkeypatch.setattr(cl, "book_flags", AsyncMock(return_value={}))
    saved = AsyncMock()
    monkeypatch.setattr(cl.obsidian_sync, "save_session_recap", saved)
    run(ll._export_session_recap("sess-1", Path("/v/Camp"), "line1\nline2"))
    kind, doc = ll.foundry.create_entity.await_args.args
    assert kind == "JournalEntry"
    assert doc["pages"][0]["text"]["content"] == "line1<br>line2"
    assert doc["flags"]["ai-gm"] == {"type": "session_recap", "session_id": "sess-1"}
    saved.assert_awaited_once_with(Path("/v/Camp"), "sess-1", "line1\nline2")
    assert "Session recap saved" in said(ll)[-1]

    ll.foundry.create_entity = AsyncMock(side_effect=RuntimeError("relay"))
    run(ll._export_session_recap("sess-1", None, "x"))
    assert "recap export failed: relay" in said(ll)[-1]


def test_generate_canon_proposals_stores_each_and_never_auto_approves(monkeypatch, tmp_path):
    memory = MagicMock()
    memory.canon_candidates = AsyncMock(return_value="HIGHLIGHTS")
    ll = make(campaign_memory=memory)
    ll.llm._http = "HTTP"
    ll.db.create_canon_proposal = AsyncMock()
    (tmp_path / "Canon.md").write_text("OLD CANON", encoding="utf-8")
    gen = AsyncMock(return_value=[
        {"fact": "f1", "confidence": "high", "rationale": "r1", "contradiction_note": ""},
        {"fact": "f2", "confidence": "low", "rationale": "r2", "contradiction_note": "clash"},
    ])
    monkeypatch.setattr(cl, "generate_canon_proposals", gen)
    run(ll._generate_and_store_canon_proposals("sess-1", "Camp", tmp_path, "RECAP"))
    memory.canon_candidates.assert_awaited_once_with("Camp", "sess-1", "RECAP")
    assert gen.await_args.args[0] == "HTTP"
    assert gen.await_args.args[-2:] == ("HIGHLIGHTS", "OLD CANON")
    assert gen.await_args.args[1].endswith("/chat/completions?thinking=false")
    stored = [c.kwargs for c in ll.db.create_canon_proposal.await_args_list]
    assert [(k["fact"], k["contradiction_note"]) for k in stored] == [("f1", ""), ("f2", "clash")]
    assert "2 canon proposal(s) awaiting review" in said(ll)[-1]

    # no Canon.md yet -> empty existing text; no proposals -> no message
    n = len(said(ll))
    gen.return_value = []
    run(ll._generate_and_store_canon_proposals("sess-1", "Camp", tmp_path / "nope", ""))
    assert len(said(ll)) == n
    # no memory/folder: nothing happens; generation failure is swallowed
    run(make()._generate_and_store_canon_proposals("s", "c", tmp_path))
    gen.side_effect = RuntimeError("llm")
    run(ll._generate_and_store_canon_proposals("sess-1", "Camp", tmp_path, ""))


def test_canon_review_numbers_proposals_and_decision_maps_back_to_ids(monkeypatch):
    ll = make()
    ll.db.get_pending_canon_proposals = AsyncMock(return_value=[])
    run(ll._cmd_canon_review())
    assert said(ll)[-1] == "No pending canon proposals."

    props = [{"id": 100 + i, "fact": f"fact{i}", "confidence": "high", "rationale": "why",
              "contradiction_note": "clash" if i == 0 else ""} for i in range(7)]
    ll.db.get_pending_canon_proposals = AsyncMock(return_value=props)
    run(ll._cmd_canon_review())
    assert ll._canon_review_ids == [100, 101, 102, 103, 104]          # only the first five are numbered
    out = said(ll)[-1]
    assert "1. [HIGH] fact0 — why ⚠️ conflicts with: clash" in out and "fact5" not in out

    approve = AsyncMock(return_value=(True, "ok"))
    reject = AsyncMock(return_value=(False, "gone"))
    monkeypatch.setattr(cl, "approve_canon_proposal_with_vault_write", approve)
    monkeypatch.setattr(cl, "reject_canon_proposal_safely", reject)
    run(ll._cmd_canon_decision("canon approve 2"))
    assert approve.await_args.args[2] == 101
    assert said(ll)[-1] == "✅ Canon proposal #2 approved."
    approve.return_value = (False, "conflict")
    run(ll._cmd_canon_decision("canon approve 1"))
    assert said(ll)[-1] == "⚠️ Proposal #1 not approved: conflict"
    run(ll._cmd_canon_decision("canon reject 3"))
    assert reject.await_args.args[1] == 102
    assert said(ll)[-1] == "⚠️ Proposal #3 not rejected: gone"
    reject.return_value = (True, "")
    run(ll._cmd_canon_decision("canon reject 3"))
    assert said(ll)[-1] == "❌ Canon proposal #3 rejected."
    for bad in ("0", "6", "x", "-1"):
        run(ll._cmd_canon_decision(f"canon approve {bad}"))
        assert said(ll)[-1] == f"Invalid proposal number: {bad}"
    ll._canon_review_ids = []
    run(ll._cmd_canon_decision("canon approve 1"))
    assert said(ll)[-1] == "Run /gm canon review first."


def test_start_combat_needs_loop_and_tokens(monkeypatch):
    ll = make()
    run(ll._start_combat())
    assert said(ll) == ["Combat loop not available."]
    loop = MagicMock()
    loop.start_combat_loop = MagicMock(return_value="coro")
    ll = make(combat_loop=loop)
    ll.foundry.get_scene_tokens = AsyncMock(return_value=[])
    run(ll._start_combat())
    assert "No tokens found" in said(ll)[-1]
    spawned = []
    monkeypatch.setattr(cl, "spawn", spawned.append)
    ll.foundry.get_scene_tokens = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    run(ll._start_combat())
    loop.start_combat_loop.assert_called_once_with([{"id": 1}, {"id": 2}])
    assert spawned == ["coro"] and "2 tokens engaged" in said(ll)[-1]


# --------------------------------------------------------------- events

def test_roll_event_resets_idle_clock_and_swallows_errors():
    ll = make()
    ll._note_player_activity = MagicMock()
    run(ll._handle_roll_event({"data": {"roll": 17, "speaker": "Aria"}}))
    ll._note_player_activity.assert_called_once()
    ll.state_tracker.state.mode = "combat"
    run(ll._handle_roll_event({"total": 5}))
    ll._note_player_activity.side_effect = RuntimeError("x")
    run(ll._handle_roll_event({}))                                     # no raise


def test_combat_events_start_end_and_turn(monkeypatch):
    loop = MagicMock()
    loop.start_combat_loop = MagicMock(return_value="coro")
    loop.stop = AsyncMock()
    ll = make(combat_loop=loop)
    ll.state_tracker.set_combat_mode = AsyncMock()
    ll.state_tracker.update_combat = AsyncMock()
    ll.foundry.get_scene_tokens = AsyncMock(return_value=[{"id": 1}])
    spawned = []
    monkeypatch.setattr(cl, "spawn", spawned.append)

    run(ll._handle_combat_event({"type": "encounter-started"}))
    ll.state_tracker.set_combat_mode.assert_awaited_with(in_combat=True)
    assert spawned == ["coro"] and said(ll) == ["⚔️ AI combat loop started."]

    ll.foundry.get_scene_tokens = AsyncMock(return_value=[])
    run(ll._handle_combat_event({"data": {"type": "start"}}))          # no tokens: no loop
    assert spawned == ["coro"]

    run(ll._handle_combat_event({"type": "encounter-ended"}))
    ll.state_tracker.set_combat_mode.assert_awaited_with(in_combat=False)
    loop.stop.assert_awaited_once()

    ll.state_tracker.state.combat.turn = 3
    ll.state_tracker.state.combat.turn_order = ["a", "b", "c"]
    run(ll._handle_combat_event({"type": "encounter-turn", "data": {"turn": {"actorId": "b"}}}))
    ll.state_tracker.update_combat.assert_awaited_once_with(in_combat=True, turn=4, turn_order=["a", "b", "c"])
    ll.state_tracker.state.combat.turn_order = None
    run(ll._handle_combat_event({"type": "encounter-turn"}))
    assert ll.state_tracker.update_combat.await_args.kwargs["turn_order"] == []

    ll.state_tracker.set_combat_mode = AsyncMock(side_effect=RuntimeError("x"))
    run(ll._handle_combat_event({"type": "encounter-started"}))        # no raise


def test_scene_event_sets_scene_clears_stale_data_and_refreshes_players():
    awareness = MagicMock()
    awareness.on_scene_change = AsyncMock()
    ll = make(scene_awareness=awareness)
    ll.state_tracker.set_scene = AsyncMock()
    ll.state_tracker.clear_stale_scene_data = AsyncMock(return_value=True)
    ll._update_player_actors = AsyncMock()
    ll._update_gm_users = AsyncMock()
    run(ll._handle_scene_event({"data": {"name": "Crypt"}}))
    ll.state_tracker.set_scene.assert_awaited_once_with("Crypt")
    awareness.on_scene_change.assert_awaited_once_with("Crypt")
    ll._update_player_actors.assert_awaited_once()
    ll._update_gm_users.assert_awaited_once()
    run(ll._handle_scene_event({"sceneName": "Hall", "data": "garbage"}))
    ll.state_tracker.set_scene.assert_awaited_with("Hall")


def test_scene_event_placeable_edits_are_player_activity_not_scene_changes():
    ll = make()
    ll.state_tracker.set_scene = AsyncMock()
    ll._note_player_activity = MagicMock()
    run(ll._handle_scene_event({"data": {"data": {"eventType": "token-update", "name": "Goblin"}}}))
    ll.state_tracker.set_scene.assert_not_awaited()
    ll._note_player_activity.assert_called_once()
    run(ll._handle_scene_event({"data": {}}))                           # nameless: nothing happens
    ll.state_tracker.set_scene.assert_not_awaited()
    ll.state_tracker.set_scene.side_effect = RuntimeError("x")
    run(ll._handle_scene_event({"name": "Crypt"}))                      # swallowed


def test_pause_hook_suspends_and_resumes_the_ai():
    ll = make()
    ll._running = True
    ll._reset_idle_timer = MagicMock()
    run(ll._handle_hook_event({"hook": "pauseGame", "data": {"paused": True}}))
    assert ll._running is False
    run(ll._handle_hook_event({"hook": "pauseGame", "data": {"paused": False}}))
    assert ll._running is True
    ll._reset_idle_timer.assert_called_once()
    run(ll._handle_hook_event({"hook": "pauseGame"}))                   # default paused=True
    assert ll._running is False
    run(ll._handle_hook_event({"hook": "other"}))
    assert ll._running is False


# ------------------------------------------------------- turn context

def test_turn_context_assembles_blocks_within_budget(monkeypatch):
    ll = make()
    ll.llm.conversation_history = [{"content": "earlier"}, "junk"]
    ll._build_location_context = AsyncMock(side_effect=lambda c: [Block(1, "location", "LOC")])
    ll._get_npc_context = AsyncMock(return_value=[Block(1, "characters", "CHARS")])
    ll._memory_context = AsyncMock(return_value="MEM")
    ll._lore_context = AsyncMock(return_value="LORE")
    out = run(ll._turn_context("query"))
    ll._build_location_context.assert_awaited_once_with("query\nearlier")
    for part in ("LOC", "CHARS", "MEM", "LORE"):
        assert part in out
    ll._memory_context.reset_mock()
    out = run(ll._turn_context(""))                                     # proactive beat: no recall
    ll._memory_context.assert_not_awaited()
    assert "MEM" not in out and "LORE" not in out


def test_lore_context_formats_hits_and_degrades_quietly():
    ll = make()
    assert run(ll._lore_context("q")) == ""
    rag = MagicMock()
    rag.inject_lore = AsyncMock(return_value=[
        SimpleNamespace(text="Salt repels dragons", source="Lore.md", also_in=["A.md", "B.md"]),
        SimpleNamespace(text="The king is dead", source="Canon.md", also_in=[]),
    ])
    ll = make(semantic_rag=rag)
    out = run(ll._lore_context("dragons"))
    rag.inject_lore.assert_awaited_once_with("dragons", top_k=3)
    assert "- Salt repels dragons (source: Lore.md; also in A.md, B.md)" in out
    assert "- The king is dead (source: Canon.md)" in out
    rag.inject_lore = AsyncMock(return_value=[])
    assert run(ll._lore_context("q")) == ""
    rag.inject_lore = AsyncMock(side_effect=RuntimeError("x"))
    assert run(ll._lore_context("q")) == ""


def test_location_context_requires_scene_and_survives_loader_and_catalog_failures():
    ll = make()
    ll.state_tracker.state.current_scene = ""
    assert run(ll._build_location_context()) == []

    loader = MagicMock()
    loader.get_scene_briefing = MagicMock(side_effect=RuntimeError("x"))
    loader.campaign_scenes = []
    ll = make(campaign_loader=loader)
    ll.state_tracker.state.current_scene = "Crypt"
    ll.foundry.list_scene_names = AsyncMock(side_effect=RuntimeError("relay"))
    blocks = run(ll._build_location_context("talk"))
    assert [b.label for b in blocks] == ["location"]
    assert "Crypt" in blocks[0].text and "authored description" not in blocks[0].text

    loader.get_scene_briefing = MagicMock(return_value="Damp and dark.")
    blocks = run(ll._build_location_context("talk"))
    assert "Damp and dark." in blocks[0].text


def _actor(name, uuid="Actor.1", hp=5, mx=9):
    return {"name": name, "uuid": uuid, "hp": hp, "max_hp": mx}


def test_npc_context_lists_party_and_tokens_with_registry_personality():
    registry = MagicMock()
    registry.get_npc_by_name = MagicMock(return_value=SimpleNamespace(npc_id="n1"))
    registry.get_npc_context = MagicMock(return_value="Gruff and suspicious. " * 20)
    ll = make(npc_registry=registry)
    ll.state_tracker.state.player_actors = {"Aria": "u1"}
    ll.state_tracker.state.current_scene = "Crypt"
    ll.state_tracker.get_encounter_context = MagicMock(return_value="")
    ll.foundry.get_actors = AsyncMock(return_value=[_actor("Aria"), _actor("Goblin", "Actor.2")])
    ll.foundry.get_scene_tokens = AsyncMock(return_value=[
        {"id": "t1", "name": "Goblin", "disposition": -1, "x": 100.5, "y": 200},
    ])
    ll.foundry.get_scene_details = AsyncMock(return_value={"data": {"flags": {"ai-gm": {"atmosphere": "dusty"}}}})
    ll._ambient_manager = MagicMock()
    ll._ambient_manager.get_atmosphere_description = MagicMock(return_value="Noon, clear")
    blocks = run(ll._get_npc_context("the Goblin"))
    chars = next(b for b in blocks if b.label == "characters").text
    assert "- Goblin [uuid: Actor.2] (HP: 5/9) [token_id: t1] hostile at (100, 200)" in chars
    assert "- Aria [uuid: Actor.1] (HP: 5/9) — not on this map" in chars
    assert "  Gruff and suspicious" in chars and "..." in chars
    texts = [b.text for b in blocks]
    assert "Scene mood: dusty" in texts
    assert not any("Atmosphere:" in t for t in texts)          # authored mood suppresses the generic sim


def test_npc_context_falls_back_to_loader_encounters_and_ambient_atmosphere():
    loader = MagicMock()
    loader.get_encounter_context_for_scene = MagicMock(return_value="2 goblins")
    ll = make(campaign_loader=loader)
    ll.state_tracker.state.player_actors = {}
    ll.state_tracker.state.current_scene = "Crypt"
    ll.state_tracker.get_encounter_context = MagicMock(return_value="")
    ll.foundry.get_actors = AsyncMock(side_effect=RuntimeError("relay"))
    ll.foundry.get_scene_tokens = AsyncMock(side_effect=RuntimeError("relay"))
    ll.foundry.get_scene_details = AsyncMock(side_effect=RuntimeError("relay"))
    ll._ambient_manager = MagicMock()
    ll._ambient_manager.get_atmosphere_description = MagicMock(return_value="Dusk")
    blocks = run(ll._get_npc_context(""))
    loader.get_encounter_context_for_scene.assert_called_once_with("Crypt")
    assert [b.text for b in blocks] == ["2 goblins", "Atmosphere: Dusk"]
    ll._ambient_manager.get_atmosphere_description = MagicMock(side_effect=RuntimeError("x"))
    run(ll._get_npc_context(""))                                          # swallowed


# --------------------------------------------- failure retry + redelivery

def test_failed_start_encounter_suppresses_retry_and_pins_history():
    ll = make()
    ll.llm._conversation_history = []
    ll.llm.generate = AsyncMock()
    failed = [{"type": "start_encounter", "error": "no tokens", "success": False},
              {"type": "place_token", "error": "no actor", "success": False}]
    assert run(ll._notify_llm_of_failures(failed)) == []
    ll.llm.generate.assert_not_awaited()
    system, canned = ll.llm._conversation_history
    assert "COMBAT DID NOT START" in system["content"] and "generate_encounter" in system["content"]
    assert canned["role"] == "assistant"
    ll.llm._conversation_history = None                                    # history unavailable: swallowed
    assert run(ll._notify_llm_of_failures(failed)) == []
    assert run(ll._notify_llm_of_failures([{"type": "x", "success": True}, {"type": "y", "success": False}])) == []


def test_retry_stamps_source_and_skips_narration_already_played():
    ll = make()
    ll.llm.generate = AsyncMock(return_value={"actions": [
        {"type": "narrate", "text": "Already heard"},
        {"type": "update_hp", "amount": 2},
    ]})
    ll.dispatcher.execute_batch = AsyncMock(return_value=[{"type": "update_hp", "success": True}])
    ll._record_action_resolved_events = AsyncMock()
    run(ll._record_sent("Already heard"))
    res = run(ll._notify_llm_of_failures([{"type": "update_hp", "error": "bad", "success": False}], source="player_turn"))
    sent = ll.dispatcher.execute_batch.await_args.args[0]
    assert sent == [{"type": "update_hp", "amount": 2, "source": "player_turn"}]
    assert res == [{"type": "update_hp", "success": True}]
    ll._record_action_resolved_events.assert_awaited_once_with(res, trigger_npcs=False)

    ll.llm.generate = AsyncMock(side_effect=RuntimeError("llm"))
    assert run(ll._notify_llm_of_failures([{"type": "x", "error": "bad", "success": False}])) == []
    ll.llm.generate = AsyncMock(return_value={"actions": [{"type": "narrate", "text": "Already heard"}]})
    assert run(ll._notify_llm_of_failures([{"type": "x", "error": "bad", "success": False}])) == []


def test_drop_redelivered_keeps_failed_scene_ops_but_strips_their_played_narration():
    ll = make()
    run(ll._record_sent("Heard speech"))
    run(ll._record_sent("Heard scene text"))
    kept = run(ll._drop_redelivered([
        {"type": "speak", "text": "Heard speech"},
        {"type": "setup_scene", "narrate": "Heard scene text", "darkness": 1},
        {"type": "narrate", "text": "Fresh"},
        {"type": "roll"},
    ]))
    assert kept == [{"type": "setup_scene", "darkness": 1}, {"type": "narrate", "text": "Fresh"}, {"type": "roll"}]


# -------------------------------------------------- token reconciliation

def _placer(actors, tokens, disp):
    ll = make()
    ll.foundry.get_actors = AsyncMock(return_value=actors)
    ll.foundry.get_scene_tokens = AsyncMock(return_value=tokens)
    ll.foundry.get_actor_dispositions = AsyncMock(return_value=disp)
    ll.foundry.place_token = AsyncMock()
    ll.register_ai_speaker = AsyncMock()
    return ll


def test_place_combatants_for_rolled_and_quantified_hostiles_near_the_party():
    ll = _placer(
        [{"name": "Goblin"}, {"name": "Skeleton"}, {"name": "Bartender"}, {"name": ""}],
        [{"name": "Aria", "disposition": 1, "x": 1000, "y": 1000}, {"name": "Goblin 1", "disposition": -1, "x": 5, "y": 5}],
        {"Goblin": -1, "Skeleton": -1, "Bartender": 1},
    )
    run(ll._place_referenced_combatants([
        {"type": "roll", "speaker": "Goblin"},
        {"type": "narrate", "text": "Three Skeletons rise while the Bartender hides."},
    ]))
    placed = [(c.args[0], c.kwargs) for c in ll.foundry.place_token.await_args_list]
    names = [p[0] for p in placed]
    # Goblin already has a token (needs 0 more); 3 skeletons placed; the ally named in passing is not
    assert names.count("Skeleton") == 3 and "Goblin" not in names and "Bartender" not in names
    assert all(k == {"disposition": -1} for _, k in placed)
    first = ll.foundry.place_token.await_args_list[0].args
    assert first[1] > 1000 and first[2] == 1000                           # ring around the party anchor (+200 x offset)
    assert {c.args[0] for c in ll.register_ai_speaker.await_args_list} == {"Skeleton", "Goblin"}


def test_place_combatants_noop_cases_and_caps():
    ll = _placer([{"name": "Goblin"}], [], {})
    run(ll._place_referenced_combatants([{"type": "update_hp"}]))          # nothing to reconcile
    ll.foundry.get_actors.assert_not_awaited()
    run(ll._place_referenced_combatants([{"type": "narrate", "text": "Goblins lurk"}]))
    ll.foundry.place_token.assert_not_awaited()                              # bare mention: no quantity word

    ll = _placer([{"name": "Goblin"}], [], {"Goblin": -1})
    ll.foundry.get_scene_tokens = AsyncMock(side_effect=RuntimeError("relay"))
    run(ll._place_referenced_combatants([{"type": "roll", "speaker": "Goblin"}]))
    ll.foundry.place_token.assert_not_awaited()

    ll = _placer([{"name": "Rat"}], [], {"Rat": -1})
    run(ll._place_referenced_combatants([{"type": "narrate", "text": "A dozen Rats swarm"}]))
    assert ll.foundry.place_token.await_count == 6                           # per-actor cap of 6
    ll = _placer([{"name": "Rat"}, {"name": "Bat"}], [], {"Rat": -1, "Bat": -1})
    run(ll._place_referenced_combatants([{"type": "narrate", "text": "Six Rats and six Bats attack"}]))
    assert ll.foundry.place_token.await_count == 10                          # global per-turn cap
    ll.foundry.place_token = AsyncMock(side_effect=RuntimeError("full"))
    run(ll._place_referenced_combatants([{"type": "roll", "speaker": "Rat"}]))   # placement failure swallowed


# ---------------------------------------------------- generated content

def test_generated_npc_registered_with_personality_and_other_results_logged():
    registry = MagicMock()
    engine = MagicMock()
    engine.parse_npc_description = MagicMock(return_value=SimpleNamespace(traits=["gruff"]))
    ll = make(npc_registry=registry, personality_engine=engine)
    run(ll._handle_generated_npcs([
        "not a dict",
        {"type": "generate_npc", "npc": {"name": "Bram", "description": "A smith", "class": "Fighter",
                                          "race": "Dwarf", "level": 3, "alignment": "LG"}},
        {"type": "generate_encounter", "encounter": {"name": "Ambush"}},
        {"type": "generate_treasure", "treasure": {"total_value_gp": 5}},
        {"type": "generate_quest", "quest": {"title": "Q"}},
    ]))
    kw = registry.register_npc.call_args.kwargs
    assert kw["npc_id"] == "Bram" and kw["class_name"] == "Fighter" and kw["level"] == 3
    assert kw["description"] == "A smith (Fighter Dwarf)"
    registry.set_npc_personality.assert_called_once_with("Bram", ["gruff"])

    registry.register_npc.side_effect = RuntimeError("dup")
    run(ll._handle_generated_npcs([{"type": "generate_npc", "npc": {"name": "X"}}]))   # swallowed
    # without a registry/engine nothing is attempted
    run(make()._handle_generated_npcs([{"type": "generate_npc", "npc": {"name": "X"}}]))


def test_update_immersion_state_ignores_failures_and_runs_with_managers(caplog):
    ll = make()
    with caplog.at_level("INFO"):
        run(ll._update_immersion_state([{"type": "set_weather", "success": True}]))   # no managers: no-op
    assert "[Tier 6]" not in caplog.text
    ll._ambient_manager = MagicMock()
    ll._effects_manager = MagicMock()
    ll._vision_manager = MagicMock()
    with caplog.at_level("INFO"):
        run(ll._update_immersion_state([
            "junk", {"type": "set_weather", "success": False},
            {"type": "set_weather", "success": True}, {"type": "set_time", "success": True},
            {"type": "apply_token_effect", "success": True, "effect_name": "burn"},
            {"type": "update_vision", "success": True, "token_id": "t1"},
        ]))
    tier6 = [r.getMessage() for r in caplog.records if "[Tier 6]" in r.getMessage()]
    assert len(tier6) == 4  # junk and the failed result are skipped
    assert any("Token effect applied: burn" in m for m in tier6)
    assert any("Vision updated for token: t1" in m for m in tier6)


# ---------------------------------------------------- event log + NPCs

def test_action_resolved_events_carry_audit_and_trigger_npcs_only_when_asked():
    ll = make()
    ll._event_store = MagicMock()
    ll._event_store.append = AsyncMock()
    ll._maybe_trigger_npc_agents = AsyncMock()
    results = [{"type": "update_hp", "success": False, "error": "no", "_audit": {"consequential": True, "params": "p"}},
               {"type": "roll"}]
    run(ll._record_action_resolved_events(results))
    first = ll._event_store.append.await_args_list[0]
    assert first.args[:3] == ("sess-1", "Camp", cl.ACTION_RESOLVED)
    assert first.kwargs["payload"] == {"action_type": "update_hp", "success": False, "error": "no",
                                       "consequential": True, "params": "p"}
    assert ll._event_store.append.await_args_list[1].kwargs["payload"]["success"] is True
    assert ll._maybe_trigger_npc_agents.await_count == 2
    ll._maybe_trigger_npc_agents.reset_mock()
    run(ll._record_action_resolved_events(results, trigger_npcs=False))
    ll._maybe_trigger_npc_agents.assert_not_awaited()
    ll.db.get_active_session_info = AsyncMock(return_value=None)
    ll._event_store.append.reset_mock()
    run(ll._record_action_resolved_events(results))
    ll._event_store.append.assert_not_awaited()


def test_npc_agent_acts_for_the_chosen_candidate_only(monkeypatch):
    goal_hit = SimpleNamespace(status="pending", matches=lambda e: True)
    goal_miss = SimpleNamespace(status="pending", matches=lambda e: False)
    goal_done = SimpleNamespace(status="done", matches=lambda e: True)
    npc = SimpleNamespace(npc_name="Bram", goals=[goal_hit, goal_miss, goal_done])
    registry = MagicMock()
    registry.list_npcs = MagicMock(return_value=[npc])
    ll = make(npc_registry=registry)
    ll._scene_director = MagicMock()
    ll._scene_director.next_turn = lambda cands: cands[0] if cands else None
    agent = MagicMock()
    ruling = SimpleNamespace(action={"type": "speak", "text": "hi"})
    agent.act = AsyncMock(return_value=[ruling])
    monkeypatch.setattr(cl, "NPCAgent", MagicMock(return_value=agent))
    ll.dispatcher.execute_batch = AsyncMock(return_value=[{"type": "speak", "success": True}])
    ll._record_action_resolved_events = AsyncMock()
    run(ll._maybe_trigger_npc_agents("sess-1", "Camp", {"type": "x"}))
    assert goal_hit.status == "done" and goal_miss.status == "pending" and goal_done.status == "done"
    ll.dispatcher.execute_batch.assert_awaited_once_with([{"type": "speak", "text": "hi"}])
    ll._record_action_resolved_events.assert_awaited_once_with([{"type": "speak", "success": True}], trigger_npcs=False)

    # an NPC that produced no rulings leaves its goal active (not done)
    goal_hit.status = "pending"
    agent.act = AsyncMock(return_value=[])
    run(ll._maybe_trigger_npc_agents("sess-1", "Camp", {"type": "x"}))
    assert goal_hit.status == "active"
    # nobody matching, and no registry, are no-ops
    npc.goals = []
    run(ll._maybe_trigger_npc_agents("sess-1", "Camp", {"type": "x"}))
    run(make()._maybe_trigger_npc_agents("s", "c", {"type": "x"}))


def test_npc_chat_replies_remembers_and_handles_failures(monkeypatch):
    registry = MagicMock()
    ll = make(npc_registry=registry)
    npc = SimpleNamespace(npc_id="n1", npc_name="Bram")
    monkeypatch.setattr(cl, "parse_npc_chat", lambda reg, text: (npc, "hello"))
    ll._npc_chat = MagicMock()
    ll._npc_chat.reply = AsyncMock(return_value="Well met")
    ll._dispatch_narration_now = AsyncMock()
    ll._event_store = MagicMock()
    ll._event_store.append = AsyncMock()
    run(ll._handle_npc_chat("Aria", "/npc Bram: hello"))
    ll._npc_chat.reply.assert_awaited_once_with("Camp", npc, "Aria", "hello")
    ll._dispatch_narration_now.assert_awaited_once_with({"type": "speak", "npc_name": "Bram", "text": "Well met"})
    ev = ll._event_store.append.await_args
    assert ev.args[:3] == ("sess-1", "Camp", cl.NPC_CONVERSED)
    assert ev.kwargs["payload"] == {"npc_id": "n1", "speaker": "Aria", "said": "hello", "replied": "Well met"}

    ll._npc_chat.reply = AsyncMock(side_effect=RuntimeError("llm"))
    run(ll._handle_npc_chat("Aria", "/npc Bram: hello"))
    assert said(ll)[-1] == "*Bram says nothing.*"
    ll._npc_chat.reply = AsyncMock(side_effect=cl.TokenBudgetExceeded("session", 1, 1, 1))
    n = len(said(ll))
    run(ll._handle_npc_chat("Aria", "/npc Bram: hello"))
    assert len(said(ll)) == n                                            # budget notice already sent elsewhere

    monkeypatch.setattr(cl, "parse_npc_chat", lambda reg, text: (None, ""))
    run(ll._handle_npc_chat("Aria", "/npc Nobody: hi"))
    assert said(ll)[-1].startswith("No one by that name answers")

    ll._degraded_mode_active = True
    ll._is_budget_available = AsyncMock(return_value=False)
    n = len(said(ll))
    run(ll._handle_npc_chat("Aria", "/npc Bram: hi"))
    assert len(said(ll)) == n


def test_remember_conversation_guards_and_swallows_store_errors():
    ll = make()
    ll._event_store = MagicMock()
    ll._event_store.append = AsyncMock()
    npc = SimpleNamespace(npc_id="n1", npc_name="Bram")
    run(ll._remember_conversation(None, npc, "A", "m", "r"))
    run(ll._remember_conversation({"campaign": "C"}, npc, "A", "m", "r"))
    ll._event_store.append.assert_not_awaited()
    ll._event_store.append.side_effect = RuntimeError("db")
    run(ll._remember_conversation({"session_id": "s"}, npc, "A", "m", "r"))     # swallowed
    ll._event_store.append.side_effect = None
    run(ll._remember_conversation({"session_id": "s"}, npc, "A", "m" * 400, "r"))
    assert len(ll._event_store.append.await_args.kwargs["description"]) == 300


def test_session_started_text_mentions_npc_command_only_with_a_registry():
    assert "/npc" not in make()._session_started_text("Camp")
    assert "/npc <name>" in make(npc_registry=MagicMock())._session_started_text("Camp")
