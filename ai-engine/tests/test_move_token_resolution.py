#!/usr/bin/env python3
"""
Regression test: move_token resolves the identifier inside Foundry.

Live play showed move_token fail 'Entity not found' when the LLM passed an
actor uuid ('Actor.IMmMlM4zG7QSuMQ7') instead of the scene token id — and the
relay's strict lookup couldn't recover. foundry.move_token now resolves the
target in Foundry (by token id / actor uuid / actor id / name) via execute-js,
and execute_move_token reports success/failure from that result.

Run:
    cd ai-engine && python -m pytest tests/test_move_token_resolution.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.executors import execute_move_token
from foundry.client import FoundryClient


def test_foundry_move_token_resolves_via_js():
    c = FoundryClient()
    c.execute_js = AsyncMock(return_value={"result": {"ok": True, "id": "HrfuNyKPqxoO4HZY", "name": "Beringar"}})
    out = asyncio.run(c.move_token("Actor.IMmMlM4zG7QSuMQ7", 300, 400))
    assert out.get("ok") is True
    # The resolution JS must reference the identifier and match by actorId.
    js = c.execute_js.await_args.args[0]
    assert "IMmMlM4zG7QSuMQ7" in js and "actorId" in js


def test_execute_move_token_reports_success():
    f = AsyncMock()
    f.move_token = AsyncMock(return_value={"ok": True, "id": "TOK", "name": "Beringar"})
    out = asyncio.run(execute_move_token("Actor.IMmMlM4zG7QSuMQ7", 100, 200, foundry=f))
    f.move_token.assert_awaited_once_with("Actor.IMmMlM4zG7QSuMQ7", 100, 200)
    assert out["success"] is True


def test_execute_move_token_reports_failure():
    f = AsyncMock()
    f.move_token = AsyncMock(return_value={"ok": False, "error": "token not found"})
    out = asyncio.run(execute_move_token("Ghost", 1, 2, foundry=f))
    assert out["success"] is False


if __name__ == "__main__":
    test_foundry_move_token_resolves_via_js()
    print("PASS  foundry.move_token resolves via execute-js")
    test_execute_move_token_reports_success()
    print("PASS  execute_move_token reports success")
    test_execute_move_token_reports_failure()
    print("PASS  execute_move_token reports failure")
    print("All move-token-resolution tests passed.")
