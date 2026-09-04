"""Additive schema migrations for pre-existing SQLite databases.

A fresh database gets the full target schema directly from the CREATE TABLE
statements in Database.init() and never actually needs these to run (each
migration below is a no-op against that schema). This module exists only to
carry an already-deployed database forward without losing data — every
migration is additive (new column, new table); nothing is ever dropped or
renamed, so old queries against old columns keep working unmodified during
rollout.
"""

import logging

logger = logging.getLogger(__name__)


async def _table_columns(conn, table: str) -> set:
    async with conn.execute(f"PRAGMA table_info({table})") as cursor:
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def _migration_1_typed_events(conn):
    """Add type/payload to `events`, backfilling existing rows as
    'legacy_note' so they project as a no-op instead of an unknown type."""
    cols = await _table_columns(conn, "events")
    if "type" not in cols:
        await conn.execute("ALTER TABLE events ADD COLUMN type TEXT")
    if "payload" not in cols:
        await conn.execute("ALTER TABLE events ADD COLUMN payload TEXT")
    await conn.execute("UPDATE events SET type = 'legacy_note' WHERE type IS NULL")


async def _migration_2_npc_tables(conn):
    """Create npc_records — NPCs aren't in SQLite at all before this, so
    there's nothing to backfill, just a new empty table. Goals are stored
    inline in data_json (dataclasses.asdict(npc) already includes them via
    npc/persistence.py) rather than a separate npc_goals table — there is
    no per-goal query need that would justify normalizing them out.

    Primary key is (npc_id, campaign), NOT npc_id alone: npc_id is a
    display-name-derived id (see foundry/chat_listener.py
    register_npc(npc_id=npc_name, ...)), so two campaigns can plausibly
    reuse the same NPC name (e.g. a generic "Bartender"). A bare npc_id PK
    would let campaign B's save silently overwrite campaign A's row for
    the same name.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS npc_records (
            npc_id TEXT NOT NULL,
            campaign TEXT NOT NULL,
            data_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npc_id, campaign)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_npc_records_campaign ON npc_records(campaign)")


async def _migration_3_llm_usage(conn):
    """Add durable per-request prompt/completion token accounting."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            campaign TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            call_type TEXT NOT NULL DEFAULT 'chat',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_session ON llm_usage(session_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_campaign ON llm_usage(campaign)")


# Ordered by version; keep every past migration even after it's folded into
# the baseline CREATE TABLE DDL, so an old deployment can still walk forward.
MIGRATIONS = {
    1: _migration_1_typed_events,
    2: _migration_2_npc_tables,
    3: _migration_3_llm_usage,
}


async def _ensure_version_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


async def get_schema_version(conn) -> int:
    await _ensure_version_table(conn)
    async with conn.execute("SELECT MAX(version) FROM schema_version") as cursor:
        row = await cursor.fetchone()
    return row[0] or 0


async def run_migrations(conn, current: int = None) -> int:
    """Run any migration whose version is greater than the stored
    schema_version, in order, committing after each. Returns the resulting
    version. Pass `current` (from a caller that already fetched it, e.g. to
    decide whether to back up first) to skip a redundant re-query."""
    if current is None:
        current = await get_schema_version(conn)
    for version in sorted(v for v in MIGRATIONS if v > current):
        logger.info(f"[migrations] Applying migration {version}")
        await MIGRATIONS[version](conn)
        await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        await conn.commit()
        current = version
    return current
