"""Behavioral coverage for persistence/db.py and backup_db.py (review pass)."""

import asyncio
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

import backup_db
import persistence.db as dbmod
from persistence.db import Database, _connection_worker, _mark_worker_daemon


def run(coro):
    return asyncio.run(coro)


async def _db(tmp_path, name="t.db"):
    db = Database(str(tmp_path / name))
    await db.init()
    return db


# --- worker-thread helpers --------------------------------------------------------

def test_connection_worker_shapes_and_daemon_marking(caplog):
    t = threading.Thread(target=lambda: None)
    assert _connection_worker(t) is t  # aiosqlite <= 0.21: the connection is the thread
    wrapper = type("C", (), {})()
    wrapper._thread = t                 # aiosqlite >= 0.22: it holds one
    assert _connection_worker(wrapper) is t
    assert _connection_worker(object()) is None
    assert _connection_worker(type("C", (), {"_thread": "nope"})()) is None
    t.daemon = False
    assert _mark_worker_daemon(wrapper) is True and t.daemon is True
    with caplog.at_level(logging.WARNING, logger="persistence.db"):
        assert _mark_worker_daemon(object()) is False
    assert "worker thread could not be located" in caplog.text


# --- lifecycle ------------------------------------------------------------------------

def test_context_manager_inits_and_closes(tmp_path):
    async def go():
        async with Database(str(tmp_path / "cm.db")) as db:
            await db.save_state("k", {"a": 1})
            assert await db.load_state("k") == {"a": 1}
            assert await db.load_state("missing") is None
        assert db._conn is None
        await db.close()  # closing twice is harmless
    run(go())


def test_gc_without_close_schedules_close_and_warns(tmp_path, caplog):
    async def go():
        db = Database(str(tmp_path / "gc.db"))
        await db.init()
        conn = db._conn
        with caplog.at_level(logging.WARNING, logger="persistence.db"):
            db.__del__()
            await asyncio.sleep(0.1)
        assert "garbage-collected without close()" in caplog.text
        assert db._conn is None  # the scheduled close ran
        db2 = Database(str(tmp_path / "gc2.db"))
        db2.__del__()  # never opened: nothing to do
        return conn
    run(go())
    Database(":memory:").__del__()  # no running loop: silently returns


def test_gc_close_scheduling_failure_is_logged(tmp_path, caplog, monkeypatch):
    async def go():
        db = Database(str(tmp_path / "gc3.db"))
        await db.init()
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "create_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
        with caplog.at_level(logging.WARNING, logger="persistence.db"):
            db.__del__()
        assert "close could not be scheduled" in caplog.text
        monkeypatch.undo()
        await db.close()
    run(go())


def test_pre_migration_backup_failure_does_not_block_startup(tmp_path, monkeypatch, caplog):
    async def go():
        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE game_state (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TIMESTAMP)")
        con.execute("INSERT INTO game_state VALUES ('k', '{\"x\": 1}', NULL)")
        con.commit()
        con.close()

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(dbmod._backup_db_module, "backup_db", boom)
        with caplog.at_level(logging.WARNING, logger="persistence.db"):
            db = Database(str(path))
            await db.init()
        assert "Pre-migration backup failed" in caplog.text
        assert await db.load_state("k") == {"x": 1}  # data intact, migrations still ran
        await db.close()
    run(go())


# --- events -----------------------------------------------------------------------------

def test_events_limit_returns_the_most_recent_in_chronological_order(tmp_path):
    async def go():
        db = await _db(tmp_path)
        for i in range(5):
            await db.record_typed_event("s1", "C", "npc_died", {"i": i}, f"d{i}")
        await db.record_event("s2", "C", "legacy")
        await db.record_typed_event("s1", "Other", "x", {}, "other-campaign")
        recent = await db.get_events_full("C", limit=3)
        assert [e["description"] for e in recent] == ["d3", "d4", "legacy"]  # newest 3, oldest-first
        assert [e["description"] for e in await db.get_events_full("C", session_id="s1", limit=2)] == ["d3", "d4"]
        full = await db.get_events_full("C")
        assert [e["description"] for e in full][:2] == ["d0", "d1"] and len(full) == 6
        assert full[0]["payload"] == {"i": 0} and full[-1]["type"] == "legacy_note" and full[-1]["payload"] == {}
        assert [e["description"] for e in await db.get_events("C", limit=2)] == ["legacy", "d4"]  # newest first
        assert [e["description"] for e in await db.get_events("C", session_id="s2")] == ["legacy"]
        await db.close()
    run(go())


