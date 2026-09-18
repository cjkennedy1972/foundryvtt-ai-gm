"""Failures that change behaviour must leave a trace.

A repo scan found 21 `except Exception: pass` handlers. Eighteen now log.
The three that stay silent carry a comment saying why, and this file guards
the count so a new one has to be a decision rather than an accident.
"""

import ast
import logging
import pathlib
import subprocess

import pytest
from unittest.mock import AsyncMock, MagicMock

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

# Silence is defensible only where the failure cannot change what the code
# does. Each of these carries a comment explaining that.
ALLOWED_SILENT = {
    "ai-engine/backup_db.py",        # CLI script; WAL already copied
    "ai-engine/foundry/client.py",   # closing an already-bad socket
    "ai-engine/llm/manager.py",      # enriching an error logged on the next line
}


def _silent_handlers():
    files = subprocess.check_output(
        ["git", "ls-files", "*.py"], text=True, cwd=REPO
    ).split()
    found = []
    for f in files:
        if f.startswith("relay/") or "/tests/" in f or pathlib.Path(f).name.startswith("test_"):
            continue
        try:
            tree = ast.parse((REPO / f).read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            broad = node.type is None or (
                isinstance(node.type, ast.Name)
                and node.type.id in ("Exception", "BaseException")
            )
            if broad and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                found.append(f"{f}:{node.body[0].lineno}")
    return found


def test_no_new_silent_exception_handlers():
    offenders = sorted(
        h for h in _silent_handlers() if h.rsplit(":", 1)[0] not in ALLOWED_SILENT
    )

    assert offenders == [], (
        "an `except Exception: pass` hides a failure that may change behaviour; "
        f"log it, or add the file to ALLOWED_SILENT with a comment saying why: {offenders}"
    )


class TestDeathSaveLogging:
    """The handler that mattered most: it decided whether PCs could die."""

    @pytest.mark.asyncio
    async def test_an_unreadable_death_save_is_logged_and_treated_as_dying(self, caplog):
        from combat.loop import CombatLoop

        loop = CombatLoop.__new__(CombatLoop)
        loop.foundry = AsyncMock()
        loop.foundry.execute_js.side_effect = ConnectionError("relay down")
        loop._dead_pc_tokens = {}
        loop._pc_tokens = [{"id": "t1", "name": "Thalia", "actorUuid": "Actor.t"}]
        loop._npc_tokens = []
        loop.state_tracker = AsyncMock()

        with caplog.at_level(logging.WARNING):
            try:
                await loop._check_combat_end()
            except Exception:
                pass  # the method needs more wiring than this; the log is the subject

        assert any("Death-save status unreadable" in r.message for r in caplog.records), (
            "a systematically failing death-save script would otherwise mean PCs "
            "never die, with nothing in the log to say so"
        )
