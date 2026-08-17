#!/usr/bin/env python3
"""Tests for npc.goals.Goal and NPCRegistry's goal-management methods.

Run:
    cd ai-engine && python -m pytest tests/test_npc_goals.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npc.goals import Goal
from npc.registry import NPCRegistry


def test_new_npc_has_no_goals():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight")
    assert reg.get_npc("n1").goals == []
    assert reg.get_active_goals("n1") == []


def test_add_goal_and_get_active_sorted_by_priority():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight")
    reg.add_goal("n1", Goal(description="patrol the wall", priority=1))
    reg.add_goal("n1", Goal(description="seek revenge on the party", priority=10))

    active = reg.get_active_goals("n1")
    assert [g.description for g in active] == ["seek revenge on the party", "patrol the wall"]


def test_done_goals_excluded_from_active():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight")
    reg.add_goal("n1", Goal(description="deliver the letter", status="done"))
    reg.add_goal("n1", Goal(description="patrol the wall", status="pending"))

    active = reg.get_active_goals("n1")
    assert len(active) == 1
    assert active[0].description == "patrol the wall"


def test_add_goal_to_unknown_npc_returns_false():
    reg = NPCRegistry()
    assert reg.add_goal("nope", Goal(description="x")) is False


def test_goal_matches_event_type_only():
    goal = Goal(description="advance", trigger_conditions={"event_type": "time_advanced"})
    assert goal.matches({"type": "time_advanced", "payload": {}})
    assert not goal.matches({"type": "npc_moved", "payload": {}})


def test_goal_matches_requires_all_conditions():
    goal = Goal(
        description="react to the party",
        trigger_conditions={"event_type": "action_resolved", "target_id": "pc-1"},
    )
    assert goal.matches({"type": "action_resolved", "payload": {"target_id": "pc-1"}})
    assert not goal.matches({"type": "action_resolved", "payload": {"target_id": "pc-2"}})
    assert not goal.matches({"type": "action_resolved", "payload": {}})


def test_goal_with_no_trigger_never_matches():
    goal = Goal(description="idle goal")
    assert not goal.matches({"type": "time_advanced", "payload": {}})
