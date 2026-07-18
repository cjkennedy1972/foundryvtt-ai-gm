"""Tests for the deterministic campaign prologue flow."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import campaign.prologue as prologue


class StubFoundry:
    def __init__(self, execute_js_side_effect=None):
        self.calls = []
        self.execute_js = AsyncMock(side_effect=execute_js_side_effect or self._execute_js)

    async def _execute_js(self, code: str):
        self.calls.append(code)
        if "setFlag" in code:
            return {"result": True}
        if "ImagePopout" in code:
            return {"result": True}
        return {"result": None}


def test_build_prologue_pages_alternates_and_captions():
    pages = prologue.build_prologue_pages(
        {
            "title": "The Weave of the Sundered Oath",
            "frame_narrative": "A chronicler opens the archive.",
            "panels": [
                {
                    "title": "The Age of Concord",
                    "body": "The realms stood whole.",
                    "image_prompt": "ancient hall",
                    "era": "ancient",
                    "image_file": "panel_01.png",
                },
                {
                    "title": "The Sundering",
                    "body": "The oath broke and the world burned.",
                    "image_prompt": "ruined citadel",
                    "era": "mythic",
                    "image_src": "ai-gm-prologue/test/panel_02.png",
                },
            ],
        }
    )

    assert [page["type"] for page in pages] == ["text", "image", "text", "image", "text"]
    assert pages[1]["text"]["content"] == "<p><strong>The Age of Concord</strong></p>"
    assert pages[3]["text"]["content"] == "<p><strong>The Sundering</strong></p>"
    assert all(page["name"] != "Epilogue" for page in pages)


def test_present_prologue_marks_shown_and_shortens_dwell(monkeypatch):
    foundry = StubFoundry()
    interrupt = asyncio.Event()
    narrated = []
    sleep_calls = []

    async def narrate(text: str):
        narrated.append(text)
        interrupt.set()

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr(prologue.asyncio, "sleep", fake_sleep)

    entry = {
        "uuid": "JournalEntry.abc123",
        "title": "Chronicle of Dawn",
        "vessel": "tapestry",
        "shown": False,
        "pages": [
            {
                "name": "The Age of Concord",
                "type": "image",
                "src": "ai-gm-prologue/test/panel_01.png",
                "content": "",
            },
            {
                "name": "The Age of Concord",
                "type": "text",
                "content": "<h2>The Age of Concord</h2><p>The realms stood whole.</p>",
            },
        ],
    }

    ok = asyncio.run(
        prologue.present_prologue(
            foundry,
            narrate,
            entry["uuid"],
            interrupt_event=interrupt,
            entry=entry,
        )
    )

    assert ok is True
    assert narrated == ["The Age of Concord The realms stood whole."]
    assert sleep_calls == [1.0]
    assert any("setFlag" in call and "shown" in call and "true" in call for call in foundry.calls)
    assert any("ImagePopout" in call for call in foundry.calls)


def test_present_prologue_noops_when_already_shown():
    foundry = StubFoundry()
    narrated = AsyncMock()
    entry = {
        "uuid": "JournalEntry.abc123",
        "title": "Chronicle of Dawn",
        "vessel": "tapestry",
        "shown": True,
        "pages": [],
    }

    ok = asyncio.run(
        prologue.present_prologue(
            foundry,
            narrated,
            entry["uuid"],
            entry=entry,
        )
    )

    assert ok is False
    narrated.assert_not_awaited()
    foundry.execute_js.assert_not_awaited()


def test_reset_prologue_shown_clears_flag():
    entry = {
        "uuid": "JournalEntry.abc123",
        "title": "Chronicle of Dawn",
        "vessel": "tapestry",
        "shown": True,
        "pages": [],
    }

    async def execute_js(code: str):
        foundry.calls.append(code)
        if "fromUuid" in code:
            return {"result": entry}
        return {"result": True}

    foundry = StubFoundry(execute_js_side_effect=execute_js)

    ok = asyncio.run(prologue.reset_prologue_shown(foundry, entry["uuid"]))

    assert ok is True
    assert any("setFlag" in call and "shown" in call and "false" in call for call in foundry.calls)
