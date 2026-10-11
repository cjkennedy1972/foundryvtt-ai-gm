"""LLM replies that arrive with empty content and the JSON in the reasoning field."""

from llm.manager import _reply_text


def _resp(content, **extra):
    return {"choices": [{"message": {"role": "assistant", "content": content, **extra}, "finish_reason": "stop"}]}


def test_content_is_used_when_present():
    assert _reply_text(_resp('{"narration": "hi"}', reasoning="thinking...")) == '{"narration": "hi"}'


def test_reasoning_is_used_when_content_is_empty():
    data = _resp("", reasoning='{"narration": "The bastion looms."}')
    assert _reply_text(data) == '{"narration": "The bastion looms."}'


def test_reasoning_content_key_is_also_read():
    data = _resp("", reasoning_content='{"actions": []}')
    assert _reply_text(data) == '{"actions": []}'


def test_whitespace_content_counts_as_empty():
    assert _reply_text(_resp("   \n", reasoning='{"ok": true}')) == '{"ok": true}'


def test_truly_empty_reply_stays_empty():
    assert _reply_text(_resp("", reasoning="")) == ""
    assert _reply_text(_resp(None)) == ""