def test_retention_deletes_old_events_keeps_new_and_never_conversations(tmp_path, monkeypatch):
    async def go():
        db = await _db(tmp_path)
        now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

        class FrozenDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(dbmod, "datetime", FrozenDT)
        cutoff = now - timedelta(days=dbmod.EVENT_RETENTION_DAYS)
        fmt = "%Y-%m-%d %H:%M:%S"  # what CURRENT_TIMESTAMP stores
        for desc, ts in (("too old", cutoff - timedelta(minutes=1)), ("just inside", cutoff + timedelta(minutes=1)),
                         ("recent", now - timedelta(days=1))):
            await db._conn.execute(
                "INSERT INTO events (session_id, campaign, description, type, timestamp) VALUES ('s','C',?,'legacy_note',?)",
                (desc, ts.strftime(fmt)))
        await db._conn.execute(
            "INSERT INTO ai_conversations (session_id, campaign, role, content, timestamp) "
            "VALUES ('s','C','user','ancient','2001-01-01 00:00:00')")
        await db._conn.commit()
        await db.apply_retention_policy()
        assert {e["description"] for e in await db.get_events("C")} == {"just inside", "recent"}
        assert len(await db.get_conversation_history("C")) == 1
        await db.close()
    run(go())


def test_retention_failure_rolls_back_and_logs(tmp_path, caplog):
    async def go():
        db = await _db(tmp_path)
        real = db._conn

        class Broken:
            rolled = False

            async def execute(self, *a, **k):
                raise RuntimeError("locked")

            async def rollback(self):
                Broken.rolled = True

        db._conn = Broken()
        with caplog.at_level(logging.ERROR, logger="persistence.db"):
            await db.apply_retention_policy()  # must not raise
        assert Broken.rolled and "Retention policy failed: locked" in caplog.text

        class NoRollback(Broken):
            async def rollback(self):
                raise RuntimeError("also broken")

        db._conn = NoRollback()
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="persistence.db"):
            await db.apply_retention_policy()
        assert "Rollback after retention failure also failed" in caplog.text
        db._conn = real
        await db.close()
    run(go())


# --- campaign memory tables ----------------------------------------------------------------

def test_memory_facts_open_resolve_and_derived_delete(tmp_path):
    async def go():
        db = await _db(tmp_path)
        n = await db.add_memory_node("C", "s", 1, 1, 4, "sum", ["Mira"])
        await db.add_memory_node("Other", "s", 1, 1, 4, "x", [])
        await db.add_memory_facts("C", n, [])
        await db.add_memory_facts("C", n, [{"kind": "debt", "text": f"f{i}"} for i in range(5)])
        facts = await db.get_open_memory_facts("C", limit=3)
        assert [f["text"] for f in facts] == ["f2", "f3", "f4"]  # newest 3, oldest-first
        await db.resolve_memory_facts("C", [])
        await db.resolve_memory_facts("C", [facts[0]["id"], facts[1]["id"]])
        assert [f["text"] for f in await db.get_open_memory_facts("C")] == ["f0", "f1", "f4"]
        # resolving another campaign's ids is a no-op
        await db.resolve_memory_facts("Other", [facts[2]["id"]])
        assert len(await db.get_open_memory_facts("C")) == 3
        nodes = await db.get_memory_nodes("C")
        assert nodes == [{"id": n, "session_id": "s", "level": 1, "first_raw_id": 1, "last_raw_id": 4,
                          "summary": "sum", "topics": ["Mira"]}]
        await db.delete_derived_memory("C")
        assert await db.get_memory_nodes("C") == [] and await db.get_open_memory_facts("C") == []
        assert len(await db.get_memory_nodes("Other")) == 1
        await db.close()
    run(go())


# --- llm usage -----------------------------------------------------------------------------------

