"""Edge-case coverage for context/campaign_memory.py (review pass)."""

import asyncio
import json
import time

from context import campaign_memory as cm
from context.campaign_memory import CampaignMemory, _clean_facts, _clean_topics, _mentions, _parse_json, _render_row
from persistence.db import Database

C = "Oakhaven"


class FakeLLM:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    async def generate_text(self, user_message, system_prompt="", context=""):
        self.calls.append({"system_prompt": system_prompt, "context": context})
        reply = self.replies.pop(0) if self.replies else _r("ok")
        if isinstance(reply, Exception):
            raise reply
        return reply


def _r(summary, topics=(), facts=(), resolved=()):
    return json.dumps({"summary": summary, "topics": list(topics), "facts": list(facts),
                       "resolved": list(resolved)})


async def _db(tmp_path):
    db = Database(str(tmp_path / "m.db"))
    await db.init()
    return db


async def _turn(db, session, player, narration):
    await db.save_conversation(session, C, "user", player)
    await db.save_conversation(session, C, "assistant", json.dumps({"type": "narrate", "text": narration}))


def run(coro):
    return asyncio.run(coro)


def test_parse_json_variants():
    assert _parse_json('noise {"a": 1} tail') == {"a": 1}
    assert _parse_json("no braces") is None
    assert _parse_json("{broken") is None
    assert _parse_json("{not: json}") is None
    assert _parse_json("[1] {") is None


def test_clean_topics_filters_dedupes_and_caps():
    raw = ["Mira", "mira", " Black Tower ", "x", "y" * 61, 7, None] + [f"Topic{i}" for i in range(20)]
    out = _clean_topics(raw)
    assert out[:2] == ["Mira", "Black Tower"]
    assert len(out) == cm.MAX_TOPICS_PER_NODE
    assert _clean_topics("not a list") == []


def test_clean_facts_validates_kind_text_and_caps():
    raw = [{"kind": "debt", "text": "  owes 5gp  "}, {"kind": "bogus", "text": "x"},
           {"kind": "item", "text": "   "}, {"kind": "item", "text": 5}, "junk",
           {"kind": "death", "text": "z" * 500}]
    out = _clean_facts(raw)
    assert out[0] == {"kind": "debt", "text": "owes 5gp"}
    assert out[1]["kind"] == "death" and len(out[1]["text"]) == 300
    assert len(out) == 2
    assert len(_clean_facts([{"kind": "item", "text": str(i)} for i in range(20)])) == cm.MAX_FACTS_PER_NODE
    assert _clean_facts(None) == []


def test_render_row_variants():
    assert _render_row({"role": "user", "content": "hi"}) == "PLAYER: hi"
    assert _render_row({"role": "user", "content": None}) == "PLAYER: "
    assert _render_row({"role": "event", "content": "door slams"}) == "EVENT: door slams"
    speak = json.dumps({"type": "speak", "npc_name": "Mira", "text": "Hello"})
    assert _render_row({"role": "assistant", "content": speak}) == "Mira: Hello"
    assert _render_row({"role": "assistant", "content": json.dumps({"type": "narrate", "text": "Dark."})}) == "GM: Dark."
    act = json.dumps({"type": "roll", "dice": "1d20"})
    assert _render_row({"role": "assistant", "content": act}) == 'GM action roll: {"dice": "1d20"}'
    assert _render_row({"role": "assistant", "content": "plain text"}) == "GM: plain text"
    assert _render_row({"role": "assistant", "content": "[1, 2]"}) == "GM: [1, 2]"
    assert _render_row({"role": "system", "content": "old summary"}) == ""
    assert len(_render_row({"role": "user", "content": "x" * 2000})) == cm.MAX_LINE_CHARS


def test_mentions_is_word_bounded_and_case_folded():
    assert _mentions("we go to the black tower", "Black Tower")
    assert not _mentions("a miramar sunset", "Mira")
    # topics ending in punctuation must still match (\b never did)
    assert _mentions("what about c++ now", "C++")
    assert _mentions("ask dr. smith", "Dr. Smith")


def test_context_block_empty_and_no_campaign(tmp_path):
    async def go():
        db = await _db(tmp_path)
        m = CampaignMemory(db, FakeLLM())
        assert await m.context_block("", "s", "q") == ""
        assert await m.context_block(C, "s", "q") == ""
        await db.close()
    run(go())


