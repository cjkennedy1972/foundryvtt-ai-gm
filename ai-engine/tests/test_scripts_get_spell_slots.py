#!/usr/bin/env python3
"""Regression test for a real bug found in review: foundry/scripts.py briefly
had TWO `def get_spell_slots` — Python silently keeps only the last one, so
RefereeAgent's spell-slot check called a different function than the one it
was written against and always failed open (no test caught it, since the
referee tests mock FoundryClient.get_spell_slots directly, bypassing
scripts.py entirely). This guards against the shadowing recurring, and
exercises the real script text end to end.

Run:
    cd ai-engine && python -m pytest tests/test_scripts_get_spell_slots.py -v
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import foundry.scripts as scripts


def test_only_one_get_spell_slots_definition_in_module_source():
    """Guards against a second `def get_spell_slots` silently shadowing this
    one again — Python doesn't error on duplicate top-level defs, so this
    has to be checked structurally, not just by calling the function."""
    tree = ast.parse(open(scripts.__file__).read())
    names = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "get_spell_slots"
    ]
    assert names == ["get_spell_slots"], f"expected exactly one definition, found {len(names)}"


def test_get_spell_slots_script_shape_matches_referee_expectations():
    """RefereeAgent._check_spell_slot expects a bare dict keyed by level
    string (plus optional 'pact') with {value, max} — no wrapper key."""
    js = scripts.get_spell_slots("Actor.abc123")
    assert '"slots"' not in js and "'slots'" not in js
    assert "spells['spell' + lvl]" in js or "spells[`spell" in js
    assert "pact" in js
