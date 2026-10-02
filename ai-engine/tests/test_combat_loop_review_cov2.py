"""CombatLoop NPC turns, degraded mode, PC waiting, reactions, death/legendary/lair, and wrap-up."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from combat.loop import CombatLoop
from config import settings
from llm.usage import TokenBudgetExceeded


def _tok(tid, name, hp=10, disp=-1, uuid="", **kw):
    return {"id": tid, "name": name, "disposition": disp, "actorUuid": uuid, "hp": hp, **kw}


def _loop(npc_registry=None):
    foundry = AsyncMock()
    foundry.execute_js.return_value = {"result": None}
    foundry.get_multiattack_count.return_value = {"count": 2}
    llm = MagicMock()
    llm.generate = AsyncMock(return_value={"actions": []})
    llm.remember_combat = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.execute_batch = AsyncMock(return_value=[])
    state = AsyncMock()
    state.get_snapshot = MagicMock(return_value="SNAP")
    state.clear_combat_snapshot = MagicMock()
    loop = CombatLoop(foundry=foundry, llm=llm, dispatcher=dispatcher, state_tracker=state,
                      db=AsyncMock(), npc_registry=npc_registry)
    return loop


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 0)
    monkeypatch.setattr(settings, "llm_combat_timeout", 5)

    async def no_tactics(foundry, token_id):
        return "\n## TACTICAL\nflank"
    monkeypatch.setattr("combat.tactics.build_tactical_snapshot", no_tactics)


# ── _process_npc_turn ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_npc_turn_executes_llm_actions_capped_by_multiattack_and_logs_them():
    loop = _loop()
    gob = _tok("n1", "Gob", uuid="Actor.n1", x=3, y=4)
    loop._npc_tokens, loop._pc_tokens = [gob], [_tok("p1", "Pc", disp=1)]
    loop._turn_order = ["n1", "p1"]
    atk = lambda i: {"type": "attack_with_item", "attacker_uuid": "Actor.n1", "item_name": "Bite",  # noqa: E731
                     "target_token_id": "p1", "i": i}
    loop.llm.generate.return_value = {"actions": [atk(1), atk(2), atk(3), {"type": "narrate"}]}
    loop.dispatcher.execute_batch.return_value = [{"success": True, "damage": 5}, {"success": True, "hit": False},
                                                  {"success": True}]
    done = []

    async def on_complete(ev):
        done.append(ev)
    loop.set_turn_complete_callback(on_complete)
    await loop._process_npc_turn(gob)

    sent = loop.dispatcher.execute_batch.await_args.args[0]
    assert [a.get("i") for a in sent if a["type"] == "attack_with_item"] == [1, 2]  # multiattack count == 2
    assert loop._attacks_used_this_turn["Actor.n1"] == 2
    assert list(loop._round_log)[0] == "R1: Gob — attack_with_item → p1 for 5 damage"
    assert list(loop._round_log)[1].endswith("and missed")
    kw = loop.llm.generate.await_args.kwargs
    assert kw["include_history"] is False and kw["persist_history"] is False
    assert "Gob's turn" in kw["user_message"]
    ctx = kw["extra_context"]
    assert "## TACTICAL" in ctx and "You can make 2 attack(s) per turn" in ctx and "x: 3, y: 4" in ctx
    assert done[0]["type"] == "turn_complete" and done[0]["actor"] == "Gob"


@pytest.mark.asyncio
async def test_npc_turn_context_includes_items_slots_personality_and_modules():
    from types import SimpleNamespace
    reg = MagicMock()
    reg.get_npc_by_name.return_value = SimpleNamespace(
        description="D" * 500, personality={"Voice": ["gruff", "loud"], "Empty": []}, alignment="CE")
    loop = _loop(npc_registry=reg)
    loop._active_modules = {"midi-qol": {}}
    loop._has_midi_qol = True
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._npc_tokens = [gob]
    loop._turn_order = ["n1"]
    loop.foundry.execute_js.side_effect = [
        {"result": None},  # legendary reset
        {"result": ["Bite", "Claw"]},  # attack items
        {"result": {"1": {"value": 2, "max": 3}, "pact": {"value": 1, "max": 1}}},  # slots
    ]
    loop.llm.generate.return_value = {"actions": [{"type": "narrate"}]}
    loop.dispatcher.execute_batch.return_value = [{}]
    await loop._process_npc_turn(gob)
    ctx = loop.llm.generate.await_args.kwargs["extra_context"]
    assert "## YOUR ATTACK ITEMS\nBite, Claw" in ctx
    assert "Level 1: 2/3, Pact: 1/1" in ctx
    assert "Background: " + "D" * 400 in ctx and "D" * 401 not in ctx
    assert "Personality: Voice: gruff, loud" in ctx and "Empty" not in ctx
    assert "Alignment: CE" in ctx
    assert "MIDI QOL" in ctx


@pytest.mark.asyncio
async def test_npc_personality_string_traits_and_registry_failure():
    from types import SimpleNamespace
    reg = MagicMock()
    reg.get_npc_by_name.return_value = SimpleNamespace(description="", personality="cunning", alignment="")
    loop = _loop(npc_registry=reg)
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._npc_tokens, loop._turn_order = [gob], ["n1"]
    await loop._process_npc_turn(gob)
    assert "Personality: cunning" in loop.llm.generate.await_args.kwargs["extra_context"]

    reg.get_npc_by_name.side_effect = RuntimeError("db")
    await loop._process_npc_turn(gob)  # must still reach the LLM
    assert loop.llm.generate.await_count == 2


@pytest.mark.asyncio
async def test_npc_turn_survives_foundry_lookup_failures():
    loop = _loop()
    loop._has_dae = True
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._npc_tokens, loop._turn_order = [gob], ["n1"]
    loop.foundry.execute_js.side_effect = RuntimeError("relay")
    loop.foundry.get_multiattack_count.side_effect = RuntimeError("relay")
    loop.llm.generate.return_value = {"actions": [{"type": "narrate"}]}
    loop.dispatcher.execute_batch.return_value = [{}]

    async def broken(foundry, tid):
        raise RuntimeError("geometry")
    import combat.tactics as tactics
    orig = tactics.build_tactical_snapshot
    tactics.build_tactical_snapshot = broken
    try:
        await loop._process_npc_turn(gob)
    finally:
        tactics.build_tactical_snapshot = orig
    loop.dispatcher.execute_batch.assert_awaited_once()
    ctx = loop.llm.generate.await_args.kwargs["extra_context"]
    assert "## YOUR ATTACK ITEMS" not in ctx and "## MULTIATTACK" not in ctx


@pytest.mark.asyncio
async def test_npc_with_no_actions_hesitates_and_runs_nothing():
    loop = _loop()
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._npc_tokens, loop._turn_order = [gob], ["n1"]
    loop.llm.generate.return_value = {"actions": []}
    await loop._process_npc_turn(gob)
    assert "Gob hesitates" in loop.foundry.chat_message.await_args.args[0]
    loop.dispatcher.execute_batch.assert_not_called()


@pytest.mark.asyncio
async def test_llm_timeout_falls_back_to_a_real_attack_on_the_nearest_pc():
    loop = _loop()
    gob = _tok("n1", "Gob", uuid="Actor.n1", x=0, y=0)
    loop._npc_tokens = [gob]
    loop._pc_tokens = [_tok("far", "Far", disp=1, x=50, y=50), _tok("near", "Near", disp=1, x=1, y=1)]
    loop._turn_order = ["n1", "far", "near"]
    loop.llm.generate.side_effect = asyncio.TimeoutError()
    loop.foundry.execute_js.side_effect = [{"result": None}, {"result": ["Bite"]}, {"result": None}, {"result": ["Bite"]}]
    done = []

    async def on_complete(ev):
        done.append(ev)
    loop.set_turn_complete_callback(on_complete)
    await loop._process_npc_turn(gob)
    loop.dispatcher.execute_batch.assert_awaited_once_with([{
        "type": "attack_with_item", "attacker_uuid": "Actor.n1", "item_name": "Bite", "target_token_id": "near"}])
    assert done[0]["actions"][0]["target_token_id"] == "near"


@pytest.mark.asyncio
async def test_budget_exhaustion_switches_to_degraded_mode_and_resolves_mechanically():
    loop = _loop()
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._npc_tokens, loop._pc_tokens, loop._turn_order = [gob], [_tok("p", "P", disp=1)], ["n1", "p"]
    loop.llm.generate.side_effect = TokenBudgetExceeded("session", 10, 5, 12)
    await loop._process_npc_turn(gob)
    assert loop._degraded_mode is True
    assert any("narration is paused" in c.args[0] for c in loop.foundry.chat_message.await_args_list)


@pytest.mark.asyncio
async def test_unexpected_error_posts_generic_message_and_reports_the_error():
    loop = _loop()
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._npc_tokens, loop._turn_order = [gob], ["n1"]
    loop.llm.generate.side_effect = ValueError("secret detail")
    done = []

    async def on_complete(ev):
        done.append(ev)
    loop.set_turn_complete_callback(on_complete)
    await loop._process_npc_turn(gob)
    msg = loop.foundry.chat_message.await_args.args[0]
    assert "Gob hesitates" in msg and "secret detail" not in msg  # detail only in logs
    assert done[0]["actions"][0]["error"] == "secret detail"


@pytest.mark.asyncio
async def test_zero_combat_timeout_means_no_timeout(monkeypatch):
    monkeypatch.setattr(settings, "llm_combat_timeout", 0)
    seen = {}
    real = asyncio.wait_for

    async def spy(aw, timeout):
        seen["t"] = timeout
        return await real(aw, timeout)
    monkeypatch.setattr("combat.loop.asyncio.wait_for", spy)
    loop = _loop()
    gob = _tok("n1", "Gob")
    loop._npc_tokens, loop._turn_order = [gob], ["n1"]
    await loop._process_npc_turn(gob)
    assert seen["t"] is None


# ── degraded mode ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_degraded_npc_turn_never_calls_the_llm_while_budget_is_out():
    loop = _loop()
    loop._degraded_mode = True
    loop._is_budget_available = AsyncMock(return_value=False)
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._npc_tokens, loop._pc_tokens = [gob], [_tok("p", "P", disp=1, x=2, y=2)]
    loop.foundry.execute_js.return_value = {"result": ["Slam"]}
    done = []

    async def on_complete(ev):
        done.append(ev)
    loop.set_turn_complete_callback(on_complete)
    loop.dispatcher.execute_batch.return_value = [{"success": True}]
    await loop._process_npc_turn(gob)
    loop.llm.generate.assert_not_called()
    assert loop.dispatcher.execute_batch.await_args.args[0][0]["item_name"] == "Slam"
    assert done[0]["degraded"] is True and done[0]["actions"] == [{"success": True}]


@pytest.mark.asyncio
async def test_degraded_mode_ends_when_budget_returns_and_turn_uses_the_llm():
    loop = _loop()
    loop._degraded_mode = True
    loop._is_budget_available = AsyncMock(return_value=True)
    gob = _tok("n1", "Gob")
    loop._npc_tokens, loop._turn_order = [gob], ["n1"]
    await loop._process_npc_turn(gob)
    assert loop._degraded_mode is False
    assert any("narration is restored" in c.args[0] for c in loop.foundry.chat_message.await_args_list)
    loop.llm.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_degraded_turn_with_no_attack_items_holds_position_silently():
    loop = _loop()
    gob = _tok("n1", "Gob", uuid="Actor.n1")
    loop._pc_tokens = [_tok("p", "P", disp=1)]
    loop.foundry.execute_js.return_value = {"result": []}
    done = []

    async def on_complete(ev):
        done.append(ev)
    loop.set_turn_complete_callback(on_complete)
    await loop._execute_degraded_npc_turn(gob)
    loop.dispatcher.execute_batch.assert_not_called()
    assert done[0]["actions"] == []
    loop.foundry.chat_message.assert_not_called()  # no invented narration


@pytest.mark.asyncio
async def test_deterministic_actions_edge_cases():
    loop = _loop()
    loop._pc_tokens = [_tok("p", "P", disp=1)]
    assert await loop._deterministic_npc_actions(_tok("n", "N", uuid="")) == []  # no uuid
    loop._pc_tokens = []
    assert await loop._deterministic_npc_actions(_tok("n", "N", uuid="A")) == []  # nobody to hit
    loop._pc_tokens = [_tok("p", "P", disp=1)]
    loop.foundry.execute_js.side_effect = RuntimeError("relay")
    assert await loop._deterministic_npc_actions(_tok("n", "N", uuid="A")) == []  # unreadable items
    loop.foundry.execute_js.side_effect = None
    loop.foundry.execute_js.return_value = {"result": "nope"}
    assert await loop._deterministic_npc_actions(_tok("n", "N", uuid="A")) == []


@pytest.mark.asyncio
async def test_is_budget_available_reads_session_usage():
    loop = _loop()
    loop.llm._usage = None
    assert await loop._is_budget_available() is True  # nothing tracked: never blocks combat
    usage = MagicMock()
    usage.budget_available = AsyncMock(return_value=False)
    loop._token_usage = usage
    loop.db.get_active_session.return_value = "sess-1"
    assert await loop._is_budget_available() is False
    usage.budget_available.assert_awaited_once_with("sess-1")


@pytest.mark.asyncio
async def test_degraded_announcements_are_failure_safe_and_idempotent():
    loop = _loop()
    loop.foundry.chat_message.side_effect = RuntimeError("x")
    await loop.enter_degraded_mode("e")
    assert loop._degraded_mode is True
    await loop._leave_degraded_mode()
    assert loop._degraded_mode is False
    loop.foundry.chat_message.reset_mock(side_effect=True)
    await loop.enter_degraded_mode()
    await loop.enter_degraded_mode()
    assert loop.foundry.chat_message.await_count == 1  # second call is a no-op


# ── PC waiting / turn gap / reaction window ─────────────────────────────────

@pytest.mark.asyncio
async def test_pc_wait_sets_awaiting_pc_and_returns_on_advance(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 8.0)
    loop = _loop()
    pc = _tok("p", "Thalia", disp=1)
    task = asyncio.create_task(loop._wait_for_pc_input(pc))
    await asyncio.sleep(0.01)
    assert loop.awaiting_pc is pc
    assert "Thalia's turn" in loop.foundry.chat_message.await_args.args[0]
    assert "after 8s of quiet" in loop.foundry.chat_message.await_args.args[0]
    loop.advance_pc_turn()
    await asyncio.wait_for(task, 1)
    assert loop.awaiting_pc is None


@pytest.mark.asyncio
async def test_pc_wait_without_quiet_hint_and_timeout_skips_turn(monkeypatch):
    monkeypatch.setattr(settings, "combat_pc_turn_quiet_seconds", 0)
    monkeypatch.setattr(settings, "pc_turn_timeout", 1)
    loop = _loop()
    await loop._wait_for_pc_input(_tok("p", "Thalia", disp=1))
    first, second = [c.args[0] for c in loop.foundry.chat_message.await_args_list]
    assert "quiet" not in first
    assert "Thalia hesitates" in second and "skipped" in second
    assert loop.awaiting_pc is None


@pytest.mark.asyncio
async def test_a_signal_set_before_the_wait_is_not_lost_only_if_set_during_the_announcement():
    loop = _loop()

    async def player_types_during_announcement(*a, **k):
        loop.advance_pc_turn()
    loop.foundry.chat_message.side_effect = player_types_during_announcement
    await asyncio.wait_for(loop._wait_for_pc_input(_tok("p", "P", disp=1)), 2)  # returns promptly, no 180s wait
    loop.foundry.chat_message.assert_awaited_once()
    assert loop.awaiting_pc is None


@pytest.mark.asyncio
async def test_turn_gap_waits_for_speech_then_pauses(monkeypatch):
    from tts import playback
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    speaking = iter([True, True, False])
    monkeypatch.setattr(playback, "is_speaking", lambda: next(speaking))
    monkeypatch.setattr(settings, "combat_turn_gap_seconds", 3.0)
    loop = _loop()
    loop._running = True
    await loop._turn_gap()
    assert sleeps == [0.5, 0.5, 3.0]


@pytest.mark.asyncio
async def test_turn_gap_gives_up_on_endless_speech_and_skips_when_stopped(monkeypatch):
    from tts import playback
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(playback, "is_speaking", lambda: True)
    monkeypatch.setattr(settings, "combat_turn_gap_seconds", 0)
    loop = _loop()
    loop._running = True
    await loop._turn_gap()
    assert sleeps == [0.5] * 40  # capped at 20s total, and gap 0 adds nothing
    sleeps.clear()
    loop._running = False
    await loop._turn_gap()
    assert sleeps == []


@pytest.mark.asyncio
async def test_reaction_window_announces_targets_and_waits_for_declared_reaction(monkeypatch):
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 6.0)
    loop = _loop()
    loop._pc_tokens = [_tok("p1", "Zed", disp=1), _tok("p2", "Amy", disp=1)]
    states = []

    async def fake_sleep(s):
        states.append(("slept", s, loop.reaction_window_open))
        loop.reaction_declared = True
        loop.reaction_processed.set()
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    actions = [{"type": "attack_with_item", "target_token_id": "p1"},
               {"type": "attack_with_item", "target_token_id": "p2"},
               {"type": "attack_with_item", "target_token_id": "npc"},
               {"type": "narrate"}]
    await loop._reaction_window("Gob", actions)
    msg = loop.foundry.chat_message.await_args.args[0]
    assert "Gob" in msg and "Amy, Zed" in msg and "6s" in msg  # sorted, NPC target excluded
    assert states == [("slept", 6.0, True)]
    assert loop.reaction_window_open is False and loop.reaction_declared is False  # reset after


@pytest.mark.asyncio
async def test_reaction_window_skipped_without_pc_targets_or_when_disabled(monkeypatch):
    loop = _loop()
    loop._pc_tokens = [_tok("p1", "Zed", disp=1)]
    await loop._reaction_window("Gob", [{"type": "attack_with_item", "target_token_id": "p1"}])  # window 0
    loop.foundry.chat_message.assert_not_called()
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 6.0)
    await loop._reaction_window("Gob", [{"type": "attack_with_item", "target_token_id": "ghost"}, {"type": "roll"}])
    loop.foundry.chat_message.assert_not_called()
    assert loop.reaction_window_open is False


@pytest.mark.asyncio
async def test_reaction_never_resolved_does_not_deadlock(monkeypatch):
    monkeypatch.setattr(settings, "combat_reaction_window_seconds", 1.0)
    loop = _loop()
    loop._pc_tokens = [_tok("p1", "Zed", disp=1)]

    async def fake_sleep(s):
        loop.reaction_declared = True
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def timed_out(aw, timeout):
        aw.close() if hasattr(aw, "close") else None
        raise asyncio.TimeoutError
    monkeypatch.setattr("combat.loop.asyncio.wait_for", timed_out)
    await loop._reaction_window("Gob", [{"type": "attack_with_item", "target_token_id": "p1"}])
    assert loop.reaction_window_open is False


# ── death saves / solo setback ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_death_save_decisions(monkeypatch):
    saves = []

    async def fake_save(uuid, foundry=None):
        saves.append(uuid)
    monkeypatch.setattr("combat.loop.execute_death_save", fake_save)
    status = {}

    async def fake_status(uuid, foundry):
        return status.get("v")
    monkeypatch.setattr("combat.loop.get_death_save_status", fake_status)
    loop = _loop()
    tok = _tok("p", "P", uuid="Actor.p", disp=1)

    status["v"] = None
    assert await loop._maybe_death_save(tok) is False
    status["v"] = {"hp": 5}
    assert await loop._maybe_death_save(tok) is False
    status["v"] = {"hp": 0, "isStable": True}
    assert await loop._maybe_death_save(tok) is True and saves == []
    status["v"] = {"hp": 0, "isDead": True}
    loop._pc_tokens = [tok, _tok("q", "Q", disp=1)]  # not solo: no setback
    assert await loop._maybe_death_save(tok) is True and saves == []
    status["v"] = {"hp": 0}
    assert await loop._maybe_death_save(tok) is True
    assert saves == ["Actor.p"]  # dying: save rolled, turn consumed


@pytest.mark.asyncio
async def test_token_without_actor_uuid_skips_turn_only_when_down():
    loop = _loop()
    assert await loop._maybe_death_save(_tok("n", "N", hp=0, uuid="")) is True
    assert await loop._maybe_death_save(_tok("n", "N", hp=4, uuid="")) is False


@pytest.mark.asyncio
async def test_solo_death_becomes_a_recorded_setback(monkeypatch):
    monkeypatch.setattr(settings, "solo_death_setback", None)
    loop = _loop()
    pc = _tok("p", "Thalia", uuid="Actor.p", disp=1)
    loop._pc_tokens = [pc]
    loop.foundry.apply_solo_death_setback.return_value = {"hp": 3, "exhaustion": 1}
    loop.db.get_active_session_info.return_value = {"session_id": "s1", "campaign": "krynn"}
    assert await loop._maybe_apply_solo_death_setback(pc) is True
    args = loop.db.record_typed_event.await_args
    assert args.args[:2] == ("s1", "krynn")
    assert args.kwargs["payload"] == {"actor_uuid": "Actor.p", "actor_name": "Thalia", "consequence": "captured",
                                      "hp_after": 3, "exhaustion": True}
    assert "awakens bound" in loop.foundry.chat_message.await_args.args[0]


@pytest.mark.asyncio
async def test_setback_not_applied_for_party_unlisted_actor_or_disabled(monkeypatch):
    loop = _loop()
    pc = _tok("p", "Thalia", uuid="Actor.p", disp=1)
    loop._pc_tokens = [pc, _tok("q", "Q", uuid="Actor.q", disp=1)]
    monkeypatch.setattr(settings, "solo_death_setback", None)
    assert await loop._maybe_apply_solo_death_setback(pc) is False  # two PCs, auto-off
    monkeypatch.setattr(settings, "solo_death_setback", True)
    assert await loop._maybe_apply_solo_death_setback(_tok("z", "Z", uuid="Actor.z")) is False  # not a PC
    assert await loop._maybe_apply_solo_death_setback(_tok("p", "P", uuid="")) is False
    monkeypatch.setattr(settings, "solo_death_setback", False)
    loop._pc_tokens = [pc]
    assert await loop._maybe_apply_solo_death_setback(pc) is False  # explicit off beats solo auto-on
    loop.foundry.apply_solo_death_setback.assert_not_called()


@pytest.mark.asyncio
async def test_setback_failure_falls_back_to_normal_death(monkeypatch):
    monkeypatch.setattr(settings, "solo_death_setback", True)
    loop = _loop()
    pc = _tok("p", "Thalia", uuid="Actor.p", disp=1)
    loop._pc_tokens = [pc]
    loop.foundry.apply_solo_death_setback.side_effect = RuntimeError("relay")
    assert await loop._maybe_apply_solo_death_setback(pc) is False


@pytest.mark.asyncio
async def test_setback_without_open_session_still_applies(monkeypatch):
    monkeypatch.setattr(settings, "solo_death_setback", True)
    loop = _loop()
    pc = _tok("p", "Thalia", uuid="Actor.p", disp=1)
    loop._pc_tokens = [pc]
    loop.foundry.apply_solo_death_setback.return_value = {}
    loop.db.get_active_session_info.return_value = None
    assert await loop._maybe_apply_solo_death_setback(pc) is True
    loop.db.record_typed_event.assert_not_called()


# ── legendary / lair ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_legendary_skips_self_dead_uuidless_and_resource_failures(monkeypatch):
    from foundry import scripts
    monkeypatch.setattr(scripts, "get_legendary_resource", lambda u: f"GET:{u}")
    loop = _loop()
    acted = _tok("n1", "Acted", uuid="Actor.n1")
    loop._npc_tokens = [acted, _tok("n2", "Dead", hp=0, uuid="Actor.n2"), _tok("n3", "NoUuid", uuid=""),
                        _tok("n4", "Boom", uuid="Actor.n4"), _tok("n5", "Spent", uuid="Actor.n5")]

    async def js(code):
        if code == "GET:Actor.n4":
            raise RuntimeError("relay")
        return {"result": {"value": 0, "max": 3}}
    loop.foundry.execute_js.side_effect = js
    await loop._maybe_legendary_actions(acted)
    queried = [c.args[0] for c in loop.foundry.execute_js.await_args_list]
    assert queried == ["GET:Actor.n4", "GET:Actor.n5"]
    loop.llm.generate.assert_not_called()


@pytest.mark.asyncio
async def test_legendary_llm_failure_spends_nothing_and_extra_mechanical_actions_are_dropped(monkeypatch):
    from foundry import scripts
    monkeypatch.setattr(scripts, "get_legendary_resource", lambda u: "GET")
    monkeypatch.setattr(scripts, "set_legendary_resource", lambda u, v: f"SET:{u}:{v}")
    loop = _loop()
    acted = _tok("n1", "Acted", uuid="Actor.n1")
    dragon = _tok("n2", "Dragon", uuid="Actor.n2")
    loop._npc_tokens, loop._turn_order = [acted, dragon], ["n1", "n2"]
    calls = []

    async def js(code):
        calls.append(code)
        return {"result": {"value": 2, "max": 3}} if code == "GET" else {"result": True}
    loop.foundry.execute_js.side_effect = js

    loop.llm.generate.side_effect = RuntimeError("llm down")
    await loop._maybe_legendary_actions(acted)
    assert not any(c.startswith("SET") for c in calls)

    loop.llm.generate.side_effect = None
    loop.llm.generate.return_value = {"actions": [
        {"type": "narrate", "n": 1}, {"type": "attack_with_item", "n": 2}, {"type": "attack_with_item", "n": 3},
        {"type": "narrate", "n": 4}]}
    await loop._maybe_legendary_actions(acted)
    sent = loop.dispatcher.execute_batch.await_args.args[0]
    assert [a["n"] for a in sent] == [1, 2, 4]  # only the first mechanical action; narration is free
    assert "SET:Actor.n2:1" in calls  # one spent


@pytest.mark.asyncio
async def test_legendary_pass_costs_nothing_and_spend_failure_is_swallowed(monkeypatch):
    from foundry import scripts
    monkeypatch.setattr(scripts, "get_legendary_resource", lambda u: "GET")
    monkeypatch.setattr(scripts, "set_legendary_resource", lambda u, v: "SET")
    loop = _loop()
    acted, dragon = _tok("n1", "A", uuid="Actor.n1"), _tok("n2", "D", uuid="Actor.n2")
    loop._npc_tokens, loop._turn_order = [acted, dragon], ["n1", "n2"]
    sets = []

    async def js(code):
        if code == "SET":
            sets.append(1)
            raise RuntimeError("relay")
        return {"result": {"value": 1}}
    loop.foundry.execute_js.side_effect = js
    loop.llm.generate.return_value = {"actions": [{"type": "narrate"}]}  # pass
    await loop._maybe_legendary_actions(acted)
    assert sets == []
    loop.llm.generate.return_value = "not a dict"
    await loop._maybe_legendary_actions(acted)
    assert sets == []
    loop.llm.generate.return_value = {"actions": [{"type": "roll"}]}
    await loop._maybe_legendary_actions(acted)  # SET raises but is swallowed
    assert sets == [1]


@pytest.mark.asyncio
async def test_lair_actions_dispatch_llm_actions_and_tolerate_failures():
    loop = _loop()
    loop._round_number = 4
    loop.llm.generate.return_value = {"actions": [{"type": "narrate", "text": "the ceiling shakes"}]}
    await loop._maybe_lair_actions()
    loop.dispatcher.execute_batch.assert_awaited_once_with([{"type": "narrate", "text": "the ceiling shakes"}])
    assert "Round 4" in loop.llm.generate.await_args.kwargs["user_message"]
    assert "ONE of them" in loop.llm.generate.await_args.kwargs["extra_context"]

    loop.dispatcher.execute_batch.reset_mock()
    loop.llm.generate.return_value = {"actions": []}
    await loop._maybe_lair_actions()
    loop.llm.generate.return_value = None
    await loop._maybe_lair_actions()
    loop.llm.generate.side_effect = RuntimeError("down")
    await loop._maybe_lair_actions()
    loop.dispatcher.execute_batch.assert_not_called()


# ── _check_combat_end extras / HP parsing ───────────────────────────────────

@pytest.mark.asyncio
async def test_prune_runs_when_the_hp_refresh_fails_and_survives_a_second_failure():
    loop = _loop()
    loop._npc_tokens = [_tok("n1", "G"), _tok("gone", "Gone")]
    loop._pc_tokens = [_tok("p", "P", disp=1)]
    loop.foundry.get_scene_tokens.side_effect = [RuntimeError("1"), [{"id": "n1"}, {"id": "p"}]]
    assert await loop._check_combat_end() is False
    assert [t["id"] for t in loop._npc_tokens] == ["n1"]

    loop.foundry.get_scene_tokens.side_effect = RuntimeError("always")
    assert await loop._check_combat_end() is False
    assert [t["id"] for t in loop._npc_tokens] == ["n1"]  # kept as-is


@pytest.mark.asyncio
async def test_downed_pc_who_is_healed_returns_to_the_fight():
    loop = _loop()
    loop._npc_tokens = [_tok("n", "G", hp=5)]
    loop._pc_tokens = []
    loop._dead_pc_tokens = {"p": _tok("p", "P", hp=0, disp=1)}
    loop.foundry.get_scene_tokens.return_value = [_tok("n", "G", hp=5), _tok("p", "P", hp=4, disp=1)]
    assert await loop._check_combat_end() is False
    assert [t["id"] for t in loop._pc_tokens] == ["p"] and loop._dead_pc_tokens == {}


@pytest.mark.asyncio
async def test_dead_or_stable_pc_moves_to_death_queue_and_unreadable_status_counts_as_dying():
    loop = _loop()
    loop._npc_tokens = [_tok("n", "G", hp=5)]
    loop._pc_tokens = [_tok("p1", "A", hp=0, uuid="Actor.p1", disp=1), _tok("p2", "B", hp=0, uuid="Actor.p2", disp=1),
                       _tok("p3", "C", hp=0, uuid="", disp=1)]
    loop.foundry.get_scene_tokens.return_value = [dict(t) for t in loop._npc_tokens + loop._pc_tokens]

    async def js(code):
        if "p1" in code:
            return {"result": {"isStable": True}}
        raise RuntimeError("relay")
    loop.foundry.execute_js.side_effect = js
    assert await loop._check_combat_end() is False
    assert set(loop._dead_pc_tokens) == {"p1"}
    assert {t["id"] for t in loop._pc_tokens} == {"p2", "p3"}  # unreadable => still dying, still in the fight


@pytest.mark.asyncio
async def test_all_pcs_down_ends_the_fight():
    loop = _loop()
    loop._npc_tokens = [_tok("n", "G", hp=5)]
    loop._pc_tokens = [_tok("p1", "A", hp=0, uuid="Actor.p1", disp=1)]
    loop.foundry.get_scene_tokens.return_value = [_tok("n", "G", hp=5), _tok("p1", "A", hp=0, uuid="Actor.p1", disp=1)]
    loop.foundry.execute_js.return_value = {"result": {"isDead": True}}
    assert await loop._check_combat_end() is True


@pytest.mark.parametrize("token,expected", [
    ({"data": {"attributes": {"hp": {"value": "7"}}}}, 7),
    ({"data": {"attributes": {"hp": {"value": None}}}}, 0),
    ({"data": {"attributes": {"hp": {"value": "abc"}}}}, 0),
    ({"data": {"attributes": {"hp": {}}}, "hp": 3}, 3),
    ({"currentHP": "9"}, 9),
    ({"hp": "x"}, 0),
    ({}, 0),
    ({"hp": None, "currentHP": 2}, 0),  # explicit hp key present with None wins -> unknown -> 0
])
def test_hp_parsing_defaults_unknown_to_zero(token, expected):
    assert CombatLoop._get_hp_from_token(token) == expected


# ── round log / combatant list / end ────────────────────────────────────────

def test_round_log_lines_and_prompt_block():
    loop = _loop()
    loop._round_number = 2
    assert loop._build_round_log() == ""
    loop._log_round_actions("Gob", [
        {"type": "attack_with_item", "target_token_id": "p1"},
        {"type": "move_token", "actor_uuid": "Actor.g"},
        {"type": "roll"},
        {"type": "narrate"},
        "weird"],
        [{"success": False}, {"damage": 0}, {"hit": False}, None, {}])
    assert list(loop._round_log) == [
        "R2: Gob — attack_with_item → p1 (failed)",
        "R2: Gob — move_token → Actor.g for 0 damage",
        "R2: Gob — roll and missed",
        "R2: Gob — narrate",
        "R2: Gob — acted",
    ]
    assert loop._build_round_log().startswith("\n## THIS FIGHT SO FAR\nR2: Gob")


def test_round_log_is_bounded():
    loop = _loop()
    loop._log_round_actions("G", [{"type": "roll"}] * 30, [{}] * 30)
    assert len(loop._round_log) == CombatLoop.ROUND_LOG_LINES


def test_combatant_list_is_capped_pcs_first_and_sides_labeled():
    loop = _loop()
    loop._pc_tokens = [_tok("p", "Pc", disp=0, x=1, y=2)]
    loop._npc_tokens = [_tok(f"n{i}", f"N{i}", x=i, y=i) for i in range(15)]
    lines = loop._build_combatant_list().split("\n")
    assert len(lines) == 10
    assert lines[0] == "- [🟢 PC] Pc at (1, 2)"
    assert lines[1].startswith("- [🔴 NPC] N0")


@pytest.mark.asyncio
async def test_end_combat_records_fight_resets_state_and_notifies(monkeypatch):
    from foundry import scripts
    monkeypatch.setattr(scripts, "end_combat", lambda: "ENDJS")
    loop = _loop()
    loop._running, loop._round_number = True, 3
    loop._round_log.extend(["R1: Gob — attack", "R2: Gob — roll"])
    loop.db.get_active_session_info.return_value = {"session_id": "s1", "campaign": None}
    loop._active_modules = {"bossbar": {}}
    events = []

    async def on_end():
        events.append("end")

    async def on_complete(ev):
        events.append(ev)
    loop.set_combat_end_callback(on_end)
    loop.set_turn_complete_callback(on_complete)
    await loop._end_combat()

    assert loop.is_running is False and not loop._round_log
    outcome = loop.llm.remember_combat.await_args.args[0]
    assert outcome.startswith("Combat ended after 3 round(s).") and "R2: Gob — roll" in outcome
    loop.db.save_conversation.assert_awaited_once_with("s1", "", "event", outcome)
    loop.state_tracker.update_combat.assert_awaited_once_with(in_combat=False)
    loop.state_tracker.set_mode.assert_awaited_once_with("exploration")
    loop.foundry.end_encounter.assert_awaited_once()
    assert "ENDJS" in [c.args[0] for c in loop.foundry.execute_js.await_args_list]
    assert "Combat ends" in loop.foundry.chat_message.await_args.args[0]
    loop.state_tracker.clear_combat_snapshot.assert_called_once()
    assert events == ["end", {"type": "combat_ended", "rounds": 3}]


@pytest.mark.asyncio
async def test_end_combat_completes_even_if_every_relay_call_fails():
    loop = _loop()
    loop._running = True
    loop._round_log.append("R1: x")
    loop.llm.remember_combat.side_effect = RuntimeError("memory")
    loop.foundry.end_encounter.side_effect = RuntimeError("a")
    loop.foundry.execute_js.side_effect = RuntimeError("b")
    loop.foundry.chat_message.side_effect = RuntimeError("c")
    done = []

    async def on_complete(ev):
        done.append(ev)
    loop.set_turn_complete_callback(on_complete)
    await loop._end_combat()
    assert not loop._round_log  # cleared despite the recording failure
    loop.state_tracker.set_mode.assert_awaited_once_with("exploration")
    loop.state_tracker.clear_combat_snapshot.assert_called_once()
    assert done == [{"type": "combat_ended", "rounds": 1}]
