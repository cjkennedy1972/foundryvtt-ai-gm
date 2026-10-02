"""A streamed prose reply (no JSON) must not raise; history stores it as a narrate action."""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.manager import LLMManager


class _FakeStream:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        yield "data: " + json.dumps({"choices": [{"delta": {"content": self._text}}]})
        yield "data: [DONE]"


@pytest.mark.asyncio
async def test_prose_stream_is_stored_as_narrate_action():
    mgr = LLMManager()
    mgr._http = MagicMock()
    mgr._http.stream = lambda *a, **k: _FakeStream("The party settles in for the night.")

    chunks = [c async for c in mgr.generate_stream("I rest for the night.")]

    assert "".join(chunks) == "The party settles in for the night."
    stored = json.loads(mgr._conversation_history[-1]["content"])
    assert stored == {"actions": [{"type": "narrate", "text": "The party settles in for the night."}]}


@pytest.mark.asyncio
async def test_stream_requests_json_object_format():
    mgr = LLMManager()
    seen = {}
    mgr._http = MagicMock()

    def fake_stream(method, url, json=None, **k):
        seen.update(json)
        return _FakeStream('{"actions": []}')

    mgr._http.stream = fake_stream
    [c async for c in mgr.generate_stream("hello")]
    assert seen["response_format"] == {"type": "json_object"}
