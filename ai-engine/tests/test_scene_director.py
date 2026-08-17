#!/usr/bin/env python3
"""Tests for orchestrator.director.SceneDirector.

Run:
    cd ai-engine && python -m pytest tests/test_scene_director.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npc.goals import Goal
from npc.registry import NPCRegistry
from orchestrator.director import Candidate, SceneDirector


def _candidate(reg, npc_id, name, priority):
    reg.register_npc(npc_id, name, "desc")
    npc = reg.get_npc(npc_id)
    goal = Goal(description=f"{name}'s goal", priority=priority, status="active")
    npc.goals.append(goal)
    return Candidate(npc=npc, matched_goals=[goal])


def test_no_candidates_returns_none():
    director = SceneDirector()
    assert director.next_turn([]) is None


def test_single_candidate_passes_through_unchanged():
    reg = NPCRegistry()
    c = _candidate(reg, "n1", "Mara", priority=0)
    director = SceneDirector()
    assert director.next_turn([c]) is c


def test_highest_priority_wins():
    reg = NPCRegistry()
    low = _candidate(reg, "n1", "Mara", priority=1)
    high = _candidate(reg, "n2", "Kael", priority=10)
    director = SceneDirector()
    assert director.next_turn([low, high]) is high


def test_tie_keeps_first_in_list():
    reg = NPCRegistry()
    first = _candidate(reg, "n1", "Mara", priority=5)
    second = _candidate(reg, "n2", "Kael", priority=5)
    director = SceneDirector()
    assert director.next_turn([first, second]) is first
