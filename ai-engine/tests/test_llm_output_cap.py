#!/usr/bin/env python3
"""
Regression test: the LLM output-token reservation is bounded and configurable.

A hardcoded 8192 output reservation collided with small model context windows —
prompt + max_tokens exceeded n_ctx and the server returned 400 ~half the time,
stalling the encounter. _max_tokens must come from settings.llm_max_output_tokens
(default 2048), not a giant constant.

Run:
    cd ai-engine && python -m pytest tests/test_llm_output_cap.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from llm.manager import LLMManager


def test_default_output_cap_is_small():
    assert settings.llm_max_output_tokens <= 4096


def test_manager_uses_configured_cap():
    mgr = LLMManager()
    assert mgr._max_tokens == (settings.llm_max_output_tokens or 2048)
    assert mgr._max_tokens != 8192


if __name__ == "__main__":
    test_default_output_cap_is_small()
    test_manager_uses_configured_cap()
    print("All LLM output-cap tests passed.")
