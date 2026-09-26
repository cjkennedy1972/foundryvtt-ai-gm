"""Campaign memory: raw log -> hierarchical summaries -> topic index -> recall.

Replaces the rolling session summary, which never saw player turns (they
stream through generate_stream, which did not record them), was overwritten
every five turns by a keyword stub, reached the prompt on one turn in three,
and did not survive a restart or carry into the next session.

Run:
    cd ai-engine && python -m pytest tests/test_campaign_memory.py -v
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from context.campaign_memory import CampaignMemory
from persistence.db import Database

CAMPAIGN = "Oakhaven"


class FakeLLM:
    """Only generate_text exists: compaction must never replay chat history."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.contexts = []

    async def generate_text(self, user_message, system_prompt="", context=""):
        self.contexts.append(context)
        reply = self.replies.pop(0) if self.replies else _reply("Nothing notable.")
        if isinstance(reply, Exception):
            raise reply
        return reply


def _reply(summary, topics=(), facts=(), resolved=()):
    return json.dumps({"summary": summary, "topics": list(topics),
                       "facts": list(facts), "resolved": list(resolved)})


async def _db(tmp_path):
    db = Database(str(tmp_path / "memory.db"))
    await db.init()
    return db


async def _turn(db, session, player, narration):
    await db.save_conversation(session, CAMPAIGN, "user", player)
    await db.save_conversation(session, CAMPAIGN, "assistant", json.dumps({"type": "narrate", "text": narration}))


def run(coro):
    return asyncio.run(coro)


