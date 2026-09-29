"""Entity extraction in enrichment: small batches, split further on failure.

A two-pack module's 11 note chunks (25k tokens) went to one entity call. Its
answer — every NPC, place, faction and artifact — outgrew max_tokens (16k),
and the model reasoned first, so two attempts came back empty and the third
cut off mid-array. All three were the identical request.

Run:
    cd ai-engine && python -m pytest tests/test_enrich_entity_batches.py -v
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from campaign import orchestrator_enrich as oe
from campaign.orchestrator import CampaignOrchestrator


def test_notes_are_batched_under_the_limit_without_splitting_a_note():
    notes = ["a" * 10, "b" * 10, "c" * 10, "d" * 50]
    assert CampaignOrchestrator._batch_notes(notes, 25) == [["a" * 10, "b" * 10], ["c" * 10], ["d" * 50]]


def _llm(cut_off_when_more_than: int):
    """An LLM whose entity answer is cut off (finish_reason=length) when the
    request holds more than N notes, and otherwise names one NPC per note."""
    calls = []

    async def post(url, headers=None, json=None, timeout=None):
        user = json["messages"][-1]["content"]
        notes = [n for n in user.split("GM NOTES:\n", 1)[1].split(oe._NOTES_SEPARATOR)]
        calls.append(len(notes))
        resp = MagicMock(status_code=200, text="")
        if len(notes) > cut_off_when_more_than:
            content, finish = '```json\n{"npcs": [{"name": "Tass', "length"
        else:
            content = __import__("json").dumps({"npcs": [{"name": n.strip()} for n in notes]})
            finish = "stop"
        resp.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                  "usage": {"completion_tokens": 16384 if finish == "length" else 50}}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=post)
    return client, calls


def test_a_batch_that_comes_back_cut_off_is_halved_not_repeated():
    llm, calls = _llm(cut_off_when_more_than=1)
    progress = []
    results = asyncio.run(CampaignOrchestrator()._enrich_entities_split(
        llm, "http://llm", {}, ["Tasslehoff", "Flint", "Tanis", "Sturm"], progress.append))

    names = [n["name"] for r in results for n in r["npcs"]]
    assert names == ["Tasslehoff", "Flint", "Tanis", "Sturm"]      # nothing lost, order kept
    assert calls.count(4) == 1 and calls.count(2) == 2             # each multi-note batch tried once
    assert any("splitting" in p for p in progress)


def test_a_single_note_that_still_fails_raises_after_retries():
    llm, calls = _llm(cut_off_when_more_than=0)
    try:
        asyncio.run(CampaignOrchestrator()._enrich_entities_split(llm, "http://llm", {}, ["Raistlin"], lambda m: None))
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("a single note that cannot be parsed must still fail the source")
    assert calls == [1, 1, 1]


def test_thinking_is_switched_off_the_ways_servers_read_it():
    payload = CampaignOrchestrator()._suppress_thinking(
        {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]})
    assert payload["messages"][-1]["content"].startswith("/no_think\n")   # Qwen3's directive
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}  # llama.cpp / vLLM
    assert payload["enable_thinking"] is False
