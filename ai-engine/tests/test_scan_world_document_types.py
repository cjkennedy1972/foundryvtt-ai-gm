#!/usr/bin/env python3
"""The world scan was wrong in every section.

scan_world is what campaign/orchestrator calls to learn what a world already
holds before it deploys into it. Run against a live Foundry v13 world with
dnd5e installed, it reported:

    world  : {"name": "Unknown", "version": "", "systems": []}
    scenes : 0
    items  : 63   ['Crafting Nonmagical Items', 'Magic Item Lists']
    journal: 0
    quests : 4    ['Combat', 'Boon of Combat Prowess']

Two causes. It read world metadata and scenes out of get_structure(), which
returns the FOLDER tree — {"data": {"folders": {}}} — and has neither key. And
it found items, journals and combats with `query="item"`, `query="journal"`,
`query="combat"`: full-text searches over every document, so "items" were
rules pages, "combats" were items, and "journal" matched nothing at all.

The relay's search takes `filter=documentType:Item` for exactly this. The same
world now reports:

    world  : Test World, 13.351, dnd5e 5.0.4
    scenes : 2    ['Foundry Virtual Tabletop', 'test']
    items  : 200  ['Abacus', 'Aberrant Ground']
    journal: 73   ['Actions', 'Animals']
    quests : 0

Run:
    cd ai-engine && python -m pytest tests/test_scan_world_document_types.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient

WORLD_JS = {"result": {
    "name": "Test World", "id": "test-world", "version": "13.351",
    "system": "dnd5e", "systemVersion": "5.0.4",
    "totalActors": 3, "totalItems": 200,
}}


def _client(search_by_type=None, js=WORLD_JS):
    """A client whose relay answers per documentType, as the real one does."""
    by_type = search_by_type or {}
    sent = []

    async def _search(msg_type, max_retries=1, **kwargs):
        sent.append(kwargs)
        f = kwargs.get("filter", "")
        dt = f.split(":", 1)[1] if ":" in f else None
        return {"data": by_type.get(dt, [])}

    c = FoundryClient()
    c._send_with_retry = _search
    c._send = AsyncMock(return_value={"data": []})
    c.execute_js = AsyncMock(return_value=js)
    c.get_actors = AsyncMock(return_value=[])
    c.get_structure = AsyncMock(return_value={"data": {"folders": {}}})
    c._sent = sent
    return c


# ── searches ask for a document type ──────────────────────────────────────

def test_each_section_asks_for_its_document_type():
    c = _client()

    asyncio.run(c.scan_world())

    filters = [k.get("filter") for k in c._sent]
    for want in ("documentType:Scene", "documentType:Item",
                 "documentType:JournalEntry", "documentType:Combat"):
        assert want in filters, f"{want} not requested; got {filters}"


def test_no_section_runs_a_full_text_search():
    """query="journal" matched nothing at all on a world holding 73 of them."""
    c = _client()

    asyncio.run(c.scan_world())

    assert all("query" not in k for k in c._sent), c._sent
    assert c._send.await_count == 0, "a bare _send('search', query=...) survived"


def test_documents_land_in_their_own_section():
    c = _client({
        "Scene": [{"name": "The Crypt", "uuid": "Scene.1"}],
        "Item": [{"name": "Abacus", "uuid": "Item.1"}],
        "JournalEntry": [{"name": "Actions", "uuid": "JournalEntry.1"}],
    })

    out = asyncio.run(c.scan_world())

    assert [s["name"] for s in out["scenes"]] == ["The Crypt"]
    assert [i["name"] for i in out["items"]] == ["Abacus"]
    assert [j["name"] for j in out["journal"]] == ["Actions"]
    assert out["quests"] == []


# ── world metadata ────────────────────────────────────────────────────────

def test_world_metadata_comes_from_the_world_not_the_folder_tree():
    out = asyncio.run(_client().scan_world())

    assert out["world"]["name"] == "Test World"
    assert out["world"]["version"] == "13.351"
    assert out["world"]["systems"] == [
        {"name": "dnd5e", "version": "5.0.4", "enabled": True}
    ]
    assert out["world"]["totalItems"] == 200


def test_metadata_degrades_to_the_old_shape_when_execute_js_is_gated():
    """A deployment with ALLOW_EXECUTE_JS off must still get a scan, not a
    crash — and the same blank block it got before."""
    c = _client()
    c.execute_js = AsyncMock(side_effect=PermissionError("execute_js disabled"))

    out = asyncio.run(c.scan_world())

    assert out["world"]["name"] == "Unknown"
    assert out["world"]["systems"] == []
    assert out["scenes"] == [] or isinstance(out["scenes"], list)


def test_a_reply_without_a_result_does_not_crash_the_scan():
    c = _client(js={"nope": True})

    out = asyncio.run(c.scan_world())

    assert out["world"]["name"] == "Unknown"


# ── the helper ────────────────────────────────────────────────────────────

def test_search_documents_unwraps_a_nested_payload():
    c = FoundryClient()
    c._send_with_retry = AsyncMock(return_value={"data": {"entries": [{"name": "x"}]}})

    assert asyncio.run(c._search_documents("Item")) == [{"name": "x"}]


def test_search_documents_returns_a_list_for_an_unexpected_shape():
    c = FoundryClient()
    c._send_with_retry = AsyncMock(return_value={"data": "not a list"})

    assert asyncio.run(c._search_documents("Item")) == []