def test_context_block_previous_session_recall_and_open_facts(tmp_path):
    async def go():
        db = await _db(tmp_path)
        m = CampaignMemory(db, FakeLLM())
        n1 = await db.add_memory_node(C, "old", 1, 1, 2, "Met Mira at the inn.", ["Mira"])
        await db.add_memory_facts(C, n1, [{"kind": "debt", "text": "Owes Mira 5gp"}])
        await db.add_memory_node(C, "old", 2, 1, 2, "Recap: Mira's debt.", ["Mira"])
        await db.add_memory_node(C, "new", 1, 3, 4, "Reached the Tower.", ["Black Tower"])
        await db.add_memory_node(C, "gone", 1, 5, 6, "", [])  # blank = legacy marker, not memory
        block = await m.context_block(C, "new", "Tell me about mira")
        assert "Topics with recorded history" in block
        assert block.index("Black Tower") < block.index("Mira,") if "Mira," in block else True
        assert "Open threads" in block and "- [debt] Owes Mira 5gp" in block
        assert "Previously (last session): Recap: Mira's debt." in block
        assert "Earlier this session:\n- Reached the Tower." in block
        # "Met Mira at the inn." is already carried by nothing -> recalled from a past session
        assert "Recalled because Mira came up" in block
        assert "(a past session) Met Mira at the inn." in block
        # No topic named: no recall section
        quiet = await m.context_block(C, "new", "I look around")
        assert "Recalled" not in quiet
        # level-1 text embedded in the previous recap isn't recalled twice
        await db.close()
    run(go())


def test_context_block_does_not_repeat_summary_already_in_previous_recap(tmp_path):
    async def go():
        db = await _db(tmp_path)
        m = CampaignMemory(db, FakeLLM())
        await db.add_memory_node(C, "old", 1, 1, 2, "Met Mira.", ["Mira"])
        await db.add_memory_node(C, "old", 2, 1, 2, "Met Mira.", ["Mira"])
        block = await m.context_block(C, "new", "mira?")
        assert block.count("Met Mira.") == 1
        assert "Recalled" not in block
        await db.close()
    run(go())


def test_recent_messages_rebuilds_chat_history(tmp_path):
    async def go():
        db = await _db(tmp_path)
        m = CampaignMemory(db, FakeLLM())
        await db.save_conversation("s", C, "assistant", json.dumps({"type": "narrate", "text": "orphan"}))
        await db.save_conversation("s", C, "user", "I knock")
        await db.save_conversation("s", C, "assistant", json.dumps({"type": "narrate", "text": "Creak."}))
        await db.save_conversation("s", C, "assistant", "raw words")
        await db.save_conversation("s", C, "event", "thunder")
        await db.save_conversation("s", C, "system", "ignored")
        msgs = await m.recent_messages(C, "s")
        assert msgs[0] == {"role": "user", "content": "I knock"}  # opens on a player message
        assert json.loads(msgs[1]["content"]) == {"actions": [
            {"type": "narrate", "text": "Creak."}, {"type": "narrate", "text": "raw words"}]}
        assert msgs[2] == {"role": "user", "content": "[Event] thunder"}
        assert len(msgs) == 3
        await db.close()
    run(go())


def test_canon_candidates_only_this_sessions_facts(tmp_path):
    async def go():
        db = await _db(tmp_path)
        m = CampaignMemory(db, FakeLLM())
        a = await db.add_memory_node(C, "s1", 1, 1, 2, "a", [])
        b = await db.add_memory_node(C, "s2", 1, 3, 4, "b", [])
        await db.add_memory_facts(C, a, [{"kind": "item", "text": "got sword"}])
        await db.add_memory_facts(C, b, [{"kind": "death", "text": "Bob died"}])
        assert await m.canon_candidates(C, "s1", "RECAP") == ["RECAP", "[item] got sword"]
        assert await m.canon_candidates(C, "s1", "") == ["[item] got sword"]
        await db.close()
    run(go())


def test_guards_blank_ids_and_backoff(tmp_path):
    async def go():
        db = await _db(tmp_path)
        llm = FakeLLM([RuntimeError("down"), RuntimeError("down"), _r("Fine.")])
        m = CampaignMemory(db, llm, every_n_turns=1)
        assert await m.maybe_compact("", "s") == 0
        assert await m.maybe_compact(C, "") == 0
        assert await m.close_session("", "s") == ""
        await _turn(db, "s", "hello", "hi")
        assert await m.maybe_compact(C, "s") == 0  # model error -> nothing written
        assert m._failures == 1 and m._retry_at > time.monotonic()
        assert await db.get_memory_nodes(C) == []
        n_calls = len(llm.calls)
        assert await m.maybe_compact(C, "s") == 0  # backing off: model not asked again
        assert len(llm.calls) == n_calls
        assert await m.maybe_compact(C, "s", force=True) == 0  # forced: asked, fails again
        assert m._failures == 2
        assert await m.maybe_compact(C, "s", force=True) == 1
        assert m._failures == 0 and m._retry_at == 0.0
        await db.close()
    run(go())


