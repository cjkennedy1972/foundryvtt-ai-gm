#!/usr/bin/env python3
"""Tests for persistence.migrations — verifies an already-deployed,
pre-Phase-2 database upgrades without losing data, and that a fresh
Database.init() ends up at the same schema either way.

Run:
    cd ai-engine && python -m pytest tests/test_migrations.py -v
"""

import asyncio
import os
import sys

import aiosqlite

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence.db import Database
from persistence.migrations import MIGRATIONS, get_schema_version, run_migrations


async def _create_v3_schema(db_path: str):
    """Recreate the schema version 3 shape (no campaign column in events/conversations)
    with a couple of rows, simulating a real pre-existing deployment before migration 4."""
    conn = await aiosqlite.connect(db_path)
    # Schema v1-3 components
    await conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, description TEXT NOT NULL, type TEXT, payload TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    await conn.execute("CREATE TABLE ai_conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    await conn.execute("CREATE TABLE session_info (session_id TEXT PRIMARY KEY, started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ended_at TIMESTAMP, active INTEGER DEFAULT 1, campaign TEXT)")
    await conn.execute("CREATE TABLE canon_proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, fact TEXT NOT NULL, confidence TEXT NOT NULL, rationale TEXT, contradiction_note TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, reviewed_at TIMESTAMP)")
    await conn.execute("CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, campaign TEXT NOT NULL DEFAULT '', prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '', call_type TEXT NOT NULL DEFAULT 'chat', timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    await conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    
    # Set version to 3
    await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (3,))
    
    # Add some legacy data without campaign
    await conn.execute("INSERT INTO events (session_id, description) VALUES (?, ?)", ("sess-legacy", "The party entered the tavern."))
    await conn.execute("INSERT INTO ai_conversations (session_id, role, content) VALUES (?, ?, ?)", ("sess-legacy", "GM", "Welcome!"))
    
    # Add an active session for the backfill to pick up
    await conn.execute("INSERT INTO session_info (session_id, active, campaign) VALUES (?, 1, ?)", ("sess-legacy", "Epic Quest"))
    
    await conn.commit()
    await conn.close()


def test_fresh_database_does_not_trigger_a_spurious_backup(tmp_path, monkeypatch):
    """Regression: a brand-new database has nothing to protect. Earlier
    logic checked file-exists-with-content AFTER connecting and creating
    tables, so it always saw its own just-written bytes and backed up every
    fresh install; the check must run before init() writes anything."""
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))

    async def run():
        db = Database(str(tmp_path / "brand_new.db"))
        await db.init()
        await db.close()

    asyncio.run(run())
    assert not backup_dir.exists(), "fresh database should never trigger a backup"


def test_fresh_database_lands_on_latest_schema(tmp_path):
    async def run():
        db = Database(str(tmp_path / "fresh.db"))
        await db.init()
        version = await get_schema_version(db._conn)
        assert version == max(MIGRATIONS)
        await db.close()

    asyncio.run(run())


def test_legacy_events_survive_migration(tmp_path):
    async def run():
        db_path = str(tmp_path / "legacy.db")
        # Use the old legacy creator from original test for basic v1 compatibility
        conn = await aiosqlite.connect(db_path)
        await conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, description TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await conn.execute("INSERT INTO events (session_id, description) VALUES (?, ?)", ("sess-legacy", "The party entered the tavern."))
        await conn.execute("INSERT INTO events (session_id, description) VALUES (?, ?)", ("sess-legacy", "A bar fight broke out."))
        await conn.commit()
        await conn.close()

        db = Database(db_path)
        await db.init()

        # Note: in new schema, get_events_full requires a campaign. 
        # For v1 legacy, it would have been backfilled to 'default' or active session.
        # We check if it's visible under whatever was backfilled.
        # Since we didn't provide session_info in this specific test, it should be 'default'
        events = await db.get_events_full("default")
        assert len(events) == 2
        assert events[0]["description"] == "The party entered the tavern."
        assert events[0]["type"] == "legacy_note"
        assert events[1]["type"] == "legacy_note"

        version = await get_schema_version(db._conn)
        assert version == max(MIGRATIONS)
        await db.close()

    asyncio.run(run())


def test_migration_creates_pre_migration_backup(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))

    async def run():
        db_path = str(tmp_path / "legacy2.db")
        conn = await aiosqlite.connect(db_path)
        await conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, description TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await conn.commit()
        await conn.close()

        db = Database(db_path)
        await db.init()
        await db.close()

    asyncio.run(run())

    assert backup_dir.exists()
    assert list(backup_dir.glob("backup_*")), "expected at least one backup directory"


def test_run_migrations_is_idempotent(tmp_path):
    async def run():
        db_path = str(tmp_path / "idempotent.db")
        conn = await aiosqlite.connect(db_path)
        await conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, description TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await conn.commit()
        await conn.close()

        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        first = await run_migrations(conn)
        second = await run_migrations(conn)
        assert first == second == max(MIGRATIONS)
        assert await get_schema_version(conn) == max(MIGRATIONS)
        await conn.close()

    asyncio.run(run())


def test_in_memory_database_skips_backup():
    """':memory:' has no file to back up — must not raise."""
    async def run():
        db = Database(":memory:")
        await db.init()
        version = await get_schema_version(db._conn)
        assert version == max(MIGRATIONS)
        await db.close()

    asyncio.run(run())


def test_v3_to_v4_migration_and_backfill(tmp_path):
    """Verify that a v3 database boots, migrates to v4, and backfills campaign column
    using the active session's campaign."""
    async def run():
        db_path = str(tmp_path / "v3_upgrade.db")
        await _create_v3_schema(db_path)

        db = Database(db_path)
        # This should not crash now that indexes are deferred and migration 4 exists
        await db.init()

        # Verify migration version
        version = await get_schema_version(db._conn)
        assert version == 4

        # Verify backfill: rows should now be in "Epic Quest"
        events = await db.get_events_full("Epic Quest")
        assert len(events) == 1
        assert events[0]["description"] == "The party entered the tavern."

        convs = await db.get_conversation_history("Epic Quest")
        assert len(convs) == 1
        assert convs[0]["content"] == "Welcome!"

        # Verify isolation: "Wrong Campaign" should be empty
        events_wrong = await db.get_events_full("Wrong Campaign")
        assert len(events_wrong) == 0

        await db.close()

    asyncio.run(run())
