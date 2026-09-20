#!/usr/bin/env python3
"""The CURRENT GAME STATE block announced an empty combat.

_format_state renders the state dict that ContextReinforcementManager builds
for each reinforcement pass. Two of the keys it reads are written by nobody.

`combat_combatants` appears only here, in the read. The manager supplies
mode, scene, in_combat, combat_round and nearby_npcs, and never a combatant
list, so every reinforcement during a fight carried:

      Combat: Active (round 3)
      Combatants:

An empty labelled field, which tells the model the fight has no participants
rather than saying nothing.

`LLMManager._game_state` is the other. It is assigned None in __init__ and
never written anywhere in the codebase, so the `if hasattr(self,
'_game_state') and self._game_state` guard on the turn path could not be
true and active_state was always {}. Game state does reach the model on
every turn — combat/loop and chat_listener pass game_state_summary straight
into generate() — so this block was a second, dead channel for it.

Run:
    cd ai-engine && python -m pytest tests/test_reinforcement_state_block.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context.reinforcer import ContextReinforcer


def _state(**over):
    base = {"mode": "combat", "in_combat": True, "combat_round": 3,
            "scene": {"name": "The Sunken Chapel"}, "nearby_npcs": []}
    base.update(over)
    return base


def _format(**over):
    return ContextReinforcer()._format_state(_state(**over))


# ── combatants ────────────────────────────────────────────────────────────

def test_a_fight_with_no_combatant_list_says_nothing_about_combatants():
    out = _format()

    assert "Combat: Active (round 3)" in out
    assert "Combatants:" not in out, f"an empty combatant list was announced:\n{out}"


def test_a_fight_with_combatants_lists_them():
    out = _format(combat_combatants=[{"name": "Goblin"}, {"name": "Aria"}])

    assert "Combatants: Goblin, Aria" in out


def test_an_empty_combatant_list_is_the_same_as_none():
    assert "Combatants:" not in _format(combat_combatants=[])


def test_out_of_combat_says_so():
    out = _format(in_combat=False, combat_round=None)

    assert "Combat: Not active" in out
    assert "Combatants:" not in out


# ── the rest of the block still renders ───────────────────────────────────

def test_the_scene_and_nearby_npcs_still_render():
    out = _format(in_combat=False, nearby_npcs=[{"name": "Halda"}, {"name": "Veyra"}])

    assert "Location: The Sunken Chapel" in out
    assert "Nearby NPCs: Halda, Veyra" in out


def test_the_combatant_list_is_capped():
    many = [{"name": f"Orc {i}"} for i in range(20)]

    out = _format(combat_combatants=many)

    assert "Orc 9" in out and "Orc 10" not in out


# ── the turn path no longer pretends to have a state source ───────────────

def test_the_llm_manager_has_no_unwritten_game_state_field():
    """It was read on every third turn and set by nothing, so the branch
    could not fire. Game state reaches the model through
    generate(game_state_summary=...) instead."""
    import ast
    import inspect
    import textwrap

    from llm.manager import LLMManager

    tree = ast.parse(textwrap.dedent(inspect.getsource(LLMManager)))
    referenced = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert "_game_state" not in referenced, (
        "a field nobody assigns is still read (hasattr counts, it takes a string)"
    )