def test_backoff_is_capped(tmp_path):
    async def go():
        db = await _db(tmp_path)
        m = CampaignMemory(db, FakeLLM(["not json"] * 20), every_n_turns=1)
        await _turn(db, "s", "hello", "hi")
        for _ in range(12):
            await m.maybe_compact(C, "s", force=True)
        assert m._retry_at - time.monotonic() <= cm.BACKOFF_MAX_S
        await db.close()
    run(go())


def test_bad_replies_are_rejected(tmp_path):
    async def go():
        db = await _db(tmp_path)
        llm = FakeLLM(["", json.dumps({"summary": "   "}), json.dumps({"summary": 5}), "[]"])
        m = CampaignMemory(db, llm, every_n_turns=1)
        await _turn(db, "s", "hello", "hi")
        for _ in range(4):
            assert await m.maybe_compact(C, "s", force=True) == 0
        assert await db.get_memory_nodes(C) == []
        await db.close()
    run(go())


def test_legacy_system_rows_are_marked_covered_without_calling_the_model(tmp_path):
    async def go():
        db = await _db(tmp_path)
        llm = FakeLLM()
        m = CampaignMemory(db, llm, every_n_turns=5)
        await db.save_conversation("s", C, "system", "old build summary")
        assert await m.maybe_compact(C, "s", include_partial=True) == 1
        assert llm.calls == []
        nodes = await db.get_memory_nodes(C)
        assert len(nodes) == 1 and nodes[0]["summary"] == ""
        assert await m.maybe_compact(C, "s", include_partial=True) == 0  # not re-read
        await db.close()
    run(go())


def test_open_facts_go_to_the_model_and_resolution_ids_are_validated(tmp_path):
    async def go():
        db = await _db(tmp_path)
        n0 = await db.add_memory_node(C, "s", 1, 0, 0, "prior", [])
        await db.add_memory_facts(C, n0, [{"kind": "debt", "text": "owes 5gp"},
                                           {"kind": "quest", "text": "find the orb"},
                                           {"kind": "secret", "text": "Mira is a spy"}])
        facts = await db.get_open_memory_facts(C, 12)
        ids = [f["id"] for f in facts]
        llm = FakeLLM([_r("Paid up.", ["Mira"], resolved=[str(ids[0]), True, 999, "x", 2.5, ids[1] + 0.0])])
        m = CampaignMemory(db, llm, every_n_turns=1)
        await _turn(db, "s", "I pay Mira", "She smiles.")
        assert await m.maybe_compact(C, "s") == 1
        ctx = llm.calls[0]["context"]
        assert ctx.startswith("OPEN FACTS:\n") and f"#{ids[0]} [debt] owes 5gp" in ctx
        assert "TRANSCRIPT:\nPLAYER: I pay Mira" in ctx
        left = {f["text"] for f in await db.get_open_memory_facts(C, 12)}
        # only the id the model gave as a numeric string was resolved; bool True/999/"x"/floats ignored
        assert left == {"find the orb", "Mira is a spy"}
        await db.close()
    run(go())


def test_session_notes_and_close_session_multi_segment_uses_model(tmp_path):
    async def go():
        db = await _db(tmp_path)
        llm = FakeLLM([_r("Seg one.", ["Mira"]), _r("Seg two.", ["Tower"]),
                       json.dumps({"summary": "Whole session.", "topics": ["Mira", "Tower"]})])
        m = CampaignMemory(db, llm, every_n_turns=1)
        await _turn(db, "s", "a", "1")
        await _turn(db, "s", "b", "2")
        assert await m.session_notes(C, "s") == "Seg one.\n\nSeg two."
        recap = await m.close_session(C, "s")
        assert recap == "Whole session."
        assert "Seg one.\n\nSeg two." == llm.calls[-1]["context"]
        l2 = [n for n in await db.get_memory_nodes(C) if n["level"] == 2]
        assert len(l2) == 1 and l2[0]["topics"] == ["Mira", "Tower"]
        assert (l2[0]["first_raw_id"], l2[0]["last_raw_id"]) == (1, 4)
        # closing again returns the stored recap without asking again
        n = len(llm.calls)
        assert await m.close_session(C, "s") == "Whole session."
        assert len(llm.calls) == n
        await db.close()
    run(go())


