"""Campaign-switch state reset, usage-scope isolation, and reply hardening."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from llm.manager import LLMManager


def _manager():
    m = LLMManager()
    m._rate_lock = asyncio.Lock()
    return m


def test_reset_campaign_state_drops_canon_house_rules_and_history():
    m = _manager()
    m.set_dynamic_canon_context("CANON A")
    m.set_dynamic_house_rules_context("RULES A")
    m._conversation_history.append({"role": "user", "content": "from A"})
    assert "CANON A" in m.system_prompt

    m.reset_campaign_state()

    assert "CANON A" not in m.system_prompt
    assert m._dynamic_house_rules_context == ""
    assert m.conversation_history == []


def test_usage_scope_is_per_task_and_leaves_shared_context_alone():
    m = _manager()
    m.set_usage_context("live", "camp")
    seen = {}

    async def scoped():
        with m.usage_scope("s2", "other"):
            await asyncio.sleep(0)
            seen["scoped"] = m.usage_context

    async def plain():
        await asyncio.sleep(0)
        seen["plain"] = m.usage_context

    async def run():
        await asyncio.gather(scoped(), plain())

    asyncio.run(run())
    assert seen == {"scoped": ("s2", "other"), "plain": ("live", "camp")}
    assert m.usage_context == ("live", "camp")


def _http(m, content):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"choices": [{"message": {"content": content}}]})
    m._http = MagicMock()
    m._http.post = AsyncMock(return_value=resp)


def test_usage_recording_failure_does_not_discard_the_reply():
    m = _manager()
    _http(m, '{"actions": [{"type": "narrate", "text": "hi"}]}')
    m._usage = MagicMock()
    m._usage.before_call = AsyncMock()
    m._usage.record = AsyncMock(side_effect=RuntimeError("db down"))

    result = asyncio.run(m.generate("hello"))

    assert result["actions"][0]["text"] == "hi"
    assert m._http.post.await_count == 1  # not re-issued


def test_json_without_actions_key_is_a_parse_failure():
    m = _manager()
    _http(m, '{"narration": "no actions here"}')

    result = asyncio.run(m.generate("hello"))

    assert m._http.post.await_count == 2  # retried like any unparseable reply
    assert "gathering the threads" in result["actions"][0]["text"]
