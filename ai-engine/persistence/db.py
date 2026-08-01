"""Persistence layer — SQLite database for game state, events, and conversations."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

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

    async def init(self):
        """Initialize the database connection and schema."""
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
        # Indexes for faster lookups
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_conversations_session ON ai_conversations(session_id)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_session_info_active ON session_info(active)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_canon_proposals_status ON canon_proposals(status)")
        await self._conn.commit()
        logger.info(f"Database initialized: {self.db_path} (WAL mode)")

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
        """Record a game event under write lock."""
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO events (session_id, description) VALUES (?, ?)",
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

    async def approve_canon_proposal(self, proposal_id: int, final_text: Optional[str] = None) -> None:
        """Mark a proposal approved. If the GM edited the wording before
        approving, final_text overwrites the stored fact so the DB always
        reflects what was actually canonized."""
        async with self._write_lock:
            if final_text:
                await self._conn.execute(
                    "UPDATE canon_proposals SET status = 'approved', fact = ?, reviewed_at = ? WHERE id = ?",
                    (final_text, datetime.now(timezone.utc).isoformat(), proposal_id),
                )
            else:
                await self._conn.execute(
                    "UPDATE canon_proposals SET status = 'approved', reviewed_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), proposal_id),
                )
            await self._conn.commit()

    async def reject_canon_proposal(self, proposal_id: int) -> None:
        """Mark a proposal rejected — it never gets written to Canon.md."""
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE canon_proposals SET status = 'rejected', reviewed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), proposal_id),
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
