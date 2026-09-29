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


# ── a single note, and the world/history step (the second failed run) ────

def test_halving_splits_notes_then_a_long_note_at_a_paragraph():
    assert oe._halve(["a", "b", "c"]) == (["a"], ["b", "c"])
    assert oe._halve(["short note"]) is None                        # retried, not split
    long = "\n\n".join(f"Paragraph {i}. " + "x" * 900 for i in range(10))
    first, second = oe._halve([long])
    assert first[0].endswith("x") and second[0].startswith("Paragraph")   # on a paragraph break
    assert first[0] + "\n\n" + second[0] == long


def test_one_note_too_dense_for_one_answer_is_split_by_its_text():
    """One note listed more entities than max_tokens holds, three times over."""
    paragraphs = [f"NPC{i} " + "lore " * 300 for i in range(8)]
    note = "\n\n".join(paragraphs)                                   # ~12k chars, one note
    calls = []

    async def post(url, headers=None, json=None, timeout=None):
        text = json["messages"][-1]["content"].split("GM NOTES:\n", 1)[1]
        calls.append(len(text))
        resp = MagicMock(status_code=200, text="")
        if len(text) > 7000:
            body = {"choices": [{"message": {"content": '```json\n{"npcs": [{"name": "NPC0'}, "finish_reason": "length"}]}
        else:
            names = [p.split()[0] for p in text.split("\n\n")]
            body = {"choices": [{"message": {"content": __import__("json").dumps({"npcs": [{"name": n} for n in names]})},
                                 "finish_reason": "stop"}]}
        resp.json.return_value = body
        return resp

    llm = MagicMock()
    llm.post = AsyncMock(side_effect=post)
    results = asyncio.run(CampaignOrchestrator()._enrich_entities_split(llm, "http://llm", {}, [note], lambda m: None))
    assert [n["name"] for r in results for n in r["npcs"]] == [f"NPC{i}" for i in range(8)]
    assert calls.count(len(note)) == 1                                # not retried whole


def test_cut_off_world_additions_are_redone_in_halves_not_kept_partial(tmp_path):
    from campaign.enrichment import build_delta_prompt  # noqa: F401  (prompt shape used below)
    orch = CampaignOrchestrator()
    calls = []

    async def post(url, headers=None, json=None, timeout=None):
        user = json["messages"][-1]["content"]
        notes = user.split("NOTES FROM THE NEW SOURCE:\n", 1)[1].split(oe._NOTES_SEPARATOR)
        calls.append(len(notes))
        resp = MagicMock(status_code=200, text="")
        if len(notes) > 1:     # the whole group: cut off inside WORLD
            content, finish = "===WORLD===\nThe first half of a long ans", "length"
        else:
            content = f"===WORLD===\nFact from {notes[0].strip()}.\n===HISTORY===\nNone\n===CONFLICTS===\n===END==="
            finish = "stop"
        resp.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
        return resp

    llm = MagicMock()
    llm.post = AsyncMock(side_effect=post)
    # Run only the delta loop's logic through its public pieces.
    async def delta_loop(notes):
        world = ""
        queue = orch._batch_notes(notes, oe._NOTES_GROUP_CHARS)
        while queue:
            group = queue.pop(0)
            w, h, c, cut = await orch._enrich_delta(llm, "http://llm", {}, world, "", oe._NOTES_SEPARATOR.join(group), "Src")
            halves = oe._halve(group) if cut else None
            if halves:
                queue[:0] = list(halves)
                continue
            world += w + "\n"
        return world
    world = asyncio.run(delta_loop(["Alpha", "Beta"]))
    assert "Fact from Alpha." in world and "Fact from Beta." in world
    assert "first half of a long ans" not in world
    assert calls == [2, 1, 1]


