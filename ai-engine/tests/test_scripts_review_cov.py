"""foundry/scripts.py builds JS that runs in the GM's browser; model/user-controlled strings must never be spliced raw."""
import inspect
import json

import pytest

from foundry import scripts

EVIL = 'X"+(a\'b)+`${c}`+\nY</script>'

_BUILDERS = [(n, f) for n, f in inspect.getmembers(scripts, inspect.isfunction) if f.__module__ == "foundry.scripts"]


def _args(fn):
    out = {}
    for name, p in inspect.signature(fn).parameters.items():
        if p.default is not inspect.Parameter.empty:
            continue
        ann = str(p.annotation)
        if "Dict" in ann:
            out[name] = {"Scene": [EVIL]}
        elif "List" in ann:
            out[name] = [EVIL]
        elif ann in ("<class 'int'>", "<class 'float'>") or name in ("level",):
            out[name] = 2
        elif ann == "<class 'bool'>":
            out[name] = False
        else:
            out[name] = EVIL
    return out


@pytest.mark.parametrize("name,fn", _BUILDERS, ids=[n for n, _ in _BUILDERS])
def test_no_builder_splices_a_hostile_string_into_js_unescaped(name, fn):
    js = fn(**_args(fn))
    assert EVIL not in js
    # the newline is what breaks a string literal; it must only ever appear JSON-escaped
    assert "X\"+(a'b)" not in js


@pytest.mark.parametrize("name,fn", _BUILDERS, ids=[n for n, _ in _BUILDERS])
def test_string_arguments_arrive_as_json_literals(name, fn):
    kwargs = _args(fn)
    js = fn(**kwargs)
    for v in kwargs.values():
        if v == EVIL:
            assert json.dumps(v) in js or json.dumps(v.strip().lower()) in js


def test_numeric_arguments_are_coerced_so_a_string_cannot_inject():
    assert "update({'system.resources.legact.value': 3})" in scripts.set_legendary_resource("a", "3")
    assert "Math.min(6, 3)" in scripts.set_exhaustion_level("a", "3")
    for call in (lambda: scripts.set_legendary_resource("a", "3); alert(1"), lambda: scripts.set_combat_turn("1; x", 1),
                 lambda: scripts.adjust_exhaustion("a", "1; x")):
        with pytest.raises(ValueError):
            call()


def test_exhaustion_is_clamped_to_zero_through_six_in_the_script():
    js = scripts.set_exhaustion_level("Actor.1", 99)
    assert "Math.max(0, Math.min(6, 99))" in js
    assert "'system.attributes.exhaustion': newLevel" in js


def test_legendary_resource_builders_target_the_right_sheet_paths():
    assert "system.resources?.legact" in scripts.get_legendary_resource("Actor.1")
    assert "system.resources.legact.value': max" in scripts.reset_legendary_resource("Actor.1")
    assert "'system.resources.legact.value': 2" in scripts.set_legendary_resource("Actor.1", 2)
    assert "system.resources?.legres" in scripts.get_legendary_resistance_resource("Actor.1")


def test_condition_present_checks_the_statuses_set_for_the_given_id():
    js = scripts.condition_present("Actor.1", "prone")
    assert 'actor.statuses?.has("prone")' in js and 'fromUuid("Actor.1")' in js


def test_combat_scripts_do_not_open_dialogs():
    js = scripts.end_combat()
    assert "combat.delete()" in js and "endCombat(" not in js
    assert "combat.update({round: 4, turn: 0})" in scripts.set_combat_turn(4, 0)


def test_count_scene_placeables_looks_the_scene_up_by_escaped_name():
    js = scripts.count_scene_placeables('Crypt "A"')
    assert 'game.scenes.getName("Crypt \\"A\\"")' in js and "walls: s.walls.size" in js