def test_compaction_waits_for_n_player_turns_and_reads_the_player(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        llm = FakeLLM([_reply("Party met Mira.", ["Mira"])])
        memory = CampaignMemory(db, llm, every_n_turns=2)
        await db.create_session("s1", CAMPAIGN)

        await _turn(db, "s1", "I greet the innkeeper.", "Mira nods.")
        assert await memory.maybe_compact(CAMPAIGN, "s1") == 0
        assert llm.contexts == []

        await _turn(db, "s1", "I ask Mira about the tower.", "She pales.")
        assert await memory.maybe_compact(CAMPAIGN, "s1") == 1
        # The player's own words reach the summariser — the old path never saw them.
        assert "PLAYER: I ask Mira about the tower." in llm.contexts[0]
        assert "GM: She pales." in llm.contexts[0]
        await db.close()
    run(scenario())


def test_index_is_always_shown_but_details_only_when_a_topic_is_named(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        llm = FakeLLM([
            _reply("Mira revealed the Black Tower's key is lost.", ["Mira", "Black Tower"]),
            _reply("The party rested at the inn.", ["Inn"]),
            _reply("Session one: the key is lost.", ["Black Tower"]),
        ])
        memory = CampaignMemory(db, llm, every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Where is the key?", "Mira sighs.")
        await _turn(db, "s1", "We rest.", "Night passes.")
        await memory.maybe_compact(CAMPAIGN, "s1")
        await memory.close_session(CAMPAIGN, "s1")
        await db.close_session("s1")
        await db.create_session("s2", CAMPAIGN)

        quiet = await memory.context_block(CAMPAIGN, "s2", "I check my pack.")
        assert "Mira" in quiet and "Black Tower" in quiet            # the index line
        assert "Previously (last session): Session one" in quiet    # session recap
        assert "Recalled" not in quiet

        named = await memory.context_block(CAMPAIGN, "s2", "We head for the black tower.")
        assert "Recalled because Black Tower came up" in named
        assert "(a past session) Mira revealed the Black Tower's key is lost." in named
        await db.close()
    run(scenario())


def test_durable_facts_stay_visible_until_resolved(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        llm = FakeLLM([
            _reply("A deal was struck.", ["Mira"],
                   facts=[{"kind": "promise", "text": "Party promised Mira 50gp."},
                          {"kind": "nonsense", "text": "dropped at the boundary"}]),
        ])
        memory = CampaignMemory(db, llm, every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Deal.", "Mira shakes on it.")
        await memory.maybe_compact(CAMPAIGN, "s1")

        block = await memory.context_block(CAMPAIGN, "s1", "")
        assert "[promise] Party promised Mira 50gp." in block
        assert "dropped at the boundary" not in block

        fact_id = (await db.get_open_memory_facts(CAMPAIGN))[0]["id"]
        llm.replies.append(_reply("They paid Mira.", resolved=[fact_id, 9999]))
        await _turn(db, "s1", "We pay her.", "Coins change hands.")
        await memory.maybe_compact(CAMPAIGN, "s1")
        # The open fact was handed to the model by id so it could settle it.
        assert f"#{fact_id} [promise]" in llm.contexts[-1]
        assert "[promise]" not in await memory.context_block(CAMPAIGN, "s1", "")
        await db.close()
    run(scenario())


def test_a_failed_compaction_writes_nothing_and_the_rows_stay_pending(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        llm = FakeLLM([RuntimeError("model down"), "not json at all"])
        memory = CampaignMemory(db, llm, every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Hello.", "Hi.")

        # force: retry now rather than wait out the backoff (tested separately)
        assert await memory.maybe_compact(CAMPAIGN, "s1") == 0
        assert await memory.maybe_compact(CAMPAIGN, "s1", force=True) == 0
        assert await db.get_memory_nodes(CAMPAIGN) == []

        llm.replies.append(_reply("Greetings exchanged."))
        assert await memory.maybe_compact(CAMPAIGN, "s1", force=True) == 1
        node = (await db.get_memory_nodes(CAMPAIGN))[0]
        assert node["first_raw_id"] == 1  # nothing was skipped over
        await db.close()
    run(scenario())


def test_summaries_are_disposable_and_rebuilt_from_the_raw_log(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        memory = CampaignMemory(db, FakeLLM([_reply("first version", ["Mira"])]), every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Hello Mira.", "Mira waves.")
        await memory.maybe_compact(CAMPAIGN, "s1")

        memory.llm = FakeLLM([_reply("rebuilt version", ["Mira"])])
        assert await memory.rebuild(CAMPAIGN) == 1
        nodes = await db.get_memory_nodes(CAMPAIGN)
        assert [n["summary"] for n in nodes] == ["rebuilt version"]
        assert len(await db.get_raw_after(CAMPAIGN, "s1")) == 2  # raw untouched
        await db.close()
    run(scenario())


def test_retention_never_deletes_the_raw_log(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        await _turn(db, "s1", "Ancient words.", "Ancient reply.")
        await db._conn.execute("UPDATE ai_conversations SET timestamp = '2000-01-01'")
        await db._conn.commit()
        await db.apply_retention_policy()
        assert len(await db.get_raw_after(CAMPAIGN, "s1")) == 2
        await db.close()
    run(scenario())


def test_legacy_summary_rows_are_not_compacted_as_play(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        llm = FakeLLM()
        memory = CampaignMemory(db, llm, every_n_turns=1)
        await db.save_conversation("s1", CAMPAIGN, "system", "## SESSION SUMMARY old derived text")
        await _turn(db, "s1", "Go on.", "The road bends.")
        await memory.maybe_compact(CAMPAIGN, "s1")
        assert "old derived text" not in llm.contexts[0]
        await db.close()
    run(scenario())


def test_restart_resumes_with_the_recent_exchanges(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        memory = CampaignMemory(db, FakeLLM())
        await db.save_conversation("s1", CAMPAIGN, "assistant", json.dumps({"type": "narrate", "text": "orphan"}))
        await db.save_conversation("s1", CAMPAIGN, "user", "I open the door.")
        await db.save_conversation("s1", CAMPAIGN, "assistant", json.dumps({"type": "narrate", "text": "It creaks."}))
        await db.save_conversation("s1", CAMPAIGN, "assistant", json.dumps({"type": "play_sound", "sound": "creak"}))
        await db.save_conversation("s1", CAMPAIGN, "event", "Combat ended after 2 round(s).")

        messages = await memory.recent_messages(CAMPAIGN, "s1")
        assert messages[0] == {"role": "user", "content": "I open the door."}
        assert json.loads(messages[1]["content"])["actions"][1]["type"] == "play_sound"
        assert messages[2]["content"].startswith("[Event] Combat ended")
        await db.close()
    run(scenario())


def test_every_turn_records_raw_and_compacts_off_the_turn_path():
    from foundry.chat_listener import ChatListener

    async def scenario():
        memory = MagicMock()
        started = asyncio.Event()

        async def slow_compact(campaign, session_id):
            started.set()
            await asyncio.sleep(10)

        memory.maybe_compact = slow_compact
        db = MagicMock()
        db.get_active_session_info = AsyncMock(return_value={"session_id": "s1", "campaign": CAMPAIGN})
        db.save_conversation = AsyncMock()
        listener = ChatListener(foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
                                state_tracker=MagicMock(), db=db, campaign_memory=memory)

        await asyncio.wait_for(listener._record_exchange("I attack.", [{"type": "narrate", "text": "Hit."}]), 1)
        roles = [c.args[2] for c in db.save_conversation.call_args_list]
        assert roles == ["user", "assistant"]
        await asyncio.wait_for(started.wait(), 1)  # compaction started, not awaited
    run(scenario())


def test_a_restarted_campaign_forgets_immediately(tmp_path):
    """Campaign restart wipes the DB directly; memory must not keep serving it."""
    async def scenario():
        db = await _db(tmp_path)
        memory = CampaignMemory(db, FakeLLM([_reply("Mira joined.", ["Mira"])]), every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Join us, Mira.", "She does.")
        await memory.maybe_compact(CAMPAIGN, "s1")
        assert "Mira" in await memory.context_block(CAMPAIGN, "s1", "")

        await db.delete_campaign_history(CAMPAIGN)
        assert await memory.context_block(CAMPAIGN, "s1", "") == ""
        await db.close()
    run(scenario())


def test_resolved_ids_written_as_strings_still_resolve(tmp_path):
    async def scenario():
        db = await _db(tmp_path)
        llm = FakeLLM([_reply("Debt taken.", facts=[{"kind": "debt", "text": "Owes Mira 5gp."}])])
        memory = CampaignMemory(db, llm, every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Lend me coin.", "Mira does.")
        await memory.maybe_compact(CAMPAIGN, "s1")
        fact_id = (await db.get_open_memory_facts(CAMPAIGN))[0]["id"]

        llm.replies.append(_reply("Debt paid.", resolved=[str(fact_id), True, "x"]))
        await _turn(db, "s1", "Here's your coin.", "Thanks.")
        await memory.maybe_compact(CAMPAIGN, "s1")
        assert await db.get_open_memory_facts(CAMPAIGN) == []
        await db.close()
    run(scenario())


def test_failures_back_off_instead_of_retrying_every_turn(tmp_path, monkeypatch):
    import context.campaign_memory as cm

    async def scenario():
        now = [1000.0]
        monkeypatch.setattr(cm.time, "monotonic", lambda: now[0])
        db = await _db(tmp_path)
        llm = FakeLLM(["garbage", "garbage"])
        memory = CampaignMemory(db, llm, every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Hello.", "Hi.")

        assert await memory.maybe_compact(CAMPAIGN, "s1") == 0
        assert len(llm.contexts) == 1
        await _turn(db, "s1", "Still there?", "Yes.")
        assert await memory.maybe_compact(CAMPAIGN, "s1") == 0
        assert len(llm.contexts) == 1                       # backing off: no call

        now[0] += cm.BACKOFF_BASE_S + 1
        assert await memory.maybe_compact(CAMPAIGN, "s1") == 0
        assert len(llm.contexts) == 2                       # retried, failed again
        now[0] += cm.BACKOFF_BASE_S + 1
        assert await memory.maybe_compact(CAMPAIGN, "s1") == 0
        assert len(llm.contexts) == 2                       # second wait is doubled

        # An explicit request ignores the backoff, and success clears it.
        assert await memory.maybe_compact(CAMPAIGN, "s1", force=True) >= 1
        assert memory._retry_at == 0.0
        await db.close()
    run(scenario())


def test_a_cancelled_compaction_writes_nothing_and_releases_the_lock(tmp_path):
    class SlowLLM(FakeLLM):
        async def generate_text(self, user_message, system_prompt="", context=""):
            await asyncio.sleep(10)

    async def scenario():
        db = await _db(tmp_path)
        memory = CampaignMemory(db, SlowLLM(), every_n_turns=1)
        await db.create_session("s1", CAMPAIGN)
        await _turn(db, "s1", "Hello.", "Hi.")

        task = asyncio.ensure_future(memory.maybe_compact(CAMPAIGN, "s1"))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await db.get_memory_nodes(CAMPAIGN) == []
        assert memory._failures == 0                        # a preemption isn't a failure

        memory.llm = FakeLLM([_reply("Greetings.")])
        assert await asyncio.wait_for(memory.maybe_compact(CAMPAIGN, "s1"), 1) == 1
        assert (await db.get_memory_nodes(CAMPAIGN))[0]["first_raw_id"] == 1
        await db.close()
    run(scenario())


def _turn_listener():
    from foundry.chat_listener import ChatListener
    state_tracker = MagicMock()
    state_tracker.state.mode = "exploration"
    listener = ChatListener(foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
                            state_tracker=state_tracker, db=MagicMock(), campaign_memory=MagicMock())
    listener._get_npc_context = AsyncMock(return_value="")
    listener._build_location_context = AsyncMock(return_value="")
    listener._memory_context = AsyncMock(return_value="")
    listener._process_normal_input = AsyncMock()
    return listener


def test_a_player_turn_preempts_compaction_on_a_single_request_server(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "llm_concurrent_requests", False)

    async def scenario():
        listener = _turn_listener()
        listener._compaction = asyncio.ensure_future(asyncio.sleep(10))
        await listener._run_turn("I open the door.", "Alice")
        await asyncio.sleep(0)
        assert listener._compaction.cancelled()
        listener._process_normal_input.assert_awaited_once()
    run(scenario())


def test_a_concurrent_server_lets_compaction_finish(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "llm_concurrent_requests", True)

    async def scenario():
        listener = _turn_listener()
        listener._compaction = asyncio.ensure_future(asyncio.sleep(10))
        await listener._run_turn("I open the door.", "Alice")
        await asyncio.sleep(0)
        assert not listener._compaction.done()
        listener._compaction.cancel()
    run(scenario())
