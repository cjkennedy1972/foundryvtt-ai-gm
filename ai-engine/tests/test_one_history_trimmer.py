#!/usr/bin/env python3
"""There was one place trimming conversation history, and a second class that
looked like it and did nothing.

ContextWindowManager was constructed in startup, published on app.state and
typed on AppState, and its add_message, add_system and get_context_messages
had no callers anywhere. Its _trim never ran. settings.max_context_tokens
fed both it and LLMManager._trim_history, so the setting looked like it
drove the dead one.

Deleted. LLMManager._trim_history is the live path and, since the budget fix,
accounts for everything a turn sends.

Run:
    cd ai-engine && python -m pytest tests/test_one_history_trimmer.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_there_is_no_second_unfed_context_manager():
    with pytest.raises(ImportError):
        import context.window_manager  # noqa: F401


def test_app_state_does_not_publish_one():
    from api.deps import AppState

    assert not hasattr(AppState(), "context_manager")


def test_the_context_setting_drives_the_trimmer_that_runs():
    """max_context_tokens fed the dead class too; it has to reach the live one."""
    from unittest.mock import patch

    from config import settings

    with patch.object(settings, "max_context_tokens", 12345):
        from llm.manager import LLMManager
        manager = LLMManager()

    assert manager._max_history_tokens == 12345


def test_the_trimmer_actually_trims():
    from llm.manager import LLMManager

    manager = LLMManager()
    manager._max_history_tokens = 4000
    manager._max_tokens = 500
    manager._custom_system_prompt = "system"
    manager._system_prompt_cache = "system"
    manager._conversation_history = [
        {"role": "user", "content": "word " * 500} for _ in range(50)
    ]

    manager._trim_history()

    assert len(manager._conversation_history) < 50