def test_an_unsplittable_piece_with_no_answer_is_retried_not_dropped(tmp_path):
    """The model sometimes reasons away the whole budget even on a small note;
    the empty answer was kept and the note's world lore lost."""
    import campaign.orchestrator_enrich as oe
    from campaign.vault import CampaignStore
    from campaign.obsidian_sync import get_campaign_folder
    vault = tmp_path / "vault"
    folder = get_campaign_folder(vault, "Camp")
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps({"campaign": {"name": "Camp"}}))
    replies = iter(["", "", "===WORLD===\nThe Fen of Sighs.\n===HISTORY===\nNone\n===CONFLICTS===\n===END==="])

    async def post(url, headers=None, json=None, timeout=None):
        system = json["messages"][0]["content"]
        resp = MagicMock(status_code=200, text="")
        if "extending the world lore" in system:
            content = next(replies)
            finish = "length" if not content else "stop"
        elif "Extract the lore entities" in system:
            content, finish = __import__("json").dumps({"npcs": [], "locations": [], "factions": [], "artifacts": []}), "stop"
        else:                                                  # pass-1 notes
            content, finish = "A short note about the Fen.", "stop"
        resp.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
        return resp

    llm = MagicMock()
    llm.post = AsyncMock(side_effect=post)
    src = tmp_path / "src"
    src.mkdir()
    (src / "Fen.md").write_text("The Fen of Sighs lies east.")
    result = asyncio.run(CampaignOrchestrator().enrich_campaign("Camp", llm, source_path=str(src), vault_path=str(vault)))
    assert result["status"] == "ok", result
    assert "The Fen of Sighs." in (folder / "Worldbuilding.md").read_text()


# ── duplicate editions, heartbeat, dead host (the Companion run) ──────────

def test_two_editions_of_one_book_have_the_same_fingerprint():
    from campaign.enrichment import fingerprint_similarity, source_fingerprint
    text = [(i, f"Page {i}. Kansaldi Fire-Eyes leads the Red Dragonarmy against Kalaman. " * 20) for i in range(30)]
    reflowed = [(1, " ".join(t for _, t in text))]                     # one layout, the same words
    other = [(1, "Lord Soth rides from Dargaard Keep under a blood moon, cursed forever. " * 600)]
    assert fingerprint_similarity(source_fingerprint(text), source_fingerprint(reflowed)) == 1.0
    assert fingerprint_similarity(source_fingerprint(text), source_fingerprint(other)) < 0.1


def test_a_second_edition_is_skipped_and_an_old_record_gets_fingerprinted(tmp_path):
    """The printer-friendly Companion re-read a book already added from its
    full-color edition: over an hour of duplicates. The full-color record
    predates fingerprints, so it is fingerprinted from its saved path."""
    from campaign.obsidian_sync import get_campaign_folder
    vault = tmp_path / "vault"
    folder = get_campaign_folder(vault, "Camp")
    folder.mkdir(parents=True)
    book = "Kansaldi Fire-Eyes leads the Red Dragonarmy against Kalaman. " * 200
    src = tmp_path / "src"
    src.mkdir()
    (src / "Companion - Full Color.md").write_text(book)
    (folder / "campaign.json").write_text(json.dumps({"campaign": {"name": "Camp"}, "sources": [
        {"id": "companion - full color-md", "title": "Companion - Full Color", "type": "md",
         "path": str(src / "Companion - Full Color.md")}]}))
    new = tmp_path / "new"
    new.mkdir()
    (new / "Companion - Printer Friendly.md").write_text(book)
    llm = MagicMock()
    llm.post = AsyncMock(side_effect=AssertionError("a duplicate must not reach the LLM"))
    progress = []

    result = asyncio.run(CampaignOrchestrator().enrich_campaign(
        "Camp", llm, source_path=str(new), vault_path=str(vault),
        on_progress=lambda m, *a: progress.append(m)))

    assert result["skipped"] == ["companion - printer friendly-md"] and result["sources"] == []
    assert any("another edition" in m and "Full Color" in m for m in progress)


def test_a_request_still_out_says_so(monkeypatch):
    monkeypatch.setattr(oe, "_HEARTBEAT_S", 0.01)
    said = []

    async def slow():
        async with oe._waiting("entities (9000 chars)", said.append):
            await asyncio.sleep(0.05)

    asyncio.run(slow())
    assert said and all("still waiting on the LLM for entities" in m for m in said)


def test_an_unreachable_host_fails_the_source_instead_of_splitting():
    import httpx
    llm = MagicMock()
    llm.post = AsyncMock(side_effect=httpx.ConnectError("Network is unreachable"))
    try:
        asyncio.run(CampaignOrchestrator()._enrich_entities_split(
            llm, "http://llm", {}, ["a" * 5000, "b" * 5000], lambda m: None))
    except httpx.ConnectError:
        pass
    else:
        raise AssertionError("expected the connection error to stand")
    assert llm.post.await_count == 1                                  # not once per half
