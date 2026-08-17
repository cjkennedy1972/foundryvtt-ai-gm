#!/usr/bin/env python3
"""Tests for LLMManager's per-instance model override — the plumbing
that lets ModelRouter (llm/router.py) actually route to a distinct model
for NPC turns instead of only the global settings.model.

Run:
    cd ai-engine && python -m pytest tests/test_llm_manager_model_override.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from llm.manager import LLMManager


def test_defaults_to_global_settings_model():
    mgr = LLMManager()
    assert mgr.model == (settings.model or "mlx-model")


def test_explicit_model_overrides_global_settings():
    mgr = LLMManager(model="tiny-npc-model")
    assert mgr.model == "tiny-npc-model"


def test_two_instances_can_have_different_models():
    frontier = LLMManager()
    npc = LLMManager(model="tiny-npc-model")
    assert frontier.model != npc.model
    assert npc.model == "tiny-npc-model"
