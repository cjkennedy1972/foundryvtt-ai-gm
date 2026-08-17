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


async def _create_legacy_schema(db_path: str):
    """Recreate the pre-Phase-2 `events` table shape (no type/payload) with
    a couple of rows, simulating a real pre-existing deployment."""
    conn = await aiosqlite.connect(db_path)
    await conn.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            description TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.execute(
        "INSERT INTO events (session_id, description) VALUES (?, ?)",
        ("sess-legacy", "The party entered the tavern."),
    )
    await conn.execute(
        "INSERT INTO events (session_id, description) VALUES (?, ?)",
        ("sess-legacy", "A bar fight broke out."),
    )
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
        await _create_legacy_schema(db_path)

        db = Database(db_path)
        await db.init()

        events = await db.get_events_full("sess-legacy")
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
        await _create_legacy_schema(db_path)

        db = Database(db_path)
        await db.init()
        await db.close()

    asyncio.run(run())

    assert backup_dir.exists()
    assert list(backup_dir.glob("backup_*")), "expected at least one backup directory"


def test_run_migrations_is_idempotent(tmp_path):
    async def run():
        db_path = str(tmp_path / "idempotent.db")
        await _create_legacy_schema(db_path)

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
