#!/usr/bin/env python3
"""The world summary read a GameState that does not exist.

POST /api/context/world_summary hands update_world_summary the real thing:
`state.state_tracker.state.model_dump()`. Three of the keys it reads do not
match what GameState dumps.

Driving it with a real GameState mid-combat:

    **Campaign:** Oakhaven
    **Session:** 4
    **Mode:** GameMode.COMBAT
    **Current Scene:** The Sunken Chapel

  - the combat block is missing: it reads "combat_state", GameState has
    "combat" — and inside it "round_num", where CombatState has "round"
  - Mode is the enum's repr, not "combat". GameState.get_summary does
    normalise this; this function did not
  - npc_context is declared `str` on GameState and iterated here as a dict,
    so as soon as a scene sets NPC context:

        RAISED AttributeError: 'str' object has no attribute 'items'

    which the route turns into a 500.

Run:
    cd ai-engine && python -m pytest tests/test_world_summary_shape.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context.reinforcer import ContextReinforcer
from state.models import CombatState, GameMode, GameState


def _summary(state: GameState) -> str:
    reinforcer = ContextReinforcer()
    reinforcer.update_world_summary(state.model_dump())
    return reinforcer.world_summary


def _fighting() -> GameState:
    return GameState(
        mode=GameMode.COMBAT, current_scene="The Sunken Chapel", campaign="Oakhaven",
        session_number=4,
        combat=CombatState(in_combat=True, round=3, turn=1, turn_order=["a", "b", "c"]),
    )


# ── combat ────────────────────────────────────────────────────────────────

def test_a_fight_reaches_the_world_summary():
    out = _summary(_fighting())

    assert "Round 3" in out, f"the combat block never rendered:\n{out}"
    assert "Turn 1" in out
    assert "3 combatants" in out


def test_out_of_combat_says_nothing_about_rounds():
    out = _summary(GameState(campaign="Oakhaven", current_scene="The Road"))

    assert "Round" not in out


# ── mode ──────────────────────────────────────────────────────────────────

def test_the_mode_is_its_value_not_the_enum_repr():
    out = _summary(_fighting())

    assert "**Mode:** combat" in out
    assert "GameMode." not in out, "the enum repr leaked into the prompt"


def test_a_mode_that_is_already_a_string_still_works():
    """Deserialised state can carry a plain string, as GameState.get_summary
    allows for."""
    reinforcer = ContextReinforcer()
    reinforcer.update_world_summary({"mode": "exploration", "campaign": "Oakhaven"})

    assert "**Mode:** exploration" in reinforcer.world_summary


# ── npc context ───────────────────────────────────────────────────────────

def test_npc_context_as_a_string_does_not_raise():
    state = _fighting()
    state.npc_context = "Halda Ironvein: 12hp, hostile"

    out = _summary(state)

    assert "Halda Ironvein" in out


def test_npc_context_as_a_mapping_still_renders_each_npc():
    reinforcer = ContextReinforcer()
    reinforcer.update_world_summary({
        "campaign": "Oakhaven",
        "npc_context": {"Halda": {"hp": 12}, "Veyra": {"hp": 30}},
    })

    assert "Halda" in reinforcer.world_summary
    assert "12" in reinforcer.world_summary
    assert "Veyra" in reinforcer.world_summary


def test_empty_npc_context_adds_nothing():
    out = _summary(GameState(campaign="Oakhaven"))

    assert "NPC:" not in out


# ── the fields that already worked ────────────────────────────────────────

def test_campaign_session_and_scene_still_render():
    out = _summary(_fighting())

    assert "**Campaign:** Oakhaven" in out
    assert "**Session:** 4" in out
    assert "**Current Scene:** The Sunken Chapel" in out