def test_usage_totals_are_scoped_and_clamped(tmp_path):
    async def go():
        db = await _db(tmp_path)
        assert await db.get_llm_usage_total() == 0
        await db.record_llm_usage("s1", "C", 10, 5, "m", "chat")
        await db.record_llm_usage("s1", "C", -50, -5, None, "text")  # negatives clamp to zero, never subtract
        await db.record_llm_usage("s2", None, 100, 1, "m")
        assert await db.get_llm_usage_total(session_id="s1") == 15
        assert await db.get_llm_usage_total(campaign="C") == 15
        assert await db.get_llm_usage_total(campaign="") == 101  # None campaign stored as ''
        assert await db.get_llm_usage_total() == 116
        assert await db.get_llm_usage_total(session_id="s1", campaign="zzz") == 15  # session wins
        u = await db.get_llm_usage(session_id="s1")
        assert (u["prompt_tokens"], u["completion_tokens"], u["total_tokens"]) == (10, 5, 15)
        assert [c["call_type"] for c in u["calls"]] == ["chat", "text"] and u["calls"][1]["model"] == ""
        both = await db.get_llm_usage(session_id="s2", campaign="")
        assert both["total_tokens"] == 101
        assert (await db.get_llm_usage())["total_tokens"] == 116
        assert (await db.get_llm_usage(session_id="s1", campaign="other"))["calls"] == []
        await db.close()
    run(go())


# --- sessions, canon proposals, campaign history ---------------------------------------------------

def test_sessions_switch_active_and_close(tmp_path):
    async def go():
        db = await _db(tmp_path)
        assert await db.get_active_session() is None and await db.get_active_session_info() is None
        await db.create_session("a", "C1")
        await db.create_session("b", "C2")
        assert await db.get_active_session() == "b"
        assert await db.get_active_session_info() == {"session_id": "b", "campaign": "C2"}
        await db.close_session("b")
        assert await db.get_active_session() is None
        await db.create_session("c", "C1")
        assert await db.get_campaign_session_ids("C1") == ["c", "a"]  # newest first
        await db.close()
    run(go())


def test_canon_proposal_state_machine_is_compare_and_swap(tmp_path):
    async def go():
        db = await _db(tmp_path)
        pid = await db.create_canon_proposal("s", "C", "Borin runs the inn", "high", "said so", "contradicts X")
        assert (await db.get_canon_proposal(pid))["status"] == "pending"
        assert await db.get_canon_proposal(999) is None
        assert await db.approve_canon_proposal(pid, final_text="Borin owns the inn") is True
        assert await db.approve_canon_proposal(pid) is False  # already claimed
        assert await db.reject_canon_proposal(pid) is False
        row = await db.get_canon_proposal(pid)
        assert row["status"] == "approved" and row["fact"] == "Borin owns the inn" and row["reviewed_at"]
        await db.revert_canon_proposal_to_pending(pid)
        row = await db.get_canon_proposal(pid)
        assert row["status"] == "pending" and row["reviewed_at"] is None
        assert [p["id"] for p in await db.get_pending_canon_proposals()] == [pid]
        assert await db.reject_canon_proposal(pid) is True
        assert await db.get_pending_canon_proposals() == []
        p2 = await db.create_canon_proposal("s", "C", "fact", "low")
        assert await db.approve_canon_proposal(p2) is True
        assert (await db.get_canon_proposal(p2))["fact"] == "fact"  # no final_text: wording untouched
        await db.close()
    run(go())


def test_delete_campaign_history_removes_only_that_campaign(tmp_path):
    async def go():
        db = await _db(tmp_path)
        await db.create_session("s1", "C")
        await db.create_session("s2", "Other")
        for s, c in (("s1", "C"), ("s2", "Other")):
            await db.save_conversation(s, c, "user", f"hi {c}")
            await db.record_event(s, c, f"ev {c}")
        await db.create_canon_proposal("s1", "C", "f", "low")
        n = await db.add_memory_node("C", "s1", 1, 1, 1, "sum", [])
        await db.add_memory_facts("C", n, [{"kind": "item", "text": "sword"}])
        assert await db.delete_campaign_history("C") == 1
        assert await db.get_conversation_history("C") == [] and await db.get_events("C") == []
        assert await db.get_pending_canon_proposals() == []
        assert await db.get_memory_nodes("C") == [] and await db.get_open_memory_facts("C") == []
        assert len(await db.get_conversation_history("Other")) == 1 and len(await db.get_events("Other")) == 1
        assert await db.delete_campaign_history("C") == 0  # nothing left: still safe
        await db.close()
    run(go())