def test_close_session_falls_back_to_joined_notes_when_model_fails(tmp_path):
    async def go():
        db = await _db(tmp_path)
        llm = FakeLLM([_r("One.", ["A1"]), _r("Two.", ["B2"]), RuntimeError("down")])
        m = CampaignMemory(db, llm, every_n_turns=1)
        await _turn(db, "s", "a", "1")
        await _turn(db, "s", "b", "2")
        recap = await m.close_session(C, "s")
        assert recap == "One.\n\nTwo."
        l2 = [n for n in await db.get_memory_nodes(C) if n["level"] == 2][0]
        assert l2["topics"] == ["A1", "B2"]
        # empty session -> no recap, no node
        assert await m.close_session(C, "nothing") == ""
        await db.close()
    run(go())


def test_rebuild_recompacts_closed_and_active_sessions(tmp_path):
    async def go():
        db = await _db(tmp_path)
        await db.create_session("old", C)
        await _turn(db, "old", "a", "1")
        await db.create_session("live", C)  # closes "old", activates "live"
        await _turn(db, "live", "b", "2")
        await _turn(db, "live", "c", "3")
        await db.add_memory_node(C, "old", 1, 1, 2, "STALE", ["Stale"])
        llm = FakeLLM([_r("Old rebuilt.", ["Oldtopic"]), _r("Live seg.", ["Livetopic"])])
        m = CampaignMemory(db, llm, every_n_turns=2)
        written = await m.rebuild(C)
        nodes = await db.get_memory_nodes(C)
        assert "STALE" not in [n["summary"] for n in nodes]
        assert written == len(nodes) == 3  # old: L1 + L2; live: one full 2-turn L1, no L2 (still open)
        assert [(n["session_id"], n["level"]) for n in nodes] == [("old", 1), ("old", 2), ("live", 1)]
        await db.close()
    run(go())


def test_next_segment_boundaries(tmp_path):
    m = CampaignMemory(None, None, every_n_turns=2)
    rows = [{"role": r, "id": i} for i, r in enumerate(["user", "assistant", "user", "assistant", "user", "assistant"])]
    assert m._next_segment(rows, False) == rows[:4]  # stops right before the 3rd player message
    assert m._next_segment(rows[:2], False) == []
    assert m._next_segment(rows[:2], True) == rows[:2]
    assert m._next_segment([], True) == []
    full = [{"role": "assistant", "id": i} for i in range(cm.MAX_ROWS_PER_PASS)]
    assert m._next_segment(full, False) == full  # a full read page is taken even without N turns
    assert CampaignMemory(None, None, every_n_turns=0).every == 1


def test_a_fact_the_model_was_never_shown_cannot_be_resolved_by_id(tmp_path):
    async def go():
        db = await _db(tmp_path)
        n0 = await db.add_memory_node(C, "s", 1, 0, 0, "prior", [])
        total = cm.OPEN_FACTS_SHOWN + 1
        await db.add_memory_facts(C, n0, [{"kind": "debt", "text": f"fact{i}"} for i in range(total)])
        everything = await db.get_open_memory_facts(C, 100)
        hidden = everything[0]  # oldest: beyond the window shown to the model
        shown = everything[-1]
        llm = FakeLLM([_r("Done.", resolved=[hidden["id"], shown["id"]])])
        m = CampaignMemory(db, llm, every_n_turns=1)
        await _turn(db, "s", "go", "ok")
        await m.maybe_compact(C, "s")
        assert f"#{hidden['id']} " not in llm.calls[0]["context"]
        left = {f["text"] for f in await db.get_open_memory_facts(C, 100)}
        assert hidden["text"] in left and shown["text"] not in left
        await db.close()
    run(go())


def test_backoff_doubles_with_each_consecutive_failure(tmp_path):
    async def go():
        db = await _db(tmp_path)
        m = CampaignMemory(db, FakeLLM(["bad"] * 5), every_n_turns=1)
        await _turn(db, "s", "hello", "hi")
        waits = []
        for _ in range(3):
            await m.maybe_compact(C, "s", force=True)
            waits.append(round(m._retry_at - time.monotonic()))
        assert waits == [cm.BACKOFF_BASE_S, 2 * cm.BACKOFF_BASE_S, 4 * cm.BACKOFF_BASE_S]
        await db.close()
    run(go())
