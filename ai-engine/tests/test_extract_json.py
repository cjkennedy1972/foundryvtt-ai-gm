"""Unit tests for LLMManager._extract_json method.

These tests cover the JSON extraction logic that handles:
1. JSON wrapped in ```json ... ``` code blocks
2. JSON wrapped in ``` ... ``` plain code blocks
3. JSON with thinking text before/after
4. Multiple JSON objects (should pick the last valid one)
5. Truncated/invalid JSON (should raise ValueError)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.manager import LLMManager


def _make_manager():
    """Create an LLMManager instance for testing (without HTTP client)."""
    import httpx
    mgr = LLMManager.__new__(LLMManager)
    mgr._http = httpx.AsyncClient()
    mgr._endpoint_url = "http://test/v1/chat/completions"
    mgr.model = "test-model"
    mgr._temperature = 0.7
    mgr._max_tokens = 2048
    mgr._conversation_history = []
    mgr._max_history_tokens = 50000
    mgr._reinforcer = None
    mgr._turn_count = 0
    mgr._system_prompt_cache = "Test system prompt"
    return mgr


def test_extract_json_from_json_code_block():
    """JSON inside ```json ... ``` should be extracted and returned."""
    mgr = _make_manager()
    text = '''Some thinking text...
```json
{"actions": [{"type": "narrate", "text": "Hello"}]}
```
More text after.'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["type"] == "narrate"
    assert parsed["actions"][0]["text"] == "Hello"


def test_extract_json_from_plain_code_block():
    """JSON inside ``` ... ``` (no language) should be extracted."""
    mgr = _make_manager()
    text = '''Thinking...
```
{"actions": [{"type": "speak", "npc_name": "Bob", "text": "Hi"}]}
```'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["type"] == "speak"
    assert parsed["actions"][0]["npc_name"] == "Bob"


def test_extract_json_picks_last_valid_block():
    """When multiple code blocks exist, the LAST valid one should win."""
    mgr = _make_manager()
    text = '''First attempt (invalid):
```json
{not valid json}
```

Second attempt (valid):
```json
{"actions": [{"type": "narrate", "text": "Second"}]}
```'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == "Second"


def test_extract_json_with_thinking_text_before():
    """Thinking text before JSON code block should not interfere."""
    mgr = _make_manager()
    text = '''Let me think about this...
The player said hello, so I should respond.
I need to output valid JSON.
```json
{"actions": [{"type": "narrate", "text": "The GM nods."}]}
```'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == "The GM nods."


def test_extract_json_with_thinking_text_after():
    """Thinking text after JSON code block should not interfere."""
    mgr = _make_manager()
    text = '''```json
{"actions": [{"type": "narrate", "text": "Done"}]}
```
This was the correct response.'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == "Done"


def test_extract_json_balanced_brace_fallback():
    """When no code blocks, balanced-brace counting should find JSON."""
    mgr = _make_manager()
    text = 'Some text {"actions": [{"type": "narrate", "text": "Found"}]} more text'
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == "Found"


def test_extract_json_brace_count_ignores_string_braces():
    """Braces inside string literals should not affect brace counting."""
    mgr = _make_manager()
    text = '''{"actions": [{"type": "narrate", "text": "Has {braces} in text"}]}'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == "Has {braces} in text"


def test_extract_json_raises_on_no_valid_json():
    """Should raise ValueError when no valid JSON can be found."""
    mgr = _make_manager()
    text = "Just some text with no JSON at all"
    try:
        mgr._extract_json(text)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "No valid JSON object found" in str(e)


def test_extract_json_raises_on_truncated_json():
    """Should raise ValueError on truncated/incomplete JSON."""
    mgr = _make_manager()
    text = '{"actions": [{"type": "narrate"'
    try:
        mgr._extract_json(text)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "No valid JSON object found" in str(e)


def test_extract_json_handles_escaped_quotes_in_strings():
    """Escaped quotes inside strings should not break string detection."""
    mgr = _make_manager()
    text = '{"actions": [{"type": "narrate", "text": "He said \\"Hello\\""}]}'
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == 'He said "Hello"'


def test_extract_json_multiple_objects_picks_last():
    """When multiple valid JSON objects exist, pick the last one (most complete)."""
    mgr = _make_manager()
    text = 'First {"actions": [{"type": "narrate", "text": "First"}]} Second {"actions": [{"type": "narrate", "text": "Second"}]}'
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    # Should pick the last complete JSON object
    assert parsed["actions"][0]["text"] == "Second"


def test_extract_json_nested_objects():
    """Nested objects in the JSON should be handled correctly."""
    mgr = _make_manager()
    text = '''```json
{"actions": [{"type": "setup_scene", "scene_name": "Test", "walls": [{"c": [0,0,10,10]}]}]}
```'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["type"] == "setup_scene"
    assert parsed["actions"][0]["walls"][0]["c"] == [0, 0, 10, 10]


def test_extract_json_falls_back_past_invalid_fenced_block():
    """An invalid/truncated fenced block must not corrupt the brace-counting
    fallback that follows it — the fence is stripped before brace-counting
    runs, so the unmatched '{' inside it can't poison the whole scan."""
    mgr = _make_manager()
    text = '''Thinking about this...
```json
{not valid json at all
```
Actually, here is my response: {"actions": [{"type": "narrate", "text": "Fallback worked"}]}'''
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == "Fallback worked"


def test_extract_json_handles_single_line_code_block():
    """Single-line code blocks should work."""
    mgr = _make_manager()
    text = '```json\n{"actions": [{"type": "narrate", "text": "One line"}]}\n```'
    result = mgr._extract_json(text)
    parsed = json.loads(result)
    assert parsed["actions"][0]["text"] == "One line"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

def test_context_system_messages_use_real_newlines():
    """The game-state and extra-context framing must not emit a literal \\n.

    Both were written inside an f-string as "\\\\n", so every turn sent the
    model the two characters backslash-n instead of a line break.
    """
    from llm.manager import LLMManager

    mgr = LLMManager()
    messages = mgr._build_prompt_messages(
        user_message="hello",
        game_state_summary="mode: exploration",
        extra_context="npc: Sage",
        include_history=False,
        include_reinforcement=False,
    )
    framing = [m["content"] for m in messages if m["role"] == "system"]
    assert any(c.startswith("CURRENT GAME STATE:\n") for c in framing)
    assert any(c.startswith("ADDITIONAL CONTEXT:\n") for c in framing)
    assert not any("\\n" in c for c in framing)
