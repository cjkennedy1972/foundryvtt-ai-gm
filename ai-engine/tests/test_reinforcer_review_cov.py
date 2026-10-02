"""Behavioral coverage for context/reinforcer.py (review pass)."""

from enum import Enum

from context.reinforcer import ContextReinforcer


class _Mode(Enum):
    COMBAT = "combat"


def test_empty_reinforcer_emits_nothing():
    assert ContextReinforcer().get_reinforcement() == ""
    assert ContextReinforcer().get_anchor_facts() == []


def test_reinforcement_sections_in_order():
    r = ContextReinforcer(anchor_facts=["Fact A"], npc_summary="- Mira", world_summary="Krynn",
                          active_players=["Alice", "Bob"])
    out = r.get_reinforcement(active_state={"in_combat": False}, extra_context="EXTRA")
    assert out.startswith("## CONTEXT ANCHOR ##\n## ANCHORED CONTEXT ##")
    order = [out.index(s) for s in ("- Fact A", "## ACTIVE NPCs ##", "- Mira", "## WORLD CONTEXT ##",
                                    "Krynn", "## CURRENT GAME STATE ##", "Combat: Not active",
                                    "## ACTIVE PLAYERS ##", "- Alice", "- Bob", "EXTRA")]
    assert order == sorted(order)
    assert r.get_anchor_facts() == ["Fact A"]


def test_format_state_combat_scene_time_and_nearby():
    r = ContextReinforcer()
    out = r._format_state({
        "in_combat": True, "combat_round": 3,
        "combat_combatants": [{"name": f"C{i}"} for i in range(12)] + [{}],
        "scene": {"name": "Crypt", "x": 4, "y": 5}, "time_of_day": "dusk",
        "nearby_npcs": [{"name": f"N{i}"} for i in range(7)],
    })
    assert "Combat: Active (round 3)" in out
    assert "Combatants: C0, C1, C2, C3, C4, C5, C6, C7, C8, C9\n" in out  # capped at 10
    assert "Location: Crypt" in out and "Position: (4, 5)" in out and "Time: dusk" in out
    assert "Nearby NPCs: N0, N1, N2, N3, N4\n" in out + "\n"


def test_format_state_combat_without_combatants_has_no_bare_label():
    out = ContextReinforcer()._format_state({"in_combat": True})
    assert "round ?" in out and "Combatants" not in out
    out2 = ContextReinforcer()._format_state({"scene": {"name": "Hall", "x": 0, "y": 9}})
    assert "Position" not in out2  # a zero coordinate means unplaced


def test_update_npc_summary_and_empty():
    r = ContextReinforcer()
    r.update_npc_summary([{"name": "Orc", "hp": 7, "x": 1, "y": 2}, {}])
    assert r.npc_summary == "- **Orc** (HP: 7, Pos: 1,2)\n- **Unknown** (HP: ?, Pos: ?,?)"
    r.update_npc_summary([])
    assert r.npc_summary == "No NPCs in combat"


def test_update_world_summary_mode_combat_and_npc_context_shapes():
    r = ContextReinforcer()
    r.update_world_summary({
        "mode": _Mode.COMBAT, "current_scene": "Crypt", "campaign": "Krynn", "session_number": 4,
        "combat": {"in_combat": True, "round": 2, "turn": 1, "turn_order": ["a", "b"]},
        "npc_context": {"Mira": {"hp": 5}, "Bob": "str-not-dict"},
    }, scene_data="SCENE")
    s = r.world_summary
    assert "**Mode:** combat" in s and "GameMode" not in s
    assert "**Campaign:** Krynn" in s and "**Session:** 4" in s and "**Current Scene:** Crypt" in s
    assert "**Combat:** Round 2, Turn 1" in s and "**Turn Order:** 2 combatants" in s
    assert "**NPC:** Mira (HP: 5)" in s and "Bob" not in s
    assert s.endswith("\n\nSCENE")
    r.update_world_summary({"npc_context": "A goblin lurks"})
    assert "**NPCs:** A goblin lurks" in r.world_summary and "**Mode:** exploration" in r.world_summary
    r.update_world_summary({}, scene_data="only scene")
    assert r.world_summary == "\nonly scene"
