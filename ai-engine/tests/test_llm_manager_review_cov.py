"""Behavioral coverage for llm/manager.py and llm/usage.py (review pass).

The HTTP client is replaced; every assertion is on what the model is sent and
on how the manager reacts to what comes back.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from config import settings
from llm.manager import LLMManager, _template_safe
from llm.usage import TokenBudgetExceeded, TokenUsage, Usage


@pytest.fixture(autouse=True)
def _pin_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_min_call_interval", 0)
    monkeypatch.setattr(settings, "llm_max_output_tokens", 100)
    monkeypatch.setattr(settings, "max_context_tokens", 50000)
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "ai_tone", "")


class FakeResp:
    def __init__(self, body=None, status=200, text=""):
        self._body, self.status_code, self.text = body, status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=self)

    def json(self):
        return self._body


def _chat(content, usage=None):
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return FakeResp(body)


class FakeHTTP:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    async def post(self, url, json=None, timeout=None):
        self.requests.append({"url": url, "json": json})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def aclose(self):
        pass


class FakeDB:
    def __init__(self, used=0):
        self.used, self.recorded = used, []

    async def get_llm_usage_total(self, session_id=None):
        return self.used

    async def record_llm_usage(self, *args):
        self.recorded.append(args)


def mgr(responses=(), **kw):
    m = LLMManager(**kw)
    m._http = FakeHTTP(responses)
    m._conversation_history = []
    return m


# --- usage.py ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_usage_budget_boundaries_and_exhaustion_hook():
    fired = []

    async def on_exhausted(err):
        fired.append(err)

    t = TokenUsage(FakeDB(used=900), budget=1000, on_exhausted=on_exhausted)
    await t.before_call("s", "c", 100)  # exactly at the budget is allowed
    with pytest.raises(TokenBudgetExceeded) as ei:
        await t.before_call("s", "c", 101)
    assert (ei.value.used, ei.value.requested, ei.value.budget) == (900, 101, 1000)
    assert "1,001/1,000" in str(ei.value) and fired == [ei.value]
    assert await t.budget_available("s") is True
    assert await TokenUsage(FakeDB(used=1000), budget=1000).budget_available("s") is False
    # no budget / no session = unlimited
    await TokenUsage(FakeDB(used=10 ** 9), budget=0).before_call("s", "c", 5)
    await TokenUsage(FakeDB(used=10 ** 9), budget=10).before_call(None, "c", 5)
    assert await TokenUsage(FakeDB(used=10 ** 9), budget=10).budget_available(None) is True
    assert TokenUsage(FakeDB(), budget=-5).budget == 0


@pytest.mark.asyncio
async def test_token_usage_record_only_with_session():
    db = FakeDB()
    t = TokenUsage(db)
    await t.record(None, "c", Usage(1, 2), "m")
    assert db.recorded == []
    await t.record("s", "c", Usage(3, 4), "m", "text")
    assert db.recorded == [("s", "c", 3, 4, "m", "text")]
    assert Usage(3, 4).total_tokens == 7


# --- _template_safe ---------------------------------------------------------

def test_template_safe_folds_leading_system_and_demotes_later_ones():
    out = _template_safe([
        {"role": "system", "content": "A"}, {"role": "system", "content": "B"},
        {"role": "user", "content": "hi"}, {"role": "system", "content": "reinforce"},
        {"role": "user", "content": "go"},
    ])
    assert out == [
        {"role": "system", "content": "A\n\nB"}, {"role": "user", "content": "hi"},
        {"role": "user", "content": "[System note]\nreinforce"}, {"role": "user", "content": "go"},
    ]
    assert _template_safe([{"role": "user", "content": "x"}]) == [{"role": "user", "content": "x"}]


# --- construction / context -------------------------------------------------

class _Loader:
    def __init__(self):
        self.queries = []

    def search_vault(self, query, max_results=5):
        self.queries.append((query, max_results))
        return [f"lore for {query}"]

    def get_npc_context_sync(self):
        return "LOADER NPCS"

    def get_world_context_sync(self):
        return "LOADER WORLD"

    def get_house_rules_context_sync(self):
        return ""

    def get_canon_context_sync(self):
        return ""


def test_endpoint_model_and_overrides():
    m = LLMManager(model="my-model")
    assert m._endpoint_url == "http://llm.test/v1/chat/completions?thinking=false"
    assert m.model == "my-model"
    assert m._max_tokens == 100


def test_scene_change_refreshes_anchor_facts_from_scene_and_npcs():
    loader = _Loader()
    m = mgr(campaign_loader=loader)
    assert m._build_anchor_facts() == []  # no scene, no npcs: no query
    m.set_dynamic_npc_context("N" * 400)
    m.set_current_scene("Crypt")
    assert loader.queries[-1] == ("Crypt " + "N" * 300, 5)  # NPC text capped at 300 chars
    assert m._reinforcer.anchor_facts == {"lore for Crypt " + "N" * 300}
    m.set_current_scene(None)
    assert m._current_scene == ""
    assert LLMManager()._build_anchor_facts() == []


@pytest.mark.asyncio
async def test_update_context_pushes_npc_world_and_anchor():
    loader = _Loader()
    m = mgr(campaign_loader=loader)
    m.set_current_scene("Hall")
    await m.update_context(npc_data=[{"name": "Orc", "hp": 3}], world_data={"campaign": "K"})
    assert "Orc" in m._reinforcer.npc_summary and "**Campaign:** K" in m._reinforcer.world_summary
    assert m._reinforcer.anchor_facts == {"lore for Hall"}
    await m.update_context()  # nothing to push still refreshes anchors, no crash


def test_system_prompt_dynamic_context_outranks_loader_and_cache_invalidates():
    m = mgr(campaign_loader=_Loader())
    assert "LOADER NPCS" in m.system_prompt and "LOADER WORLD" in m.system_prompt
    first = m.system_prompt
    assert m.system_prompt is first  # cached
    m.set_dynamic_npc_context("LIVE NPCS")
    assert "LIVE NPCS" in m.system_prompt and "LOADER NPCS" not in m.system_prompt
    m.set_dynamic_house_rules_context("HR-X")
    m.set_dynamic_canon_context("CANON-X")
    m.set_dynamic_world_context("LIVE WORLD")
    p = m.system_prompt
    assert all(s in p for s in ("HR-X", "CANON-X", "LIVE WORLD"))
    m.set_system_prompt("CUSTOM")
    assert m.system_prompt == "CUSTOM"
    m._system_prompt_cache = None
    assert m.system_prompt == "CUSTOM"  # a custom prompt survives cache invalidation


def test_refresh_campaign_context_drops_scene_copies_keeps_live_edits():
    m = mgr(campaign_loader=_Loader())
    m.set_dynamic_npc_context("old copy")
    m.set_dynamic_world_context("old copy")
    m.set_dynamic_canon_context("live canon")
    m.refresh_campaign_context()
    assert m._dynamic_npc_context == "" and m._dynamic_world_context == ""
    assert m._dynamic_canon_context == "live canon"
    assert "LOADER NPCS" in m.system_prompt


def test_set_active_modules_invalidates_prompt():
    m = mgr()
    _ = m.system_prompt
    m.set_active_modules(None)
    assert m._active_modules == [] and m._system_prompt_cache is None


# --- prompt assembly / history ----------------------------------------------

def test_prompt_messages_order_and_reinforcement_every_third_turn():
    m = mgr()
    m._reinforcer.anchor_facts = {"Fact A"}
    seen = []
    for i in range(3):
        msgs = m._build_prompt_messages(f"u{i}", game_state_summary="GS", extra_context="EC")
        seen.append(msgs)
    assert [x["role"] for x in seen[0]] == ["system", "system", "system", "user"]
    assert seen[0][1]["content"] == "CURRENT GAME STATE:\nGS"
    assert seen[0][2]["content"] == "ADDITIONAL CONTEXT:\nEC"
    assert not any("CONTEXT ANCHOR" in x["content"] for msgs in seen[:2] for x in msgs)
    assert any("CONTEXT ANCHOR" in x["content"] and "Fact A" in x["content"] for x in seen[2])
    m2 = mgr()
    m2._reinforcer.anchor_facts = {"F"}
    for _ in range(3):
        m2._build_prompt_messages("u", _skip_turn_increment=True)
    assert m2._turn_count == 0  # skipped turns don't advance the reinforcement clock


def test_trim_history_budget_arithmetic(monkeypatch):
    m = mgr()
    m.set_system_prompt("x" * 60)
    m._conversation_history = [{"role": "user", "content": f"m{i}"} for i in range(6)]
    seen = {}

    def fake_trim(history, budget, always_keep_system):
        seen.update(budget=budget, keep=always_keep_system)
        return history[-1:]

    monkeypatch.setattr("llm.manager.trim_messages_to_budget", fake_trim)
    from utils.token_counter import estimate_tokens
    m._trim_history(reserved=40)
    assert seen["budget"] == 50000 - 100 - (estimate_tokens("x" * 60) + 50) - 40 - 500
    assert seen["keep"] is False and len(m._conversation_history) == 1
    m._trim_history(reserved=-999)  # negative reservations never add budget
    assert seen["budget"] == 50000 - 100 - (estimate_tokens("x" * 60) + 50) - 500


def test_trim_history_when_prompt_overflows_keeps_last_two():
    m = mgr()
    m._max_history_tokens = 10
    m._conversation_history = [{"role": "user", "content": str(i)} for i in range(5)]
    m._trim_history()
    assert [h["content"] for h in m._conversation_history] == ["3", "4"]
    m._conversation_history = [{"role": "user", "content": "only"}]
    m._trim_history()
    assert len(m._conversation_history) == 1


@pytest.mark.asyncio
async def test_remember_combat_and_restore_history():
    m = mgr()
    await m.remember_combat("   ")
    await m.remember_combat(None)
    assert m.conversation_history == []
    await m.remember_combat("  The orcs fled.  ")
    assert m.conversation_history == [
        {"role": "user", "content": "[Combat resolved]"},
        {"role": "assistant", "content": "The orcs fled."},
    ]
    await m.restore_history([{"role": "user", "content": "ignored"}])  # play already started
    assert m.conversation_history[0]["content"] == "[Combat resolved]"
    fresh = mgr()
    await fresh.restore_history([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    assert [h["content"] for h in fresh.conversation_history] == ["a", "b"]
    fresh.conversation_history.append("x")  # the property returns a copy
    assert len(fresh.conversation_history) == 2


@pytest.mark.asyncio
async def test_rate_limit_waits_for_the_minimum_gap(monkeypatch):
    monkeypatch.setattr(settings, "llm_min_call_interval", 5.0)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("llm.manager.asyncio.sleep", fake_sleep)
    clock = iter([100.0, 100.0, 101.0, 101.0])
    monkeypatch.setattr("llm.manager.time", SimpleNamespace(monotonic=lambda: next(clock)))
    m = mgr()
    m._last_call_time = 98.0
    await m._acquire_rate_limit()  # 2s since last call, 3s still owed
    assert slept == [3.0]
    await m._acquire_rate_limit()   # 1s since the stamp at 100.0, 4s owed
    assert slept == [3.0, 4.0]


# --- generate ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_sends_json_mode_payload_and_persists_history():
    m = mgr([_chat('Thinking... {"actions": [{"type": "narrate", "text": "Hi"}]}')])
    res = await m.generate("I wave", game_state_summary="GS")
    assert res == {"actions": [{"type": "narrate", "text": "Hi"}]}
    req = m._http.requests[0]
    assert req["url"].endswith("?thinking=false")
    p = req["json"]
    assert p["response_format"] == {"type": "json_object"} and p["max_tokens"] == 100
    assert p["messages"][-1] == {"role": "user", "content": "I wave"}
    assert [x["role"] for x in p["messages"]].count("system") == 1  # folded for strict templates
    assert [h["role"] for h in m.conversation_history] == ["user", "assistant"]
    assert json.loads(m.conversation_history[1]["content"]) == res


@pytest.mark.asyncio
async def test_generate_without_history_flags_leaves_history_alone():
    m = mgr([_chat('{"actions": []}')])
    m._conversation_history = [{"role": "user", "content": "OLD"}, {"role": "assistant", "content": "OLD2"}]
    await m.generate("npc turn", include_history=False, persist_history=False)
    sent = m._http.requests[0]["json"]["messages"]
    assert all(x["content"] != "OLD" for x in sent)
    assert len(m.conversation_history) == 2


@pytest.mark.asyncio
async def test_generate_retries_with_corrective_user_nudge_then_succeeds():
    m = mgr([_chat("not json at all"), _chat('{"actions": [{"type": "narrate", "text": "ok"}]}')])
    res = await m.generate("go")
    assert res["actions"][0]["text"] == "ok"
    retry = m._http.requests[1]["json"]["messages"]
    assert retry[-1]["role"] == "user" and "not valid JSON" in retry[-1]["content"]
    assert retry[-2] == {"role": "user", "content": "go"}
    # the nudge is not kept in history
    assert all("not valid JSON" not in h["content"] for h in m.conversation_history)


@pytest.mark.asyncio
async def test_generate_falls_back_to_neutral_narration_after_two_bad_replies():
    m = mgr([_chat("prose"), _chat("more prose")])
    res = await m.generate("go")
    assert res["actions"][0]["type"] == "narrate" and "gathering the threads" in res["actions"][0]["text"]
    assert len(m._http.requests) == 2
    assert [h["role"] for h in m.conversation_history] == ["user", "assistant"]
    m2 = mgr([_chat("x"), _chat("y")])
    await m2.generate("go", persist_history=False)
    assert m2.conversation_history == []


@pytest.mark.asyncio
async def test_generate_http_error_raises_and_duplicate_errors_are_suppressed(caplog):
    resp = FakeResp(status=400, text="context length exceeded")
    m = mgr([resp, FakeResp(status=400, text="again")])
    with caplog.at_level("DEBUG", logger="llm.manager"):
        with pytest.raises(httpx.HTTPStatusError):
            await m.generate("go")
        assert any("body: context length exceeded" in r.getMessage() for r in caplog.records)
        caplog.clear()
        with pytest.raises(httpx.HTTPStatusError):
            await m.generate("go")
        assert any("Suppressed duplicate" in r.getMessage() for r in caplog.records)
        assert not any(r.levelname == "ERROR" for r in caplog.records)
    assert m.conversation_history == []  # a failed call writes nothing


@pytest.mark.asyncio
async def test_generate_charges_usage_with_reported_or_estimated_counts():
    db = FakeDB()
    m = mgr([_chat('{"actions": []}', usage={"prompt_tokens": 11, "completion_tokens": 7}),
             _chat('{"actions": []}')])
    m.set_usage_tracker(TokenUsage(db))
    m.set_usage_context("sess", "camp")
    assert m.usage_context == ("sess", "camp")
    await m.generate("a")
    s, c, p, comp, model, call_type = db.recorded[0]
    assert (s, c, p, comp, call_type) == ("sess", "camp", 11, 7, "chat") and model == m.model
    await m.generate("b")  # no usage block: estimated from the prompt and reply
    assert db.recorded[1][2] > 0 and db.recorded[1][3] > 0


@pytest.mark.asyncio
async def test_budget_is_checked_before_the_request_is_sent():
    fired = []

    async def hook(err):
        fired.append(err)

    m = mgr([_chat('{"actions": []}')])
    m.set_usage_tracker(TokenUsage(FakeDB(used=999_990), budget=1_000_000, on_exhausted=hook))
    m.set_usage_context("sess")
    with pytest.raises(TokenBudgetExceeded):
        await m.generate("go")
    assert m._http.requests == [] and len(fired) == 1
    # requested = estimated prompt + reserved output tokens
    assert fired[0].requested >= m._max_tokens
    with pytest.raises(TokenBudgetExceeded):
        await m.generate_text("hi")
    with pytest.raises(TokenBudgetExceeded):
        [c async for c in m.generate_stream("hi")]
    assert m._http.requests == []


# --- generate_text ----------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_text_request_shape_and_usage():
    db = FakeDB()
    m = mgr([_chat("  Hello there.  ", usage={"prompt_tokens": 5, "completion_tokens": 3})])
    m.set_usage_tracker(TokenUsage(db))
    m.set_usage_context("sess", "camp")
    out = await m.generate_text("Q?", system_prompt="SYS", context="CTX")
    assert out == "Hello there."
    p = m._http.requests[0]["json"]
    assert p["messages"] == [{"role": "system", "content": "SYS\n\nCTX"}, {"role": "user", "content": "Q?"}]
    assert "response_format" not in p
    assert db.recorded[0][2:] == (5, 3, m.model, "text")
    assert m.conversation_history == []  # generate_text reads/writes no chat history


@pytest.mark.asyncio
async def test_generate_text_without_context_has_single_system_message():
    m = mgr([_chat("x")])
    await m.generate_text("Q?", system_prompt="SYS")
    assert m._http.requests[0]["json"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert len(m._http.requests[0]["json"]["messages"]) == 2


# --- generate_stream --------------------------------------------------------

class FakeStream:
    def __init__(self, lines, status=200):
        self.lines, self.status = lines, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    async def aiter_lines(self):
        for line in self.lines:
            yield line


def _sse(obj):
    return "data: " + json.dumps(obj)


@pytest.mark.asyncio
async def test_stream_skips_garbage_lines_records_stream_usage_and_stores_json():
    db = FakeDB()
    m = mgr()
    lines = [
        "", ": keepalive", "data: {broken", _sse({"choices": []}),
        _sse({"choices": [{"delta": {"content": '{"actions": '}}]}),
        _sse({"choices": [{"delta": {}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}),
        _sse({"choices": [{"delta": {"content": '[]}'}}]}),
        "data: [DONE]", _sse({"choices": [{"delta": {"content": "NEVER"}}]}),
    ]
    m._http.stream = lambda *a, **k: FakeStream(lines)
    m.set_usage_tracker(TokenUsage(db))
    m.set_usage_context("sess", "camp")
    got = [c async for c in m.generate_stream("go")]
    assert "".join(got) == '{"actions": []}'
    assert db.recorded[0][2:] == (9, 4, m.model, "stream")
    assert m.conversation_history[-1] == {"role": "assistant", "content": '{"actions": []}'}


@pytest.mark.asyncio
async def test_stream_http_error_propagates_and_stores_nothing():
    m = mgr()
    m._http.stream = lambda *a, **k: FakeStream([], status=500)
    with pytest.raises(httpx.HTTPStatusError):
        [c async for c in m.generate_stream("go")]
    assert m.conversation_history == []


# --- _extract_json ----------------------------------------------------------

def test_extract_json_fences_braces_and_failure():
    m = mgr()
    assert m._extract_json('think\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert m._extract_json('```\n{"a": 2}\n```') == '{"a": 2}'
    # newest valid fence wins; an invalid later one is skipped
    assert m._extract_json('```json\n{"a": 1}\n```\n```json\n{bad\n```') == '{"a": 1}'
    # braces inside strings don't unbalance the scan; latest parseable block wins
    assert m._extract_json('{"x": 1} then {"t": "}{", "y": 2}') == '{"t": "}{", "y": 2}'
    assert m._extract_json('{"q": "say \\"hi\\" {"} trailing') == '{"q": "say \\"hi\\" {"}'
    with pytest.raises(ValueError):
        m._extract_json("no json here { unbalanced")


@pytest.mark.asyncio
async def test_close_swallows_shutdown_errors():
    m = mgr()

    class Boom:
        async def aclose(self):
            raise RuntimeError("loop closed")

    boom = Boom()
    m._http = boom
    assert await m.close() is None
    assert m._http is boom  # the failed close leaves the client reference alone
