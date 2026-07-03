"""Checks for the speak → scene-presence hook (Elara playtest gap)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from actions import executors
from foundry import scripts


class StubFoundry:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def execute_js(self, code):
        self.calls.append(code)
        return {"result": self._result}


def test_ensure_npc_token_js_escapes_names():
    js = scripts.ensure_npc_token("D'Artagnan \"the Blade\"")
    # json.dumps escaping keeps the quote characters inside the JS string literal
    assert 'const want="d\'artagnan \\"the blade\\""' in js
    assert "createEmbeddedDocuments" in js


def test_presence_check_runs_once_per_cooldown():
    executors._npc_presence_checked.clear()
    stub = StubFoundry({"ok": True, "placed": "Elara"})

    asyncio.run(executors._ensure_npc_presence("Elara", stub))
    asyncio.run(executors._ensure_npc_presence("Elara", stub))

    assert len(stub.calls) == 1  # second call within cooldown is skipped
    assert "elara" in stub.calls[0]

    # Scene change resets the cache, so the NPC is re-checked
    asyncio.run(executors._notify_scene_change(None, ""))
    asyncio.run(executors._ensure_npc_presence("Elara", stub))
    assert len(stub.calls) == 2
