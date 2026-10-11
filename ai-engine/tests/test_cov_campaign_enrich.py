"""Coverage-gap tests for campaign.orchestrator_enrich (progress, maps, dedup,
cut-off handling, entity splitting, saves, conflict reporting)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

from campaign import orchestrator_enrich as oe
from campaign.obsidian_sync import get_campaign_folder
from campaign.orchestrator import CampaignOrchestrator
from campaign.vault import CampaignStore


def _vault(tmp_path, name="Test Camp", data=None):
    vault = tmp_path / "vault"
    folder = get_campaign_folder(vault, name)
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps(data or {"campaign": {"name": name}, "npcs": []}))
    return vault, folder


def _resp(content, finish="stop"):
    r = MagicMock(status_code=200, text="")
    r.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    return r


def _llm(handler):
    client = MagicMock()
    client.post = AsyncMock(side_effect=handler)
    return client


def _kind(json_body):
    system = json_body["messages"][0]["content"]
    if "extending the world lore" in system:
        return "delta"
    if "Extract the lore entities" in system:
        return "entities"
    return "notes"


def _run(orch, vault, llm, **kw):
    return asyncio.run(orch.enrich_campaign("Test Camp", llm, vault_path=str(vault), **kw))


# ─── _halve ───────────────────────────────────────────────────────────────


def test_halve_by_note_lone_small_note_and_lone_big_note():
    assert oe._halve(["a", "b", "c"]) == (["a"], ["b", "c"])
    assert oe._halve(["x" * 100]) is None
    big = "p" * 5000 + "\n\n" + "q" * 5000
    first, second = oe._halve([big])
    assert first == ["p" * 5000] and second == ["q" * 5000]
    # no paragraph break at all: cut at the middle
    first, second = oe._halve(["z" * 10_000])
    assert len(first[0]) == len(second[0]) == 5000


# ─── progress callback / error paths ──────────────────────────────────────


def test_progress_callback_receives_messages_and_a_raising_one_is_swallowed(tmp_path):
    vault, folder = _vault(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("Lord Soth rules Dargaard Keep and much else besides.")
    seen = []

    def cb(msg, stage, extra):
        seen.append((stage, extra))
        raise RuntimeError("callback bug")

    async def handler(url, headers=None, json=None, timeout=None):
        k = _kind(json)
        if k == "delta":
            return _resp("===WORLD===\nNone\n===HISTORY===\nNone\n===CONFLICTS===\n===END===")
        if k == "entities":
            return _resp('{"npcs": [], "locations": [], "factions": [], "artifacts": []}')
        return _resp("notes")

    res = _run(CampaignOrchestrator(), vault, _llm(handler), source_path=str(src), on_progress=cb)
    assert res["status"] == "ok" and res["sources"][0]["id"] == "a-md"
    assert seen and all(s == ("enrich", "") for s in seen)


def test_missing_source_path_and_journals_without_client_report_errors(tmp_path):
    vault, _ = _vault(tmp_path)
    res = _run(CampaignOrchestrator(), vault, MagicMock(), source_path=str(tmp_path / "gone"))
    assert res["status"] == "error" and "Source path does not exist" in res["error"]
    res = _run(CampaignOrchestrator(), vault, MagicMock(), journal_pack="world.pack")
    assert res["status"] == "error" and "connected Foundry client is required" in res["error"]


def test_collect_sources_reads_journal_pack_and_folder_and_skips_empty():
    orch = CampaignOrchestrator()
    good = {"pages": [{"html": "<p>" + "Dragon lore. " * 10 + "</p>"}]}
    orch._fetch_journal_pack = AsyncMock(return_value=[good])
    orch._fetch_world_journals = AsyncMock(return_value=[{"pages": [{"html": "<p>tiny</p>"}]}])
    out = asyncio.run(orch._enrich_collect_sources(None, "pack.x", "My Folder", object()))
    assert [(s["id"], s["title"], s["type"], s["path"]) for s in out] == [("pack-x", "pack.x", "foundry", "pack.x")]
    assert out[0]["pages"][0][0] == 1 and out[0]["pages"][0][1].startswith("Dragon lore.")
    assert orch._fetch_journal_pack.await_args.args[1] == "pack.x"
    assert orch._fetch_world_journals.await_args.args[1] == "My Folder"


# ─── reference maps ───────────────────────────────────────────────────────


def test_maps_only_source_keeps_map_once_and_skips_unreadable(tmp_path):
    vault, folder = _vault(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (80, 40), "blue").save(src / "Krynn Map.png")
    (src / "Broken Map.png").write_bytes(b"not a png")
    orch = CampaignOrchestrator()
    res = _run(orch, vault, MagicMock(), source_path=str(src))
    assert res["status"] == "ok" and res["sources"] == []
    assert res["maps"] == ["krynn map-png"]
    data = json.loads((folder / "campaign.json").read_text())
    rec = data["reference_maps"][0]
    assert (rec["id"], rec["title"], rec["width"], rec["height"]) == ("krynn map-png", "Krynn Map", 80, 40)
    assert (folder / "Maps" / "Reference" / "krynn map-png.jpg").exists()
    note = (folder / "Maps" / "Reference" / "krynn map-png.md").read_text()
    assert "![[krynn map-png.jpg]]" in note and "Krynn Map.png" in note
    assert not (folder / "Maps" / "Reference" / "broken map-png.jpg").exists()
    # second run: already kept -> nothing new, save untouched
    again = _run(orch, vault, MagicMock(), source_path=str(src))
    assert again["maps"] == []


def test_enrich_maps_with_all_unreadable_saves_nothing(tmp_path):
    vault, folder = _vault(tmp_path)
    store = CampaignStore("Test Camp", str(vault))
    bad = tmp_path / "bad map.png"
    bad.write_bytes(b"junk")
    data = {"campaign": {}}
    ids = asyncio.run(CampaignOrchestrator()._enrich_maps(
        store, data, [{"id": "bad", "title": "bad", "path": str(bad)}], lambda m: None))
    assert ids == [] and "reference_maps" in data and data["reference_maps"] == []


# ─── duplicate detection / recorded fingerprints ──────────────────────────


def test_second_edition_with_same_text_is_skipped_unless_forced(tmp_path):
    vault, folder = _vault(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    text = " ".join(f"word{i}" for i in range(60)) + " Soth rules Dargaard."
    (src / "a full color.md").write_text(text)
    (src / "b printer friendly.md").write_text(text)

    async def handler(url, headers=None, json=None, timeout=None):
        k = _kind(json)
        if k == "delta":
            return _resp("===WORLD===\nNone\n===HISTORY===\nNone\n===CONFLICTS===\n===END===")
        if k == "entities":
            return _resp('{"npcs": []}')
        return _resp("n")

    res = _run(CampaignOrchestrator(), vault, _llm(handler), source_path=str(src))
    assert [s["id"] for s in res["sources"]] == ["a full color-md"]
    assert res["skipped"] == ["b printer friendly-md"]
    forced = _run(CampaignOrchestrator(), vault, _llm(handler), source_path=str(src), force=True)
    assert len(forced["sources"]) == 2 and forced["skipped"] == []


def test_recorded_fingerprints_rereads_path_and_drops_unreadable(tmp_path):
    f = tmp_path / "old.md"
    f.write_text("alpha beta gamma delta epsilon zeta eta theta iota kappa")
    data = {"sources": [
        {"id": "old-md", "title": "Old", "type": "md", "path": str(f)},
        {"id": "gone-md", "title": "Gone", "type": "md", "path": str(tmp_path / "gone.md")},
        {"id": "fnd", "title": "J", "type": "foundry", "path": "pack"},
        {"id": "has", "type": "pdf", "path": "x", "fingerprint": [1, 2]},
    ]}
    known = CampaignOrchestrator._recorded_fingerprints(data)
    assert set(known) == {"old-md", "has"}
    assert known["old-md"][0] == "Old" and known["old-md"][1]
    assert data["sources"][0]["fingerprint"] == known["old-md"][1]   # kept on the record
    assert known["has"] == ("has", [1, 2])                           # title falls back to id


# ─── delta cut-off handling ───────────────────────────────────────────────


def _delta_client(cut_flags, calls):
    flags = list(cut_flags)

    async def handler(url, headers=None, json=None, timeout=None):
        k = _kind(json)
        if k == "delta":
            calls.append(json["messages"][-1]["content"])
            cut = flags.pop(0) if flags else False
            return _resp("===WORLD===\nThe Blood Sea is a maelstrom.\n===HISTORY===\nNone\n===CONFLICTS===\n===END===",
                         "length" if cut else "stop")
        if k == "entities":
            return _resp('{"npcs": []}')
        return _resp("note body " * 30)

    return _llm(handler)


def _one_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("Lord Soth rules Dargaard Keep and the Blood Sea is a maelstrom of old.")
    return src


def test_cut_off_small_group_is_retried_then_kept(tmp_path):
    vault, folder = _vault(tmp_path)
    calls = []
    res = _run(CampaignOrchestrator(), vault, _delta_client([True, True, True], calls),
               source_path=str(_one_source(tmp_path)))
    assert len(calls) == 3  # initial + two retries; third cut-off is kept
    assert res["sources"][0]["world_added"] is True
    assert "Blood Sea" in (folder / "Worldbuilding.md").read_text()


def test_cut_off_group_is_redone_in_halves_when_it_can_be_split(tmp_path, monkeypatch):
    vault, folder = _vault(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("Para one about Soth.\n\n" + "filler words here. " * 400)
    monkeypatch.setattr("campaign.orchestrator_enrich.chunk_pages", lambda pages: ["c1", "c2"])
    calls = []
    res = _run(CampaignOrchestrator(), vault, _delta_client([True, False, False], calls), source_path=str(src))
    # first call has both notes, cut off; then each half answered separately
    assert len(calls) == 3
    assert calls[0].count("note body") == 60 and calls[1].count("note body") == 30 and calls[2].count("note body") == 30
    assert res["status"] == "ok"
    assert (folder / "Worldbuilding.md").read_text().count("Blood Sea") >= 1


def test_enrich_delta_flags_length_finish_and_returns_parsed_sections():
    orch = CampaignOrchestrator()
    client = _llm(AsyncMock(return_value=_resp("===WORLD===\nW\n===HISTORY===\nH\n===CONFLICTS===\n===END===", "length")))
    w, h, conflicts, cut = asyncio.run(orch._enrich_delta(client, "u", {}, "", "", "notes", "T"))
    assert (w.strip(), h.strip(), conflicts, cut) == ("W", "H", [], True)
    client = _llm(AsyncMock(return_value=_resp("", "length")))
    assert asyncio.run(orch._enrich_delta(client, "u", {}, "", "", "notes", "T"))[3] is True


# ─── entity splitting ─────────────────────────────────────────────────────


def test_entities_split_reraises_transport_errors_without_splitting():
    orch = CampaignOrchestrator()
    orch._enrich_entities = AsyncMock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        asyncio.run(orch._enrich_entities_split(None, "u", {}, ["a", "b"], lambda m: None))
    assert orch._enrich_entities.await_count == 1


def test_entities_split_lone_small_note_raises_after_its_retries():
    orch = CampaignOrchestrator()
    orch._enrich_entities = AsyncMock(side_effect=ValueError("bad json"))
    with pytest.raises(ValueError):
        asyncio.run(orch._enrich_entities_split(None, "u", {}, ["tiny"], lambda m: None))
    assert orch._enrich_entities.await_args.kwargs["max_attempts"] == 3


def test_entities_split_halves_on_failure_and_collects_results():
    orch = CampaignOrchestrator()

    async def fake(client, endpoint, headers, notes, max_attempts):
        if "\n\n---\n\n" in notes:
            raise ValueError("unusable")
        return {"npcs": [{"name": notes}]}

    orch._enrich_entities = fake
    msgs = []
    out = asyncio.run(orch._enrich_entities_split(None, "u", {}, ["a", "b"], msgs.append))
    assert out == [{"npcs": [{"name": "a"}]}, {"npcs": [{"name": "b"}]}]
    assert any("came back unusable (ValueError); splitting it" in m for m in msgs)


# ─── aliases / save / report ──────────────────────────────────────────────


def test_enrich_aliases_only_asks_llm_for_unmatched_npcs_and_locations():
    orch = CampaignOrchestrator()
    orch._semantic_match_names = AsyncMock(return_value={"Kansaldi": "Kansaldi Fire-Eyes"})
    existing = [{"name": "Kansaldi Fire-Eyes"}, {"name": "Haven"}, {}]
    incoming = [{"name": "Kansaldi"}, {"name": "haven"}, {"name": ""}]
    out = asyncio.run(orch._enrich_aliases(None, "npcs", existing, incoming))
    assert out == {"Kansaldi": "Kansaldi Fire-Eyes"}
    args = orch._semantic_match_names.await_args.args
    assert args[1] == "npc" and args[2] == [{"name": "Kansaldi"}]
    assert args[3] == [{"name": "Kansaldi Fire-Eyes", "uuid": "Kansaldi Fire-Eyes"}, {"name": "Haven", "uuid": "Haven"}]
    orch._semantic_match_names.reset_mock()
    assert asyncio.run(orch._enrich_aliases(None, "factions", existing, incoming)) == {}
    assert asyncio.run(orch._enrich_aliases(None, "npcs", [], incoming)) == {}
    assert asyncio.run(orch._enrich_aliases(None, "npcs", existing, [{"name": "Haven"}])) == {}
    orch._semantic_match_names.assert_not_awaited()


def test_enrich_save_writes_only_what_changed(tmp_path, monkeypatch):
    from campaign import obsidian_sync as vault_mod

    vault, folder = _vault(tmp_path)
    store = CampaignStore("Test Camp", str(vault))
    calls = {}
    for name in ("save_npc_notes", "save_location_notes", "save_factions", "save_artifacts"):
        monkeypatch.setattr(vault_mod, name, AsyncMock(side_effect=lambda *a, _n=name, **k: calls.setdefault(_n, a)))
    data = {"campaign": {}, "factions": [{"name": "F"}], "artifacts": [{"name": "A"}]}
    changed = {"npcs": {"N": {"name": "N"}}, "locations": {"L": {"name": "L"}},
               "factions": {"F": {}}, "artifacts": {"A": {}}}
    orch = CampaignOrchestrator()
    asyncio.run(orch._enrich_save(store, data, "W", "H", True, True, changed))
    assert (folder / "Worldbuilding.md").read_text() == "W"
    assert (folder / "History.md").read_text() == "H"
    assert calls["save_npc_notes"][1] == {"npcs": [{"name": "N"}]}
    assert calls["save_location_notes"][1] == {"locations": [{"name": "L"}]}
    assert calls["save_factions"][1] == {"factions": [{"name": "F"}]}
    assert calls["save_artifacts"][1] == {"artifacts": [{"name": "A"}]}
    calls.clear()
    empty = {s: {} for s in ("npcs", "locations", "factions", "artifacts")}
    asyncio.run(orch._enrich_save(store, data, "W", "H", False, False, empty))
    assert calls == {}


def test_enrich_report_labels_and_failure_modes():
    orch = CampaignOrchestrator()
    got = []

    async def sink(claim, why, existing):
        got.append((claim, why, existing))

    assert asyncio.run(orch._enrich_report(None, "T", {"claim": "c"})) is False
    ok = asyncio.run(orch._enrich_report(sink, "T", {"entity": "Soth", "field": "npcs", "claim": "c", "existing": "e"}))
    assert ok is True and got[-1] == ("c", "From source 'T' (Soth — npcs)", "e")
    asyncio.run(orch._enrich_report(sink, "T", {"field": "world", "claim": "c"}))
    assert got[-1] == ("c", "From source 'T' (world)", "")
    asyncio.run(orch._enrich_report(sink, "T", {"claim": "c"}))
    assert got[-1][1] == "From source 'T' (world)"

    async def refuses(*a):
        return False

    async def boom(*a):
        raise RuntimeError("db down")

    assert asyncio.run(orch._enrich_report(refuses, "T", {"claim": "c"})) is False
    assert asyncio.run(orch._enrich_report(boom, "T", {"claim": "c"})) is False


# ─── campaign.enrichment helpers ──────────────────────────────────────────


def test_read_text_pages_empty_file_has_no_pages(tmp_path):
    from campaign.enrichment import read_text_pages

    f = tmp_path / "e.md"
    f.write_text("  \n ")
    assert read_text_pages(f) == []


def test_load_sources_skips_unreadable_pdf_but_reraises_missing_pypdf(tmp_path, monkeypatch):
    from campaign import enrichment as en

    (tmp_path / "bad.pdf").write_bytes(b"%PDF broken")
    (tmp_path / "ok.md").write_text("Solamnia is a realm of knights.")
    monkeypatch.setattr(en, "extract_pdf_text", lambda p: (_ for _ in ()).throw(ValueError("encrypted")))
    assert [s["id"] for s in en.load_sources(str(tmp_path))] == ["ok-md"]
    monkeypatch.setattr(en, "extract_pdf_text", lambda p: (_ for _ in ()).throw(ImportError("pypdf")))
    with pytest.raises(ImportError):
        en.load_sources(str(tmp_path))


def test_append_section_separator_variants():
    from campaign.enrichment import append_section

    assert append_section("", "x", "S") == "## From S\n\nx\n"
    assert append_section("a\n\n", "x", "S") == "a\n\n## From S\n\nx\n"
    assert append_section("a\n", "x", "S") == "a\n\n## From S\n\nx\n"
    assert append_section("a", "x", "S") == "a\n\n## From S\n\nx\n"


def test_merge_entities_ignores_nameless_incoming_items():
    from campaign.enrichment import merge_entities

    items, stats, conflicts = merge_entities([{"name": "A"}], [{"name": "  "}, {"role": "x"}], "s")
    assert items == [{"name": "A"}] and stats == {"added": 0, "enriched": 0} and conflicts == []
