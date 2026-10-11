"""Review-2 fixes: Pact slots, spell-level validation, dnd5e save objects in scripts."""

import asyncio
import json
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from actions.executors import execute_cast_spell
from foundry import scripts

node = shutil.which("node")
needs_node = pytest.mark.skipif(not node, reason="node not available")


def _run_js(body: str, actor: dict, extra: str = ""):
    js = f"""
    const actor = {json.dumps(actor)};
    actor.update = async (d) => {{ actor.updates = d; }};
    globalThis.fromUuid = async () => actor;
    {extra}
    (async () => {{ const r = await (async () => {{ {body} }})(); console.log(JSON.stringify([r, actor.updates ?? null])); }})();
    """
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_spend_spell_slot_uses_pact_for_warlock():
    spells = {"spell1": {"value": 0, "max": 0}, "pact": {"value": 2, "max": 2, "level": 3}}
    r, upd = _run_js(scripts.spend_spell_slot("Actor.w", 2), {"system": {"spells": spells}})
    assert r["used"] is True and r["remaining"] == 1
    assert upd == {"system.spells.pact.value": 1}


@needs_node
def test_spend_spell_slot_pact_too_low_for_spell_level():
    spells = {"pact": {"value": 2, "max": 2, "level": 2}}
    r, upd = _run_js(scripts.spend_spell_slot("Actor.w", 3), {"system": {"spells": spells}})
    assert r["used"] is False and upd is None


@needs_node
def test_spend_spell_slot_prefers_regular_slot():
    spells = {"spell3": {"value": 1, "max": 2}, "pact": {"value": 2, "max": 2, "level": 3}}
    r, upd = _run_js(scripts.spend_spell_slot("Actor.w", 3), {"system": {"spells": spells}})
    assert upd == {"system.spells.spell3.value": 0}


@needs_node
@pytest.mark.parametrize("save", [3, {"value": 3}])
def test_environmental_save_accepts_number_or_object_and_has_no_undefined_refs(save):
    body = scripts.resolve_environmental_save("dex", 10, "1d6", True, ["t1"], ["t1"])
    extra = """
    globalThis.canvas = {tokens: {placeables: [{id: 't1', name: 'T', actor: {name: 'T',
      system: {abilities: {dex: {save: %s}}}, applyDamage: async (n) => { globalThis.dmg = n; }}}]}};
    globalThis.Roll = class { constructor(f) { this.f = f; } async evaluate() { this.total = this.f.startsWith('1d20') ? 1 + Number(this.f.split('+')[1]) : 8; return this; } };
    globalThis.ChatMessage = {create: async () => ({})};
    """ % json.dumps(save)
    r, _ = _run_js(body, {}, extra)
    assert r["ok"] is True and r["results"][0]["saveTotal"] == 4  # 1 + 3, not NaN / [object Object]
    assert r["results"][0]["success"] is False and r["results"][0]["damageDealt"] == 8


def _foundry(base_level=None, used=True):
    f = AsyncMock()
    f.use_spell_slot = AsyncMock(return_value={"ok": True, "used": used, "remaining": 1})
    f.break_concentration = AsyncMock()
    f.check_spell_ritual = AsyncMock(return_value={"isRitual": False})
    f.chat_message = AsyncMock()
    f.execute_js = AsyncMock(return_value={"result": {
        "newSpellRequiresConcentration": False, "alreadyConcentrating": False, "spellLevel": base_level}})
    return f


def _cast(f, name="Fireball", level=3):
    return asyncio.run(execute_cast_spell("Actor.x", name, level, foundry=f))


def test_cast_rejects_leveled_spell_as_cantrip_using_sheet_level():
    f = _foundry(base_level=3)
    r = _cast(f, level=0)
    assert r["success"] is False and "level 3" in r["error"]
    f.use_spell_slot.assert_not_awaited()


def test_cast_rejects_level_below_base_but_allows_upcast():
    f = _foundry(base_level=3)
    assert _cast(f, level=2)["success"] is False
    assert _cast(f, level=5).get("success") is not False


def test_cast_falls_back_to_rules_database_when_sheet_has_no_level():
    f = _foundry(base_level=None)
    r = _cast(f, name="Fireball", level=0)
    assert r["success"] is False
    f.use_spell_slot.assert_not_awaited()


def test_real_cantrip_still_free():
    f = _foundry(base_level=0)
    r = _cast(f, name="Fire Bolt", level=0)
    assert r.get("success") is not False and r["result"]["cantrip"] is True


# --- player-lookup failure must fail closed, rests/setup must not claim success -------------

@pytest.fixture
def failing_lookup(monkeypatch):
    import actions.executors as ex
    monkeypatch.setattr(ex, "_pc_uuid_cache", {})
    monkeypatch.setattr(ex, "_pc_uuid_cache_at", 0.0)
    f = AsyncMock()
    f.get_actors = AsyncMock(side_effect=RuntimeError("relay down"))
    return f


@pytest.mark.parametrize("call", [
    lambda ex, f: ex.execute_skill_check("Actor.pc", "stealth", 12, foundry=f),
    lambda ex, f: ex.execute_saving_throw("Actor.pc", "dex", 12, foundry=f),
    lambda ex, f: ex.execute_death_save("Actor.pc", foundry=f),
    lambda ex, f: ex.execute_attack_with_item("Actor.pc", "Sword", "tok1", foundry=f),
])
def test_roll_executors_do_not_roll_when_player_lookup_fails(failing_lookup, call):
    import actions.executors as ex
    r = asyncio.run(call(ex, failing_lookup))
    assert r["success"] is False and "verify" in r["error"]
    failing_lookup.request_skill_check.assert_not_called()
    failing_lookup.request_saving_throw.assert_not_called()
    failing_lookup.request_death_save.assert_not_called()
    failing_lookup.execute_js.assert_not_called()


@pytest.mark.parametrize("fn", ["execute_short_rest", "execute_long_rest"])
def test_empty_rest_list_is_not_success(fn):
    import actions.executors as ex
    f = AsyncMock()
    r = asyncio.run(getattr(ex, fn)([], foundry=f))
    assert r["success"] is False
    f.chat_message.assert_not_called()


def test_setup_scene_all_failed_reports_failure_with_step_errors():
    import actions.executors as ex
    f = AsyncMock()
    f.configure_scene = AsyncMock(side_effect=RuntimeError("cfg"))
    f.canvas_create = AsyncMock(side_effect=RuntimeError("walls"))
    r = asyncio.run(ex.execute_setup_scene(walls=[{}], foundry=f))
    assert r["success"] is False
    assert r["errors"] == {"scene_config_error": "cfg", "walls_error": "walls"}


def test_setup_scene_partial_failure_still_succeeds_but_lists_errors():
    import actions.executors as ex
    f = AsyncMock()
    f.canvas_create = AsyncMock(side_effect=RuntimeError("walls"))
    r = asyncio.run(ex.execute_setup_scene(walls=[{}], foundry=f))
    assert r["success"] is True and r["errors"] == {"walls_error": "walls"}
