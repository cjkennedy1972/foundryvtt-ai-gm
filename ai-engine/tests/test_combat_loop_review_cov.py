"""CombatLoop start-up and turn sequencing: the control flow that decides who acts, when a round
rolls over, and when the fight stops."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from combat.loop import CombatLoop, _limit_multiattack_actions
from config import settings


def _tok(tid, name, hp=10, disp=-1, uuid=None, **kw):
    return {"id": tid, "name": name, "disposition": disp, "actorUuid": uuid or f"Actor.{tid}",
            "data": {"attributes": {"hp": {"value": hp}}}, **kw}


def _loop(**kw):
    foundry = AsyncMock()
    foundry.get_world_info.return_value = {"modules": []}
    foundry.execute_js.return_value = {"result": []}
    state = AsyncMock()
    state.clear_combat_snapshot = MagicMock()
    loop = CombatLoop(foundry=foundry, llm=MagicMock(), dispatcher=MagicMock(),
                      state_tracker=state, db=AsyncMock(), **kw)
    return loop


# ── _limit_multiattack_actions ──────────────────────────────────────────────

def test_limit_multiattack_keeps_non_attacks_and_caps_attacks():
    acts = [{"type": "attack_with_item", "n": 1}, {"type": "narrate"}, {"type": "attack_with_item", "n": 2},
            {"type": "move_token"}, {"type": "attack_with_item", "n": 3}]
    out = _limit_multiattack_actions(acts, 2)
    assert [a.get("n") for a in out if a["type"] == "attack_with_item"] == [1, 2]
    assert [a["type"] for a in out if a["type"] != "attack_with_item"] == ["narrate", "move_token"]


def test_limit_multiattack_floors_at_one_attack():
    acts = [{"type": "attack_with_item", "n": 1}, {"type": "attack_with_item", "n": 2}]
    assert _limit_multiattack_actions(acts, 0) == [acts[0]]


# ── start_combat_loop ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_while_running_is_ignored():
    loop = _loop()
    loop._running = True
    await loop.start_combat_loop([_tok("a", "A")])
    loop.foundry.get_world_info.assert_not_called()
    loop.state_tracker.update_combat.assert_not_called()


@pytest.mark.asyncio
async def test_missing_disposition_refuses_to_start_and_tells_the_gm(monkeypatch):
    loop = _loop()
    bad = {"id": "x1", "name": "Mystery"}
    with pytest.raises(ValueError) as exc:
        await loop.start_combat_loop([_tok("p", "Pc", disp=1), bad])
    assert "Mystery" in str(exc.value) and "x1" in str(exc.value)
    assert loop.is_running is False
    msg = loop.foundry.chat_message.await_args.args[0]
    assert "Mystery" in msg
    loop.state_tracker.set_mode.assert_not_called()


@pytest.mark.asyncio
async def test_missing_disposition_still_raises_if_chat_fails():
    loop = _loop()
    loop.foundry.chat_message.side_effect = RuntimeError("relay down")
    with pytest.raises(ValueError):
        await loop.start_combat_loop([{"id": "x"}])


@pytest.mark.asyncio
async def test_start_splits_sides_uses_foundry_initiative_and_sets_state(monkeypatch):
    loop = _loop()
    loop._process_turns = AsyncMock()
    started, begun = [], []

    async def on_start(ev):
        started.append(ev)

    async def on_combat_start(tokens):
        begun.append(tokens)

    loop.set_turn_start_callback(on_start)
    loop.set_combat_start_callback(on_combat_start)
    loop.foundry.get_world_info.return_value = {"modules": [
        {"id": "midi-qol", "title": "Midi", "version": "1", "active": True},
        {"id": "dae", "active": False},
    ]}
    # Foundry returns an id we don't know (dropped) and omits "n2" (appended).
    loop._fetch_initiative_order = AsyncMock(return_value=["n1", "ghost", "p1"])
    tokens = [_tok("p1", "Pc", disp=1), _tok("n1", "Gob", disp=-1), _tok("n2", "Orc", disp=-1),
              _tok("p2", "Neutral", disp=0)]
    await loop.start_combat_loop(tokens)

    assert loop._turn_order == ["n1", "p1", "p2", "n2"]
    assert [t["id"] for t in loop._pc_tokens] == ["p1", "p2"]  # neutral counts as PC side
    assert [t["id"] for t in loop._npc_tokens] == ["n1", "n2"]
    assert loop._has_midi_qol and not loop._has_dae
    assert loop.is_running
    loop.state_tracker.save_combat_snapshot.assert_awaited_once_with(tokens=tokens)
    loop.state_tracker.update_combat.assert_awaited_once_with(
        in_combat=True, round_num=1, turn=0, turn_order=["n1", "p1", "p2", "n2"])
    loop.state_tracker.set_mode.assert_awaited_once_with("combat")
    assert started == [{"type": "combat_started", "round": 1, "turn_order": ["n1", "p1", "p2", "n2"],
                        "pc_count": 2, "npc_count": 2}]
    assert begun == [tokens]
    loop._process_turns.assert_awaited_once()
    # initiative announced to chat with the first slot marked
    init_msg = next(c.args[0] for c in loop.foundry.chat_message.await_args_list
                    if "INITIATIVE" in c.args[0])
    assert "1. ⚔️ Gob ← **YOUR TURN**" in init_msg
    assert "2. 👤 Pc" in init_msg


@pytest.mark.asyncio
async def test_start_falls_back_to_shuffle_and_survives_module_detection_failure(monkeypatch):
    loop = _loop()
    loop._process_turns = AsyncMock()
    loop.foundry.get_world_info.side_effect = RuntimeError("no world info")
    loop._fetch_initiative_order = AsyncMock(return_value=[])
    shuffled = []
    monkeypatch.setattr("combat.loop.random.shuffle", lambda lst: shuffled.append(list(lst)) or lst.reverse())
    await loop.start_combat_loop([_tok("p", "P", disp=1), _tok("n", "N", disp=-1)])
    assert shuffled == [["p", "n"]]
    assert loop._turn_order == ["n", "p"]
    assert loop.is_running


@pytest.mark.asyncio
async def test_failed_setup_leaves_loop_not_running():
    loop = _loop()
    loop._fetch_initiative_order = AsyncMock(return_value=[])
    loop.state_tracker.update_combat.side_effect = RuntimeError("db")
    with pytest.raises(RuntimeError):
        await loop.start_combat_loop([_tok("p", "P", disp=1)])
    assert loop.is_running is False


# ── foundry sync / bossbar / initiative / announce ──────────────────────────

@pytest.mark.asyncio
async def test_sync_foundry_combat_sets_turn_only_after_ok(monkeypatch):
    from foundry import scripts
    loop = _loop()
    loop._turn_order = ["a"]
    monkeypatch.setattr(scripts, "sync_combat_combatants", lambda ids: f"SYNC{ids}")
    monkeypatch.setattr(scripts, "set_combat_turn", lambda r, t: f"TURN{r}/{t}")
    loop.foundry.execute_js.side_effect = [{"result": {"ok": True}}, {"result": 1}]
    await loop._sync_foundry_combat()
    assert [c.args[0] for c in loop.foundry.execute_js.await_args_list] == ["SYNC['a']", "TURN1/0"]

    loop.foundry.execute_js.reset_mock()
    loop.foundry.execute_js.side_effect = [{"result": {"ok": False}}]
    await loop._sync_foundry_combat()
    assert loop.foundry.execute_js.await_count == 1  # unexpected result: no turn push

    loop.foundry.execute_js.side_effect = RuntimeError("boom")
    await loop._sync_foundry_combat()  # swallowed


@pytest.mark.asyncio
async def test_sync_foundry_combat_turn_pushes_current_position(monkeypatch):
    from foundry import scripts
    loop = _loop()
    loop._round_number, loop._current_turn_index = 3, 2
    monkeypatch.setattr(scripts, "set_combat_turn", lambda r, t: f"TURN{r}/{t}")
    await loop._sync_foundry_combat_turn()
    loop.foundry.execute_js.assert_awaited_once_with("TURN3/2")
    loop.foundry.execute_js.side_effect = RuntimeError("x")
    await loop._sync_foundry_combat_turn()


@pytest.mark.asyncio
async def test_bossbar_only_when_module_active_and_failures_are_swallowed():
    loop = _loop()
    await loop._apply_bossbar()
    await loop._clear_bossbar()
    loop.foundry.execute_js.assert_not_called()

    loop._active_modules = {"bossbar": {}}
    await loop._apply_bossbar()
    assert "setFlag('bossbar','actors'" in loop.foundry.execute_js.await_args.args[0]
    await loop._clear_bossbar()
    assert "unsetFlag('bossbar','actors')" in loop.foundry.execute_js.await_args.args[0]
    loop.foundry.execute_js.side_effect = RuntimeError("nope")
    await loop._apply_bossbar()
    await loop._clear_bossbar()


@pytest.mark.asyncio
async def test_fetch_initiative_order_stringifies_and_drops_falsy():
    loop = _loop()
    loop.foundry.execute_js.return_value = {"result": ["a", 5, None, ""]}
    assert await loop._fetch_initiative_order() == ["a", "5"]
    loop.foundry.execute_js.return_value = {"result": None}
    assert await loop._fetch_initiative_order() == []
    loop.foundry.execute_js.return_value = ["raw"]  # non-dict reply is itself the order
    assert await loop._fetch_initiative_order() == ["raw"]
    loop.foundry.execute_js.side_effect = RuntimeError("x")
    assert await loop._fetch_initiative_order() == []


@pytest.mark.asyncio
async def test_announce_turn_adds_call_to_action_only_for_pcs():
    loop = _loop()
    await loop._announce_current_turn("Thalia", False, 2, 3)
    pc_msg = loop.foundry.chat_message.await_args.args[0]
    assert "Round 3, Turn 2" in pc_msg and "Thalia" in pc_msg and "What is your next action?" in pc_msg
    await loop._announce_current_turn("Gob", True, 1, 1)
    assert "What is your next action?" not in loop.foundry.chat_message.await_args.args[0]
    loop.foundry.chat_message.side_effect = RuntimeError("x")
    await loop._announce_current_turn("Gob", True, 1, 1)  # swallowed


@pytest.mark.asyncio
async def test_announce_initiative_swallows_chat_failure():
    loop = _loop()
    loop._turn_order = ["n"]
    loop._npc_tokens = [_tok("n", "Gob")]
    loop.foundry.chat_message.side_effect = RuntimeError("x")
    await loop._announce_initiative()
    loop.foundry.chat_message.assert_awaited_once()  # the announcement was attempted, its failure contained


def test_module_features_summary_lists_each_active_module():
    loop = _loop()
    assert loop._get_module_features_summary() == "No automated combat modules active."
    loop._has_midi_qol = loop._has_dae = loop._has_autoanimations = True
    s = loop._get_module_features_summary()
    assert "MIDI QOL" in s and "DAE" in s and "AutoAnimations" in s


@pytest.mark.asyncio
async def test_track_active_effects_is_dae_gated_and_failure_safe(monkeypatch):
    from foundry import scripts
    monkeypatch.setattr(scripts, "get_active_effects", lambda u: f"FX{u}")
    loop = _loop()
    await loop._track_active_effects({"actorUuid": "A"})
    loop.foundry.execute_js.assert_not_called()
    loop._has_dae = True
    loop.foundry.execute_js.return_value = {"result": [{"name": "Bless", "disabled": False},
                                                       {"name": "Off", "disabled": True}]}
    await loop._track_active_effects({"actorUuid": "A", "name": "N"})
    loop.foundry.execute_js.assert_awaited_once_with("FXA")
    loop.foundry.execute_js.side_effect = RuntimeError("x")
    await loop._track_active_effects({"actorUuid": "A"})


# ── _process_turns ──────────────────────────────────────────────────────────

def _drive(loop, *, stop_after, end_after=None, order=None):
    """Replace every collaborator of _process_turns with recorders. ``stop_after`` NPC/PC turns
    run, then the loop is told to stop."""
    calls = []

    async def act(kind, token):
        calls.append((kind, token["id"], loop._round_number))
        if len([c for c in calls if c[0] in ("npc", "pc")]) >= stop_after:
            loop._running = False

    loop._maybe_death_save = AsyncMock(return_value=False)
    loop._turn_gap = AsyncMock()
    async def npc(t):
        await act("npc", t)

    async def pc(t):
        await act("pc", t)

    loop._process_npc_turn = AsyncMock(side_effect=npc)
    loop._wait_for_pc_input = AsyncMock(side_effect=pc)
    loop._maybe_legendary_actions = AsyncMock()
    async def lair():
        calls.append(("lair", None, loop._round_number))

    loop._maybe_lair_actions = AsyncMock(side_effect=lair)
    loop._announce_current_turn = AsyncMock()
    loop._sync_foundry_combat_turn = AsyncMock()
    loop._check_combat_end = AsyncMock(return_value=False)
    async def end():
        loop._running = False

    loop._end_combat = AsyncMock(side_effect=end)
    return calls


@pytest.mark.asyncio
async def test_turns_dispatch_by_side_and_roll_into_the_next_round(monkeypatch):
    monkeypatch.setattr(settings, "combat_round_cap", 50)
    loop = _loop()
    loop._pc_tokens, loop._npc_tokens = [_tok("p", "Pc", disp=1)], [_tok("n", "Gob")]
    loop._turn_order, loop._running = ["p", "n"], True
    seen = []
    async def on_start(e):
        seen.append(e)

    loop.set_turn_start_callback(on_start)
    calls = _drive(loop, stop_after=3)
    await loop._process_turns()

    assert calls == [("pc", "p", 1), ("npc", "n", 1), ("lair", None, 2), ("pc", "p", 2)]
    loop._turn_gap.assert_awaited_once()  # only before the NPC
    assert [e["type"] for e in seen] == ["turn_started", "turn_started", "round_started", "turn_started"]
    assert seen[2]["round"] == 2
    assert seen[1] == {"type": "turn_started", "round": 1, "turn": 2, "actor": "Gob", "is_npc": True}
    # state tracker told the post-advance index each turn
    turns = [c.kwargs["turn"] for c in loop.state_tracker.update_combat.await_args_list]
    assert turns[:2] == [1, 2]
    assert loop._current_turn_index == 1 and loop._round_number == 2


@pytest.mark.asyncio
async def test_a_skipped_death_save_turn_runs_no_handler(monkeypatch):
    loop = _loop()
    loop._pc_tokens, loop._npc_tokens = [_tok("p", "Pc", disp=1)], [_tok("n", "Gob")]
    loop._turn_order, loop._running = ["p", "n"], True
    calls = _drive(loop, stop_after=99)

    async def death_save(token):
        loop._running = False
        return True
    loop._maybe_death_save = AsyncMock(side_effect=death_save)
    await loop._process_turns()
    assert calls == []
    loop._wait_for_pc_input.assert_not_called()
    loop._maybe_legendary_actions.assert_awaited_once()  # still given its chance
    assert loop._current_turn_index == 1  # but the turn was consumed


@pytest.mark.asyncio
async def test_handler_crash_does_not_stall_the_turn(monkeypatch):
    loop = _loop()
    loop._pc_tokens, loop._npc_tokens = [_tok("p", "Pc", disp=1)], [_tok("n", "Gob")]
    loop._turn_order, loop._running = ["p", "n"], True
    _drive(loop, stop_after=99)

    async def boom(token):
        loop._running = False
        raise RuntimeError("relay")
    loop._wait_for_pc_input = AsyncMock(side_effect=boom)
    loop._maybe_legendary_actions = AsyncMock(side_effect=RuntimeError("legend"))
    await loop._process_turns()
    assert loop._current_turn_index == 1  # advanced despite both failures


@pytest.mark.asyncio
async def test_unknown_token_is_dropped_and_empty_order_ends_combat():
    loop = _loop()
    loop._turn_order, loop._running = ["ghost"], True
    _drive(loop, stop_after=99)
    await loop._process_turns()
    assert loop._turn_order == []
    loop._end_combat.assert_awaited_once()

    loop = _loop()
    loop._pc_tokens = [_tok("p", "Pc", disp=1)]
    loop._turn_order, loop._running, loop._current_turn_index = ["ghost", "p"], True, 0
    calls = _drive(loop, stop_after=1)
    await loop._process_turns()
    assert loop._turn_order == ["p"]
    assert calls[0] == ("pc", "p", 1)
    loop._end_combat.assert_not_called()


@pytest.mark.asyncio
async def test_dead_pc_slot_is_skipped_silently_and_wraps_the_round():
    loop = _loop()
    loop._npc_tokens = [_tok("n", "Gob")]
    loop._dead_pc_tokens = {"d": _tok("d", "Fallen", hp=0, disp=1)}
    loop._turn_order, loop._running, loop._current_turn_index = ["n", "d"], True, 1
    calls = _drive(loop, stop_after=1)
    await loop._process_turns()
    # dead PC skipped with no announcement, round wrapped, then NPC acted in round 2
    assert calls == [("npc", "n", 2)]
    assert [c.args[0] for c in loop._announce_current_turn.await_args_list] == ["Gob"]
    assert loop._round_number == 2


@pytest.mark.asyncio
async def test_dead_pc_skip_ends_combat_when_check_says_so():
    loop = _loop()
    loop._dead_pc_tokens = {"d": _tok("d", "Fallen", hp=0, disp=1)}
    loop._turn_order, loop._running = ["d"], True
    _drive(loop, stop_after=99)
    loop._check_combat_end = AsyncMock(return_value=True)
    await loop._process_turns()
    loop._end_combat.assert_awaited_once()
    loop._announce_current_turn.assert_not_called()


@pytest.mark.asyncio
async def test_combat_ends_immediately_after_a_turn_when_a_side_is_wiped():
    loop = _loop()
    loop._pc_tokens, loop._npc_tokens = [_tok("p", "Pc", disp=1)], [_tok("n", "Gob")]
    loop._turn_order, loop._running = ["p", "n"], True
    _drive(loop, stop_after=99)
    loop._check_combat_end = AsyncMock(return_value=True)
    await loop._process_turns()
    loop._end_combat.assert_awaited_once()
    loop._sync_foundry_combat_turn.assert_not_called()
    assert loop._round_number == 1


@pytest.mark.asyncio
async def test_round_cap_posts_stalemate_and_ends(monkeypatch):
    monkeypatch.setattr(settings, "combat_round_cap", 2)
    loop = _loop()
    loop._pc_tokens = [_tok("p", "Pc", disp=1)]
    loop._turn_order, loop._running = ["p"], True
    calls = _drive(loop, stop_after=99)
    await loop._process_turns()
    msg = loop.foundry.chat_message.await_args.args[0]
    assert "stalemate" in msg and "round 2" in msg
    loop._end_combat.assert_awaited_once()
    assert ("pc", "p", 2) in calls  # round 2 (== cap) is still played in full
    assert ("lair", None, 3) not in calls  # round 3 never starts


@pytest.mark.asyncio
async def test_lair_failure_and_round_callbacks_do_not_break_the_loop(monkeypatch):
    monkeypatch.setattr(settings, "combat_round_cap", 50)
    loop = _loop()
    loop._pc_tokens = [_tok("p", "Pc", disp=1)]
    loop._turn_order, loop._running = ["p"], True
    _drive(loop, stop_after=2)
    loop._maybe_lair_actions = AsyncMock(side_effect=RuntimeError("lair"))
    await loop._process_turns()
    assert loop._round_number >= 2


@pytest.mark.asyncio
async def test_pruned_dead_npc_does_not_make_the_next_actor_act_twice(monkeypatch):
    """_reanchor_turn_index is what keeps the index right when a combatant dies mid-round."""
    monkeypatch.setattr(settings, "combat_round_cap", 50)
    loop = _loop()
    loop._pc_tokens = [_tok("p", "Pc", disp=1)]
    loop._npc_tokens = [_tok("n1", "G1"), _tok("n2", "G2")]
    loop._turn_order, loop._running = ["n1", "p", "n2"], True
    calls = _drive(loop, stop_after=3)

    async def kill_n1_then_act(token):
        calls.append(("npc", token["id"], loop._round_number))
        loop._npc_tokens = [t for t in loop._npc_tokens if t["id"] != "n1"]  # n1 dies on its own turn
    loop._process_npc_turn = AsyncMock(side_effect=kill_n1_then_act)
    async def pc_turn(t):
        calls.append(("pc", t["id"], loop._round_number))
    loop._wait_for_pc_input = AsyncMock(side_effect=pc_turn)

    async def check():
        return False
    loop._check_combat_end = AsyncMock(side_effect=check)
    # stop once the third combatant has been announced
    async def announce(name, is_npc, n, r):
        if len(loop._announce_current_turn.await_args_list) >= 3:
            loop._running = False
    loop._announce_current_turn = AsyncMock(side_effect=announce)
    await loop._process_turns()
    assert [c[1] for c in calls if c[0] in ("npc", "pc")] == ["n1", "p", "n2"]
    assert loop._turn_order == ["p", "n2"]


# ── _reanchor_turn_index ────────────────────────────────────────────────────

def test_reanchor_follows_the_actor_that_survived():
    loop = _loop()
    loop._pc_tokens, loop._npc_tokens = [_tok("p", "P", disp=1)], [_tok("n2", "G2")]
    loop._turn_order = ["n1", "p", "n2"]  # n1 (earlier slot) was pruned
    loop._reanchor_turn_index("p", 1)
    assert loop._turn_order == ["p", "n2"]
    assert loop._current_turn_index == 1  # right after p


def test_reanchor_when_actor_died_resumes_at_its_old_slot():
    loop = _loop()
    loop._pc_tokens, loop._npc_tokens = [_tok("p", "P", disp=1)], [_tok("n3", "G3")]
    loop._turn_order = ["n0", "n1", "p", "n3"]  # n0 and n1 gone; n1 just acted (pos 1)
    loop._reanchor_turn_index("n1", 1)
    assert loop._turn_order == ["p", "n3"]
    assert loop._current_turn_index == 0  # 1 - 2 removed before => max(0, -1) == 0 -> "p" next


def test_reanchor_keeps_downed_pcs_and_ignores_total_wipe():
    loop = _loop()
    loop._dead_pc_tokens = {"d": _tok("d", "D", hp=0, disp=1)}
    loop._npc_tokens = [_tok("n", "G")]
    loop._turn_order = ["d", "n", "x"]
    loop._reanchor_turn_index("n", 1)
    assert loop._turn_order == ["d", "n"]
    assert loop._current_turn_index == 2  # wraps via caller's round-boundary check

    loop = _loop()
    loop._turn_order, loop._current_turn_index = ["x"], 5
    loop._reanchor_turn_index("x", 0)
    assert loop._turn_order == ["x"] and loop._current_turn_index == 5  # untouched when nothing is valid


# ── stop / callbacks / properties ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_unblocks_a_waiting_pc_turn():
    loop = _loop()
    loop._running = True
    waiter = asyncio.create_task(loop._pc_turn_event.wait())
    await asyncio.sleep(0)
    await loop.stop()
    await asyncio.wait_for(waiter, 1)
    assert loop.is_running is False


def test_advance_pc_turn_sets_the_event_and_properties_reflect_state():
    loop = _loop()
    loop._round_number, loop._current_turn_index, loop._turn_order = 4, 2, ["a", "b", "c"]
    assert not loop._pc_turn_event.is_set()
    loop.advance_pc_turn()
    assert loop._pc_turn_event.is_set()
    assert (loop.current_round, loop.current_turn, loop.turn_order) == (4, 3, ["a", "b", "c"])
    assert loop.turn_key == (4, 2)


@pytest.mark.asyncio
async def test_register_turn_advance_stores_callback():
    loop = _loop()
    cb = AsyncMock()
    await loop._register_turn_advance(cb)
    assert loop._on_turn_advance is cb
    loop.set_turn_complete_callback(cb)
    loop.set_combat_end_callback(cb)
    assert loop._on_turn_complete_callback is cb and loop._on_combat_end_callback is cb
