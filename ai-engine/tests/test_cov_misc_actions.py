"""Coverage for actions/system_actions, dispatcher edge paths, undo edge paths, static_files."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import actions.dispatcher as disp
import actions.undo as undo_mod
from actions import system_actions as sa
from actions.dispatcher import ActionDispatcher, _clamp_damage
from actions.executors_shared import ExecutionError
from actions.schemas import ACTION_SCHEMAS, MAX_DAMAGE, MIN_DAMAGE
from actions.undo import UndoLedger, undo_last
from api.static_files import PanelStaticFiles
from config import settings


# --------------------------------------------------------------- system_actions

@pytest.mark.asyncio
async def test_execute_js_rejects_blank_code():
    res = await sa.execute_execute_js("   ", foundry=AsyncMock())
    assert res["success"] is False and res["error"] == "Code is empty"
    assert res["description"] == "   "


@pytest.mark.asyncio
async def test_execute_js_blocked_when_gate_off(monkeypatch):
    monkeypatch.setattr(settings, "allow_execute_js", False, raising=False)
    fc = AsyncMock()
    res = await sa.execute_execute_js("1+1", description="d", foundry=fc)
    assert res["success"] is False and "ALLOW_EXECUTE_JS" in res["error"]
    fc.execute_js.assert_not_called()


@pytest.mark.asyncio
async def test_execute_js_requires_connection_then_runs(monkeypatch):
    monkeypatch.setattr(settings, "allow_execute_js", True, raising=False)
    res = await sa.execute_execute_js("1+1", foundry=None)
    assert res["error"] == "Foundry is not connected"
    fc = AsyncMock()
    fc.is_connected = False
    assert (await sa.execute_execute_js("1+1", foundry=fc))["success"] is False
    fc.is_connected = True
    fc.execute_js.return_value = {"result": 2}
    res = await sa.execute_execute_js("1+1", foundry=fc)
    assert res == {"type": "execute_js", "description": "1+1", "result": {"result": 2}}
    fc.execute_js.assert_awaited_once_with("1+1")


@pytest.mark.asyncio
async def test_pause_stops_listener_pauses_foundry_and_posts_reason():
    fc = AsyncMock()
    state = SimpleNamespace(chat_listener=SimpleNamespace(_running=True))
    res = await sa.execute_pause_game(reason="Break", foundry=fc, app_state=state)
    assert state.chat_listener._running is False
    assert "togglePause(true" in fc.execute_js.await_args.args[0]
    fc.chat_message.assert_awaited_once_with("*Break*", speaker="GM")
    assert res == {"type": "pause_game", "reason": "Break"}


@pytest.mark.asyncio
async def test_pause_survives_foundry_failures(caplog):
    fc = AsyncMock()
    fc.execute_js.side_effect = RuntimeError("down")
    fc.chat_message.side_effect = RuntimeError("down")
    with caplog.at_level(logging.WARNING):
        res = await sa.execute_pause_game(reason="x", foundry=fc, app_state=None)
    assert res["type"] == "pause_game"
    assert "Foundry pause failed" in caplog.text


@pytest.mark.asyncio
async def test_pause_with_no_foundry_and_reason_does_not_raise():
    res = await sa.execute_pause_game(reason="x", foundry=None)
    assert res["reason"] == "x"


@pytest.mark.asyncio
async def test_resume_restarts_listener_and_tolerates_failure(caplog):
    fc = AsyncMock()
    state = SimpleNamespace(chat_listener=SimpleNamespace(_running=False))
    assert (await sa.execute_resume_game(foundry=fc, app_state=state)) == {"type": "resume_game"}
    assert state.chat_listener._running is True
    assert "togglePause(false" in fc.execute_js.await_args.args[0]
    fc.execute_js.side_effect = RuntimeError("down")
    with caplog.at_level(logging.WARNING):
        await sa.execute_resume_game(foundry=fc)
    assert "Foundry unpause failed" in caplog.text


@pytest.mark.asyncio
async def test_macro_requires_manager_and_dispatcher():
    with pytest.raises(ExecutionError):
        await sa.execute_execute_macro("m", app_state=None)
    mgr_only = SimpleNamespace(macro_manager=MagicMock(), action_dispatcher=None)
    with pytest.raises(ExecutionError):
        await sa.execute_execute_macro("m", app_state=mgr_only)


@pytest.mark.asyncio
async def test_macro_dispatches_resolved_action_with_source():
    mgr = MagicMock()
    mgr.resolve_macro.return_value = {"type": "narrate", "text": "hi"}
    dispatcher = MagicMock()
    dispatcher.execute = AsyncMock(return_value={"success": True})
    state = SimpleNamespace(macro_manager=mgr, action_dispatcher=dispatcher)
    res = await sa.execute_execute_macro("m", overrides={"a": 1}, app_state=state, source="player_turn")
    mgr.resolve_macro.assert_called_once_with("m", overrides={"a": 1})
    dispatcher.execute.assert_awaited_once_with(
        {"type": "narrate", "text": "hi", "source": "player_turn"})
    assert res["success"] is True and res["action_type"] == "narrate" and res["error"] is None


@pytest.mark.asyncio
async def test_macro_resolution_error_is_reported_not_dispatched():
    mgr = MagicMock()
    mgr.resolve_macro.return_value = {"type": "x", "error": "unknown macro"}
    dispatcher = MagicMock()
    dispatcher.execute = AsyncMock()
    state = SimpleNamespace(macro_manager=mgr, action_dispatcher=dispatcher)
    res = await sa.execute_execute_macro("m", app_state=state)
    assert res == {"type": "execute_macro", "macro_id": "m", "success": False, "error": "unknown macro"}
    dispatcher.execute.assert_not_called()


@pytest.mark.asyncio
async def test_macro_reports_inner_failure():
    mgr = MagicMock()
    mgr.resolve_macro.return_value = {"type": "narrate"}
    dispatcher = MagicMock()
    dispatcher.execute = AsyncMock(return_value={"success": False, "error": "boom"})
    state = SimpleNamespace(macro_manager=mgr, action_dispatcher=dispatcher)
    res = await sa.execute_execute_macro("m", app_state=state)
    assert res["success"] is False and res["error"] == "boom"


# ------------------------------------------------------------------- dispatcher

def test_clamp_damage_bounds():
    assert _clamp_damage(10) == (10, None)
    v, why = _clamp_damage(MIN_DAMAGE - 5)
    assert v == MIN_DAMAGE and str(MIN_DAMAGE - 5) in why
    v, why = _clamp_damage(MAX_DAMAGE + 5)
    assert v == MAX_DAMAGE and str(MAX_DAMAGE) in why


@pytest.mark.asyncio
async def test_dispatch_rejects_missing_and_unknown_types():
    d = ActionDispatcher(AsyncMock())
    r = await d.execute({})
    assert r == {"type": None, "error": "No action type specified", "success": False}
    r = await d.execute({"type": "no_such_action"})
    assert r["success"] is False and "Unknown action type" in r["error"]


@pytest.mark.asyncio
async def test_dispatch_rejects_unknown_fields_and_missing_schema(monkeypatch):
    d = ActionDispatcher(AsyncMock())
    r = await d.execute({"type": "narrate", "text": "x", "bogus": 1})
    assert r["success"] is False and "Unknown fields" in r["error"] and "bogus" in r["error"]
    monkeypatch.delitem(ACTION_SCHEMAS, "narrate")
    r = await d.execute({"type": "narrate", "text": "x"})
    assert r["error"] == "No schema for action type: narrate"


@pytest.mark.asyncio
async def test_dispatch_applies_clamp_to_handler_kwargs(monkeypatch):
    seen = {}

    async def handler(actor_uuid, damage, **kw):
        seen["damage"] = damage
        return {"type": "update_hp", "success": True}

    monkeypatch.setitem(disp.ACTION_HANDLERS, "update_hp", handler)
    monkeypatch.setattr(disp, "_clamp_damage", lambda v: (7, "clamped for test"))
    r = await ActionDispatcher(AsyncMock()).execute(
        {"type": "update_hp", "actor_uuid": "Actor.a", "damage": 3})
    assert seen["damage"] == 7 and r["success"] is True


@pytest.mark.asyncio
async def test_dispatch_wraps_non_dict_result_and_flags_inner_failure(monkeypatch):
    async def raw(text, **kw):
        return "plain"

    async def inner_fail(text, **kw):
        return {"type": "narrate", "result": {"error": "Entity not found"}}

    d = ActionDispatcher(AsyncMock())
    monkeypatch.setitem(disp.ACTION_HANDLERS, "narrate", raw)
    r = await d.execute({"type": "narrate", "text": "x"})
    assert r["raw_result"] == "plain" and r["success"] is True
    monkeypatch.setitem(disp.ACTION_HANDLERS, "narrate", inner_fail)
    r = await d.execute({"type": "narrate", "text": "x"})
    assert r["success"] is False and r["error"] == "Entity not found"


@pytest.mark.asyncio
async def test_dispatch_handler_exception_becomes_failed_result(monkeypatch):
    async def boom(text, **kw):
        raise ValueError("kaboom")

    monkeypatch.setitem(disp.ACTION_HANDLERS, "narrate", boom)
    d = ActionDispatcher(AsyncMock())
    r = await d.execute({"type": "narrate", "text": "x"})
    assert r["success"] is False and r["error"] == "kaboom" and "_audit" in r
    assert d.undo.last() is None


@pytest.mark.asyncio
async def test_execute_batch_and_available_actions(monkeypatch):
    async def ok(text, **kw):
        return {"type": "narrate", "success": True}

    monkeypatch.setitem(disp.ACTION_HANDLERS, "narrate", ok)
    d = ActionDispatcher(AsyncMock())
    out = await d.execute_batch([{"type": "narrate", "text": "a"}, {"type": "zzz"}])
    assert [o["success"] for o in out] == [True, False]
    assert "narrate" in d.available_actions


# ------------------------------------------------------------------------- undo

def _entry(restore):
    return {"type": "t", "label": "L", "restore": restore}


def test_ledger_discard_only_exact_entry():
    ledger = UndoLedger()
    a, b = _entry({}), _entry({})
    ledger.record(a)
    ledger.record(b)
    ledger.discard(_entry({}))                # equal but not identical: ignored
    assert len(ledger.recent()) == 2
    ledger.discard(a)
    assert ledger.last() is b and len(ledger.recent()) == 1


@pytest.mark.asyncio
async def test_undo_hp_unreadable_blocks_and_keeps_entry():
    fc = MagicMock()
    fc.get_actors = AsyncMock(return_value=[])
    ledger = UndoLedger()
    ledger.record(_entry({"kind": "hp", "actor_uuid": "Actor.a", "hp_before": 5}))
    r = await undo_last(ledger, fc)
    assert r["success"] is False and "Could not read" in r["error"]
    assert ledger.last() is not None


@pytest.mark.asyncio
async def test_undo_hp_already_at_prior_value_writes_nothing():
    fc = MagicMock()
    fc.get_actors = AsyncMock(return_value=[{"uuid": "Actor.a", "hp": 5, "max_hp": 9}])
    fc.decrease_attribute = AsyncMock()
    fc.increase_attribute = AsyncMock()
    ledger = UndoLedger()
    ledger.record(_entry({"kind": "hp", "actor_uuid": "Actor.a", "hp_before": 5}))
    r = await undo_last(ledger, fc)
    assert r == {"success": True, "label": "L"}
    fc.decrease_attribute.assert_not_called()
    fc.increase_attribute.assert_not_called()
    assert ledger.last() is None


def _cond_ledger():
    ledger = UndoLedger()
    ledger.record(_entry({"kind": "condition", "actor_uuid": "Actor.a", "status": "prone"}))
    return ledger


@pytest.mark.asyncio
async def test_undo_condition_already_gone_counts_as_removed(monkeypatch):
    monkeypatch.setattr(undo_mod, "CONDITION_SETTLE_S", 0)
    fc = MagicMock()
    fc.remove_effect = AsyncMock(side_effect=RuntimeError("Effect Not Found"))
    fc.execute_js = AsyncMock(return_value={"result": {"ok": True, "present": False}})
    ledger = _cond_ledger()
    r = await undo_last(ledger, fc)
    assert r["success"] is True and ledger.last() is None


@pytest.mark.asyncio
async def test_undo_condition_other_runtime_error_fails_and_keeps_entry(monkeypatch):
    monkeypatch.setattr(undo_mod, "CONDITION_SETTLE_S", 0)
    fc = MagicMock()
    fc.remove_effect = AsyncMock(side_effect=RuntimeError("relay exploded"))
    ledger = _cond_ledger()
    r = await undo_last(ledger, fc)
    assert r == {"success": False, "error": "relay exploded"}
    assert ledger.last() is not None


@pytest.mark.asyncio
async def test_undo_condition_unconfirmable_and_stuck(monkeypatch):
    monkeypatch.setattr(undo_mod, "CONDITION_SETTLE_S", 0)
    fc = MagicMock()
    fc.remove_effect = AsyncMock()
    fc.execute_js = AsyncMock(return_value={"result": {"ok": False, "error": "no sheet"}})
    r = await undo_last(_cond_ledger(), fc)
    assert r["success"] is False and "no sheet" in r["error"]
    fc.execute_js = AsyncMock(return_value={"result": {"ok": True, "present": True}})
    r = await undo_last(_cond_ledger(), fc)
    assert r["success"] is False and "still on" in r["error"]
    assert fc.remove_effect.await_count == 1 + undo_mod.CONDITION_REMOVE_ATTEMPTS


@pytest.mark.asyncio
async def test_undo_exhaustion_and_move_failure_paths():
    fc = MagicMock()
    fc.execute_js = AsyncMock(return_value="not a dict")     # _js treats as {}
    ledger = UndoLedger()
    ledger.record(_entry({"kind": "exhaustion", "actor_uuid": "Actor.a", "level": 1}))
    r = await undo_last(ledger, fc)
    assert r["success"] is False and "exhaustion back to 1" in r["error"]
    fc.execute_js = AsyncMock(return_value={"result": {"ok": True, "newLevel": 1}})
    assert (await undo_last(ledger, fc))["success"] is True
    ledger.record(_entry({"kind": "move", "token_id": "t", "x": 1, "y": 2}))
    fc.move_token = AsyncMock(return_value={"ok": False})
    r = await undo_last(ledger, fc)
    assert r["success"] is False and "move the token back" in r["error"]
    assert ledger.last() is not None


# ----------------------------------------------------------------- static files

@pytest.mark.asyncio
async def test_panel_static_files_no_cache_only_for_html(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "app.abc123.js").write_text("x=1")
    sf = PanelStaticFiles(directory=str(tmp_path), html=True)

    def scope(path):
        return {"type": "http", "method": "GET", "path": "/" + path, "headers": [],
                "root_path": ""}

    for p in ("index.html", ""):
        r = await sf.get_response(p, scope(p))
        assert r.headers["Cache-Control"] == "no-cache"
    r = await sf.get_response("app.abc123.js", scope("app.abc123.js"))
    assert "cache-control" not in r.headers
