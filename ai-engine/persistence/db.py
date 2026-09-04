"""Persistence layer — SQLite database for game state, events, and conversations."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

import backup_db as _backup_db_module
from persistence.migrations import MIGRATIONS, get_schema_version, run_migrations

logger = logging.getLogger(__name__)

# Chat history retention settings
CONVERSATION_RETENTION_DAYS = 30  # Keep last 30 days of conversation
MIN_RECENT_MESSAGES_PER_SESSION = 100  # Always keep at least 100 recent messages per session
EVENT_RETENTION_DAYS = 60  # Keep event log for 60 days


class Database:
    """SQLite persistence with WAL mode, write lock, and connection reuse."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    # --- Resource-safety: a missed close() must not leak aiosqlite's thread ---
    # aiosqlite runs each Connection on a non-daemon worker thread that blocks
    # interpreter exit if it is never closed (this hung the whole pytest run —
    # see tests/test_npc_memory.py before the close() calls were added). Making
    # Database an async context manager and adding a best-effort __del__ means a
    # forgotten close() degrades to a logged warning instead of a hung process.
    async def __aenter__(self) -> "Database":
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def __del__(self):
        # Best-effort cleanup when the object is GC'd without close(). We cannot
        # await here, so if the loop is still running we schedule close(); if it
        # is already gone (interpreter shutdown) there is nothing safe to do —
        # but by then the leaked thread is the only issue and it is a test-only
        # concern, since production always closes in the lifespan shutdown.
        conn = getattr(self, "_conn", None)
        if conn is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop — cannot safely schedule anything
        if loop.is_closed():
            return
        logger.warning(
            "Database(%s) garbage-collected without close(); scheduling close. "
            "Use 'async with Database(...)' or 'await db.close()'.",
            self.db_path,
        )
        try:
            loop.create_task(self.close())
        except Exception:
            pass

    async def init(self):
        """Initialize the database connection and schema."""
        # Capture whether this is a genuinely pre-existing database BEFORE
        # connecting — aiosqlite.connect() creates the file on disk for a
        # brand-new path, and the CREATE TABLE statements below write real
        # bytes to it, so checking "does the file have content" AFTER
        # connecting would call a just-created, still-empty database
        # "pre-existing" and back it up for no reason.
        pre_existing = (
            self.db_path != ":memory:"
            and Path(self.db_path).exists()
            and Path(self.db_path).stat().st_size > 0
        )
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        # WAL mode allows concurrent reads + single writer
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # 5-second timeout before failing on lock contention
        await self._conn.execute("PRAGMA busy_timeout=5000")
        # Foreign keys for data integrity
        await self._conn.execute("PRAGMA foreign_keys=ON")

        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS game_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                description TEXT NOT NULL,
                type TEXT,
                payload TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS session_info (
                session_id TEXT PRIMARY KEY,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                active INTEGER DEFAULT 1,
                campaign TEXT
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS canon_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                campaign TEXT,
                fact TEXT NOT NULL,
                confidence TEXT NOT NULL,
                rationale TEXT,
                contradiction_note TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP
            )
        """)
        await self._conn.execute("""
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
        # Indexes for faster lookups
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_conversations_session ON ai_conversations(session_id)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_session_info_active ON session_info(active)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_canon_proposals_status ON canon_proposals(status)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_session ON llm_usage(session_id)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_campaign ON llm_usage(campaign)")
        await self._conn.commit()

        current_version = await get_schema_version(self._conn)
        if pre_existing and current_version < max(MIGRATIONS):
            await self._backup_before_migrating()
        applied = await run_migrations(self._conn, current=current_version)
        logger.info(f"Database initialized: {self.db_path} (WAL mode, schema v{applied})")

    async def _backup_before_migrating(self):
        """Back up the database file before schema migrations run. Callers
        only reach this for a database that was already on disk, with
        content, before this init() call started."""
        try:
            backup_dir = os.environ.get("BACKUP_DIR", "./backups")
            # backup_db() calls asyncio.run() internally for its WAL
            # checkpoint step, which raises if invoked directly from our
            # already-running event loop — run it in a thread instead, where
            # asyncio.run() is free to spin its own loop.
            await asyncio.to_thread(_backup_db_module.backup_db, self.db_path, backup_dir)
        except Exception:
            logger.warning("Pre-migration backup failed; continuing without it", exc_info=True)

    async def save_state(self, key: str, value: dict):
        """Save game state under write lock for thread safety."""
        async with self._write_lock:
            await self._conn.execute(
                "INSERT OR REPLACE INTO game_state (key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, default=str), datetime.now(timezone.utc).isoformat())
            )
            await self._conn.commit()

    async def load_state(self, key: str) -> Optional[dict]:
        """Load game state. No lock needed — reads don't conflict."""
        async with self._conn.execute(
            "SELECT value_json FROM game_state WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
        return None

    async def record_event(self, session_id: str, description: str):
        """Record a game event under write lock. Untyped events are tagged
        'legacy_note' so EventStore.replay() can treat them as a no-op
        projection rather than an unknown type."""
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO events (session_id, description, type) VALUES (?, ?, 'legacy_note')",
                (session_id, description)
            )
            await self._conn.commit()

    async def get_events(self, session_id: str, limit: int = 50) -> list:
        """Get recent events for a session."""
        async with self._conn.execute(
            "SELECT description, timestamp FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        ) as cursor:
            return [{"description": row[0], "timestamp": row[1]} async for row in cursor]

    async def record_typed_event(
        self, session_id: str, event_type: str, payload: Optional[dict] = None, description: str = ""
    ) -> int:
        """Record a typed, event-sourced game event. Returns its row id."""
        async with self._write_lock:
            cursor = await self._conn.execute(
                "INSERT INTO events (session_id, description, type, payload) VALUES (?, ?, ?, ?)",
                (session_id, description, event_type, json.dumps(payload or {}, default=str)),
            )
            await self._conn.commit()
            return cursor.lastrowid

    async def get_events_full(self, session_id: str, limit: Optional[int] = None) -> list:
        """Get typed events for a session, oldest first — the ordering
        EventStore.replay() needs to project world state correctly. When
        `limit` is given, returns the MOST RECENT `limit` events (still
        ordered oldest-first) — not the oldest `limit`, which a bare
        `ORDER BY id ASC LIMIT ?` would silently give instead."""
        if limit is not None:
            query = (
                "SELECT id, type, payload, description, timestamp FROM events "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?"
            )
            params: list = [session_id, limit]
        else:
            query = "SELECT id, type, payload, description, timestamp FROM events WHERE session_id = ? ORDER BY id ASC"
            params = [session_id]
        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        if limit is not None:
            rows = list(reversed(rows))
        events = []
        for row in rows:
            events.append({
                "id": row["id"],
                "type": row["type"] or "legacy_note",
                "payload": json.loads(row["payload"]) if row["payload"] else {},
                "description": row["description"],
                "timestamp": row["timestamp"],
            })
        return events

    async def save_conversation(self, session_id: str, role: str, content: str):
        """Save a conversation turn under write lock."""
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO ai_conversations (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content)
            )
            await self._conn.commit()

    async def get_conversation_history(self, session_id: str, limit: int = 100) -> list:
        """Get conversation history ordered oldest-first."""
        async with self._conn.execute(
            "SELECT role, content, timestamp FROM ai_conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        ) as cursor:
            rows = [{"role": row[0], "content": row[1], "timestamp": row[2]} async for row in cursor]
            return list(reversed(rows))

    async def record_llm_usage(self, session_id: str, campaign: str, prompt_tokens: int,
                               completion_tokens: int, model: str = "", call_type: str = "chat"):
        """Persist the provider-reported usage for one completed LLM request."""
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO llm_usage (session_id, campaign, prompt_tokens, completion_tokens, model, call_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, campaign or "", max(0, prompt_tokens), max(0, completion_tokens), model or "", call_type),
            )
            await self._conn.commit()

    async def get_llm_usage_total(self, session_id: Optional[str] = None,
                                  campaign: Optional[str] = None) -> int:
        """Return total prompt+completion tokens for a session or campaign."""
        if session_id is not None:
            query, params = "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM llm_usage WHERE session_id = ?", (session_id,)
        elif campaign is not None:
            query, params = "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM llm_usage WHERE campaign = ?", (campaign,)
        else:
            query, params = "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM llm_usage", ()
        async with self._conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
        return int(row[0] or 0)

    async def get_llm_usage(self, session_id: Optional[str] = None,
                            campaign: Optional[str] = None) -> dict:
        """Return aggregate and per-call usage for API reporting."""
        clauses, params = [], []
        if session_id is not None:
            clauses.append("session_id = ?"); params.append(session_id)
        if campaign is not None:
            clauses.append("campaign = ?"); params.append(campaign)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), COUNT(*) FROM llm_usage" + where,
            params,
        ) as cursor:
            row = await cursor.fetchone()
        async with self._conn.execute(
            "SELECT session_id, campaign, prompt_tokens, completion_tokens, model, call_type, timestamp "
            "FROM llm_usage" + where + " ORDER BY id ASC", params,
        ) as cursor:
            calls = [dict(row) async for row in cursor]
        return {"prompt_tokens": int(row[0]), "completion_tokens": int(row[1]),
                "total_tokens": int(row[0] + row[1]), "calls": calls}

    async def create_session(self, session_id: str, campaign: str = "") -> str:
        """Create a new active session, closing any previous one."""
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO session_info (session_id, active, campaign) VALUES (?, 1, ?)",
                (session_id, campaign)
            )
            await self._conn.execute(
                "UPDATE session_info SET active = 0 WHERE session_id != ?", (session_id,)
            )
            await self._conn.commit()
        return session_id

    async def get_active_session(self) -> Optional[str]:
        """Get the currently active session ID."""
        async with self._conn.execute(
            "SELECT session_id FROM session_info WHERE active = 1 LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_active_session_info(self) -> Optional[dict]:
        """Get the currently active session ID and campaign name."""
        async with self._conn.execute(
            "SELECT session_id, campaign FROM session_info WHERE active = 1 LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return {"session_id": row[0], "campaign": row[1]} if row else None

    async def close_session(self, session_id: str):
        """Mark a session as inactive."""
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE session_info SET active = 0, ended_at = ? WHERE session_id = ?",
                (datetime.now(timezone.utc).isoformat(), session_id)
            )
            await self._conn.commit()

    async def create_canon_proposal(
        self,
        session_id: str,
        campaign: str,
        fact: str,
        confidence: str,
        rationale: str = "",
        contradiction_note: Optional[str] = None,
    ) -> int:
        """Insert an AI-proposed canon fact awaiting GM review. Returns its id."""
        async with self._write_lock:
            cursor = await self._conn.execute(
                "INSERT INTO canon_proposals "
                "(session_id, campaign, fact, confidence, rationale, contradiction_note, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (session_id, campaign, fact, confidence, rationale, contradiction_note),
            )
            await self._conn.commit()
            return cursor.lastrowid

    async def get_pending_canon_proposals(self) -> list:
        """Return all pending canon proposals, oldest first."""
        async with self._conn.execute(
            "SELECT id, session_id, campaign, fact, confidence, rationale, "
            "contradiction_note, status, created_at, reviewed_at "
            "FROM canon_proposals WHERE status = 'pending' ORDER BY created_at ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_canon_proposal(self, proposal_id: int) -> Optional[dict]:
        """Fetch a single canon proposal by id, or None if it doesn't exist."""
        async with self._conn.execute(
            "SELECT id, session_id, campaign, fact, confidence, rationale, "
            "contradiction_note, status, created_at, reviewed_at "
            "FROM canon_proposals WHERE id = ?",
            (proposal_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def approve_canon_proposal(self, proposal_id: int, final_text: Optional[str] = None) -> bool:
        """Atomically claim a PENDING proposal as approved — the WHERE
        status='pending' guard makes this a compare-and-swap, so a proposal
        can never be approved/written twice even if two requests race (a
        double-click, or chat + admin panel approving the same id). If the
        GM edited the wording before approving, final_text overwrites the
        stored fact so the DB always reflects what was actually canonized.

        Returns True if this call actually claimed it (it was pending),
        False if it had already been reviewed by someone else — callers
        must skip the vault write when this returns False.
        """
        async with self._write_lock:
            if final_text:
                cursor = await self._conn.execute(
                    "UPDATE canon_proposals SET status = 'approved', fact = ?, reviewed_at = ? "
                    "WHERE id = ? AND status = 'pending'",
                    (final_text, datetime.now(timezone.utc).isoformat(), proposal_id),
                )
            else:
                cursor = await self._conn.execute(
                    "UPDATE canon_proposals SET status = 'approved', reviewed_at = ? "
                    "WHERE id = ? AND status = 'pending'",
                    (datetime.now(timezone.utc).isoformat(), proposal_id),
                )
            await self._conn.commit()
            return cursor.rowcount > 0

    async def reject_canon_proposal(self, proposal_id: int) -> bool:
        """Mark a proposal rejected — it never gets written to Canon.md.
        Same compare-and-swap guard as approve_canon_proposal; returns
        False if it was already reviewed."""
        async with self._write_lock:
            cursor = await self._conn.execute(
                "UPDATE canon_proposals SET status = 'rejected', reviewed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (datetime.now(timezone.utc).isoformat(), proposal_id),
            )
            await self._conn.commit()
            return cursor.rowcount > 0

    async def revert_canon_proposal_to_pending(self, proposal_id: int) -> None:
        """Best-effort rollback: put a proposal back to 'pending' after a
        vault write failure that followed a successful approval claim, so
        it can be retried instead of vanishing as 'approved' with the fact
        never actually written anywhere."""
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE canon_proposals SET status = 'pending', reviewed_at = NULL WHERE id = ?",
                (proposal_id,),
            )
            await self._conn.commit()

    async def delete_campaign_history(self, campaign: str) -> int:
        """Delete all sessions, events, and conversations for a campaign.

        Used by campaign restart so the AI starts with no memory of prior play.
        Returns the number of sessions removed.
        """
        async with self._write_lock:
            # canon_proposals is keyed directly by campaign, not session_id —
            # clear it unconditionally so a restart doesn't leave orphaned
            # pending proposals behind even if session_info is already empty.
            await self._conn.execute("DELETE FROM canon_proposals WHERE campaign = ?", (campaign,))

            async with self._conn.execute(
                "SELECT session_id FROM session_info WHERE campaign = ?", (campaign,)
            ) as cursor:
                session_ids = [row[0] async for row in cursor]
            if not session_ids:
                await self._conn.commit()
                return 0

            # Batch deletes to prevent unbounded query construction.
            # Each batch respects SQLite's variable limit (max ~32766)
            # and caps the query length for defense-in-depth.
            batch_size = 1000
            total_deleted = 0

            for i in range(0, len(session_ids), batch_size):
                batch = session_ids[i:i + batch_size]
                ph = ",".join("?" * len(batch))
                await self._conn.execute(
                    f"DELETE FROM ai_conversations WHERE session_id IN ({ph})",
                    batch
                )
                await self._conn.execute(
                    f"DELETE FROM events WHERE session_id IN ({ph})",
                    batch
                )
                await self._conn.execute(
                    f"DELETE FROM session_info WHERE session_id IN ({ph})",
                    batch
                )
                total_deleted += len(batch)

            await self._conn.commit()
            logger.info(f"Deleted {total_deleted} session(s) of history for campaign '{campaign}'")
            return total_deleted

    async def upsert_npc_record(self, npc_id: str, campaign: str, data: dict) -> None:
        """Insert or update one NPC's full record (personality, relationships,
        goals — serialized as JSON) for a campaign."""
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO npc_records (npc_id, campaign, data_json, updated_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(npc_id, campaign) DO UPDATE SET "
                "data_json=excluded.data_json, updated_at=excluded.updated_at",
                (npc_id, campaign, json.dumps(data, default=str)),
            )
            await self._conn.commit()

    async def get_npc_records(self, campaign: str) -> list:
        """Return every stored NPC record's parsed data for a campaign."""
        async with self._conn.execute(
            "SELECT data_json FROM npc_records WHERE campaign = ?", (campaign,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [json.loads(row[0]) for row in rows]

    async def close(self):
        """Close the persistent database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    async def apply_retention_policy(self):
        """Apply retention policy to conversation and event history.

        Deletes old messages that exceed retention period, while preserving
        a minimum number of recent messages per session.
        """
        async with self._write_lock:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=CONVERSATION_RETENTION_DAYS)

            # Delete old conversation messages beyond retention period
            # Keep at least MIN_RECENT_MESSAGES_PER_SESSION recent messages per session
            try:
                # Keep the most recent MIN_RECENT_MESSAGES_PER_SESSION messages
                # *per session* — the window function partitions by session_id so
                # LIMIT applies to rows within each session, not to sessions.
                await self._conn.execute("""
                    DELETE FROM ai_conversations
                    WHERE id NOT IN (
                        SELECT id FROM (
                            SELECT id,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY session_id ORDER BY id DESC
                                   ) AS rn
                            FROM ai_conversations
                        )
                        WHERE rn <= ?
                    )
                    AND timestamp < ?
                """, (MIN_RECENT_MESSAGES_PER_SESSION, cutoff_date.isoformat()))

                # Delete old events beyond retention period
                event_cutoff = datetime.now(timezone.utc) - timedelta(days=EVENT_RETENTION_DAYS)
                await self._conn.execute(
                    "DELETE FROM events WHERE timestamp < ?",
                    (event_cutoff.isoformat(),)
                )

                await self._conn.commit()
                logger.info(
                    f"[Database] Applied retention policy: "
                    f"conversations >{CONVERSATION_RETENTION_DAYS}d, "
                    f"events >{EVENT_RETENTION_DAYS}d, "
                    f"min {MIN_RECENT_MESSAGES_PER_SESSION} recent msgs/session"
                )
            except Exception as e:
                logger.error(f"[Database] Retention policy failed: {e}", exc_info=True)
