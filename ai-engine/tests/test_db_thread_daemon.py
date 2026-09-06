#!/usr/bin/env python3
"""The aiosqlite worker thread behind a Database must be a daemon.

A non-daemon worker blocks interpreter exit, so one Database left unclosed —
which is what a test whose assertion fires before its `await db.close()`
leaves behind — hangs the entire pytest run until CI's job timeout kills it
(CKP-139). `Database.init()` marks the thread a daemon so that degrades to a
warning instead.

The mechanism is version-sensitive: through aiosqlite 0.21 `Connection` is
itself a `Thread`, and from 0.22 it holds one in `_thread`. A plain
`connection.daemon = True` silently becomes a stray attribute on the newer
shape, restoring the hang with no visible symptom. These tests fail loudly if
an upgrade moves the thread somewhere `_connection_worker` does not look.

Run:
    cd ai-engine && python -m pytest tests/test_db_thread_daemon.py -v
"""

import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

from persistence.db import Database, _connection_worker, _mark_worker_daemon


def test_worker_thread_is_locatable_on_this_aiosqlite():
    """Guards the version coupling itself, so an upgrade that relocates the
    thread fails here rather than silently reintroducing the hang.

    Nothing is torn down: on every release so far `connect()` only constructs
    the worker and `__await__` starts it, so an un-awaited connection owns no
    running thread. Stopping it explicitly would mean naming a private API
    that itself moved in 0.22 (`_stop_running` became `stop`), which would
    fail this guard on precisely the upgrade it exists to validate. Marking
    the worker a daemon keeps that harmless even if some future release does
    start it eagerly.
    """
    connection = aiosqlite.connect(":memory:")
    _mark_worker_daemon(connection)

    worker = _connection_worker(connection)
    assert isinstance(worker, threading.Thread), (
        f"aiosqlite {getattr(aiosqlite, '__version__', '?')} keeps its worker "
        "thread somewhere _connection_worker does not look"
    )


def test_open_database_runs_on_a_daemon_thread():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            worker = _connection_worker(db._conn)
            assert worker is not None
            assert worker.daemon, "an unclosed Database would block interpreter exit"
        finally:
            await db.close()

    asyncio.run(run())