def test_delete_campaign_history_also_clears_rows_whose_session_was_never_registered(tmp_path):
    """A session created without a campaign (create_session's default) leaves
    session_info.campaign == ''; the campaign's raw log and events written under
    it must still be wiped on restart, or campaign memory rebuilds the old story."""
    async def go():
        db = await _db(tmp_path)
        await db.create_session("s-nocampaign")          # campaign defaults to ""
        await db.save_conversation("s-nocampaign", "C", "user", "old story")
        await db.record_event("s-nocampaign", "C", "old event")
        await db.record_typed_event("ghost-session", "C", "npc_died", {}, "orphan")
        await db.delete_campaign_history("C")
        assert await db.get_conversation_history("C") == []
        assert await db.get_events("C") == []
        assert await db.get_campaign_sessions_in_order("C") == []
        await db.close()
    run(go())


def test_npc_records_upsert_and_scope(tmp_path):
    async def go():
        db = await _db(tmp_path)
        await db.upsert_npc_record("mira", "C", {"name": "Mira", "hp": 5})
        await db.upsert_npc_record("mira", "C", {"name": "Mira", "hp": 9})
        await db.upsert_npc_record("mira", "Other", {"name": "Other Mira"})
        assert await db.get_npc_records("C") == [{"name": "Mira", "hp": 9}]
        assert await db.get_npc_records("none") == []
        await db.close()
    run(go())


# --- backup_db ----------------------------------------------------------------------------------------

def _make_wal_db(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")  # keep committed rows in the WAL file
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES ('committed-in-wal')")
    con.commit()
    return con  # left open: a live writer, like the engine


def test_backup_of_a_live_wal_database_contains_committed_rows_in_the_main_file(tmp_path):
    src = tmp_path / "game.db"
    live = _make_wal_db(src)
    out = backup_db.backup_db(str(src), str(tmp_path / "bk"))
    live.close()
    copy = tmp_path / "isolated.db"
    # Only the main db file: the checkpoint must have folded the WAL into it before copying.
    import shutil
    shutil.copy(f"{out}/game.db", copy)
    rows = sqlite3.connect(copy).execute("SELECT v FROM t").fetchall()
    assert rows == [("committed-in-wal",)]


def test_backup_copies_wal_and_prunes_oldest(tmp_path, monkeypatch, capsys):
    src = tmp_path / "game.db"
    live = _make_wal_db(src)
    bk = tmp_path / "bk"
    stamps = iter(range(1, 20))

    class FakeDT:
        @staticmethod
        def now():
            return datetime(2026, 1, 1, 0, 0, next(stamps))

    monkeypatch.setattr(backup_db, "datetime", FakeDT)
    outs = [backup_db.backup_db(str(src), str(bk), max_backups=2) for _ in range(4)]
    live.close()
    assert sorted(p.name for p in bk.glob("backup_*")) == ["backup_20260101_000003", "backup_20260101_000004"]
    assert "Pruned old backup" in capsys.readouterr().out
    assert (bk / "backup_20260101_000004" / "game.db").exists()
    assert outs[-1].endswith("backup_20260101_000004")


def test_backup_missing_database_exits_nonzero(tmp_path, capsys):
    with pytest.raises(SystemExit) as ei:
        backup_db.backup_db(str(tmp_path / "nope.db"), str(tmp_path / "bk"))
    assert ei.value.code == 1 and "Database not found" in capsys.readouterr().err
    assert not (tmp_path / "bk").exists()  # nothing created for a missing source


def test_backup_config_and_cli_args(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "/x/env.db")
    monkeypatch.setenv("BACKUP_DIR", "/x/envbk")
    assert backup_db.get_backup_config() == ("/x/env.db", "/x/envbk")
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("BACKUP_DIR")
    assert backup_db.get_backup_config() == ("./data/game.db", "./backups")
    seen = {}
    monkeypatch.setattr(backup_db, "backup_db", lambda *a: seen.setdefault("args", a))
    monkeypatch.setattr("sys.argv", ["backup_db.py", "--db-path", "/a.db", "--backup-dir", "/b", "--max-backups", "7"])
    backup_db.main()
    assert seen["args"] == ("/a.db", "/b", 7)
    seen.clear()
    monkeypatch.setenv("DATABASE_URL", "/env.db")
    monkeypatch.setattr("sys.argv", ["backup_db.py"])
    backup_db.main()
    assert seen["args"] == ("/env.db", "./backups", 30)
