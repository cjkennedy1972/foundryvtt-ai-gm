"""Tests for utils.token_counter — the single source of truth for LLM token
budget math. A drift here silently corrupts context-window trimming, so the
arithmetic is pinned."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import token_counter as tc


def test_estimate_tokens_empty_is_zero():
    assert tc.estimate_tokens("") == 0


def test_estimate_tokens_minimum_one():
    # Short non-empty text still costs at least one token.
    assert tc.estimate_tokens("a") == 1


def test_estimate_tokens_uses_four_chars_per_token():
    assert tc.estimate_tokens("a" * 40) == 10


def test_estimate_message_tokens_includes_role_overhead():
    msgs = [{"role": "user", "content": "a" * 40}]  # 10 content + 8 overhead
    assert tc.estimate_message_tokens(msgs) == 18


def test_estimate_message_tokens_missing_content():
    assert tc.estimate_message_tokens([{"role": "user"}]) == 8


def test_estimate_system_prompt_adds_framing_overhead():
    assert tc.estimate_system_prompt_tokens("a" * 40) == 60  # 10 + 50


def test_available_budget_subtracts_all_reserves():
    budget = tc.calculate_available_budget(
        max_context_tokens=32000, max_output_tokens=8192,
        system_prompt="", reserved_tokens=500)
    # system_prompt "" -> estimate 0 + 50 framing
    assert budget == 32000 - 8192 - 50 - 500


def test_available_budget_never_negative():
    assert tc.calculate_available_budget(
        max_context_tokens=100, max_output_tokens=8192) == 0


def test_trim_empty_returns_empty():
    assert tc.trim_messages_to_budget([], budget=100) == []


def test_trim_keeps_system_and_most_recent():
    msgs = [
        {"role": "system", "content": "s" * 40},   # 10 + 8 overhead when counted
        {"role": "user", "content": "old" * 40},
        {"role": "assistant", "content": "recent"},
    ]
    out = tc.trim_messages_to_budget(msgs, budget=40, always_keep_system=True)
    assert out[0]["role"] == "system"
    # The most recent message survives; the oldest large one is dropped first.
    assert out[-1]["content"] == "recent"


def test_trim_can_drop_system_when_disabled():
    msgs = [{"role": "system", "content": "s" * 400}]
    out = tc.trim_messages_to_budget(msgs, budget=5, always_keep_system=False)
    assert out == []


def test_calculate_turn_tokens_counts_both_roles():
    # 10 + 8 (user) + 10 + 8 (assistant)
    assert tc.calculate_turn_tokens("u" * 40, "a" * 40) == 36
