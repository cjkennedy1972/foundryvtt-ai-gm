#!/usr/bin/env python3
"""Automated SQLite database backup utility.

Snapshots the database (including committed WAL content) to a timestamped backup directory.
Designed to be run via cron (e.g., every 6 hours).

Usage:
    python3 backup_db.py [--max-backups N] [--db-path PATH]

Defaults:
    --max-backups: 30 (oldest backups are pruned)
    --db-path:     env VAR DATABASE_URL or defaults to the game database path

Environment Variables:
    DATABASE_URL:  Path to the SQLite database (default: ./data/game.db)
    BACKUP_DIR:    Directory for backups (default: ./backups)
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def get_backup_config():
    """Read database path and backup directory from env or defaults."""
    db_path = os.environ.get("DATABASE_URL", "./data/game.db")
    backup_dir = os.environ.get("BACKUP_DIR", "./backups")
    return db_path, backup_dir


def backup_db(db_path: str, backup_dir: str, max_backups: int = 30) -> str:
    """Create a timestamped, consistent snapshot of the database.

    Args:
        db_path: Path to the SQLite database file.
        backup_dir: Directory to store backups.
        max_backups: Maximum number of backups to keep (oldest pruned).

    Returns:
        Path to the created backup directory.
    """
    db = Path(db_path)
    if not db.exists():
        print(f"[backup] Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{timestamp}"
    backup_full = backup_path / backup_name
    backup_full.mkdir(parents=True, exist_ok=True)

    # SQLite's online backup API takes a consistent snapshot of a live WAL database in one
    # step. Copying the main file and its -wal/-shm separately could pair files from
    # different moments if a writer committed in between.
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_full / db.name)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    print(f"[backup] Created: {backup_full}")

    # Prune old backups
    backups = sorted(backup_path.glob("backup_*"), key=lambda p: p.name)
    while len(backups) > max_backups:
        old = backups.pop(0)
        shutil.rmtree(old)
        print(f"[backup] Pruned old backup: {old}")

    return str(backup_full)


def main():
    parser = argparse.ArgumentParser(description="Backup SQLite database")
    parser.add_argument("--max-backups", type=int, default=30,
                        help="Maximum backups to keep (default: 30)")
    parser.add_argument("--db-path", default=None,
                        help="Override DATABASE_URL env var")
    parser.add_argument("--backup-dir", default=None,
                        help="Override BACKUP_DIR env var")
    args = parser.parse_args()

    db_path = args.db_path or os.environ.get("DATABASE_URL", "./data/game.db")
    backup_dir = args.backup_dir or os.environ.get("BACKUP_DIR", "./backups")

    backup_db(db_path, backup_dir, args.max_backups)


if __name__ == "__main__":
    main()
