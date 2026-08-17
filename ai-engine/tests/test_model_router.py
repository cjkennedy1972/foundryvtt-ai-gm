#!/usr/bin/env python3
"""Tests for llm.router.ModelRouter.

Run:
    cd ai-engine && python -m pytest tests/test_model_router.py -v
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.router import ModelRouter


def test_defaults_npc_tier_to_frontier_when_no_second_model():
    frontier = MagicMock(name="frontier-llm")
    router = ModelRouter(frontier)
    assert router.get("frontier") is frontier
    assert router.get("npc") is frontier


def test_uses_distinct_npc_model_when_configured():
    frontier = MagicMock(name="frontier-llm")
    npc = MagicMock(name="npc-llm")
    router = ModelRouter(frontier, npc=npc)
    assert router.get("npc") is npc
    assert router.get("frontier") is frontier


def test_unknown_tier_falls_back_to_frontier():
    frontier = MagicMock(name="frontier-llm")
    router = ModelRouter(frontier)
    assert router.get("something-made-up") is frontier
