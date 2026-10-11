"""Coverage-gap tests for orchestrator_import fetch helpers and misc paths."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.orchestrator import CampaignOrchestrator


def _client(*results):
    c = MagicMock()
    c.execute_js = AsyncMock(side_effect=list(results))
    return c


def test_world_rolltables_group_by_chapter_and_drop_foreign_or_rootless_tables():
    tables = [
        {"name": "Rumours", "folder": "Dragons of Krynn / Chapter 1", "results": ["a"]},
        {"name": "Events", "folder": "Dragons of Krynn / Chapter 1", "results": ["b"]},
        {"name": "Weather", "folder": "Dragons of Krynn / Chapter 2", "results": []},
        {"name": "Other Book", "folder": "Waterdeep / Chapter 9", "results": []},
        {"name": "Loose", "folder": None, "results": []},
        {"name": "Root", "folder": "Dragons of Krynn", "results": []},
    ]
    out = asyncio.run(CampaignOrchestrator()._fetch_world_rolltables(
        _client({"result": {"tables": tables}}), "Dragons of Krynn"))
    assert {k: [t["name"] for t in v] for k, v in out.items()} == {
        "Chapter 1": ["Rumours", "Events"], "Chapter 2": ["Weather"], "Dragons of Krynn": ["Root"]}


def test_world_rolltables_empty_on_error_payload_exception_or_no_match():
    o = CampaignOrchestrator()
    assert asyncio.run(o._fetch_world_rolltables(_client({"result": {"error": "x"}}), "C")) == {}
    assert asyncio.run(o._fetch_world_rolltables(_client({"result": "weird"}), "C")) == {}
    bad = MagicMock()
    bad.execute_js = AsyncMock(side_effect=RuntimeError("ws"))
    assert asyncio.run(o._fetch_world_rolltables(bad, "C")) == {}
    assert asyncio.run(o._fetch_world_rolltables(_client({"result": {"tables": []}}), "C")) == {}


def test_journal_pack_fetches_only_adventure_entries_and_skips_failed_documents():
    index = {"result": {"entries": [{"id": "d1", "name": "Chapter 1: Start"},
                                    {"id": "d2", "name": "Players Handbook"},
                                    {"id": "d3", "name": "Appendix A"},
                                    {"id": "d4", "name": "Chapter 2"}]}}
    docs = [{"result": {"name": "Chapter 1: Start", "pages": [{"html": "x"}]}},
            {"result": {"error": "Document not found: d3"}},
            {"result": "weird"}]
    client = _client(index, *docs)
    out = asyncio.run(CampaignOrchestrator()._fetch_journal_pack(client, "world.journals"))
    assert out == [{"name": "Chapter 1: Start", "pages": [{"html": "x"}]}]
    assert client.execute_js.await_count == 4  # index + 3 adventure docs, handbook never fetched
    assert "'d1'" in client.execute_js.await_args_list[1].args[0]


def test_journal_pack_all_adventure_entries_and_index_failures():
    o = CampaignOrchestrator()
    ok = _client({"result": {"entries": [{"id": "a", "name": "Chapter 1"}]}},
                 {"result": {"name": "Chapter 1", "pages": []}})
    assert asyncio.run(o._fetch_journal_pack(ok, "p")) == [{"name": "Chapter 1", "pages": []}]
    with pytest.raises(RuntimeError, match="no such pack"):
        asyncio.run(o._fetch_journal_pack(_client({"result": {"error": "no such pack"}}), "p"))
    with pytest.raises(RuntimeError, match="Journal pack index query failed"):
        asyncio.run(o._fetch_journal_pack(_client({"result": None}), "p"))
