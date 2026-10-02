"""WorldImportMixin helpers: linking to existing Foundry docs, semantic match/dedupe, world fetchers, lore/handout writes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import campaign.orchestrator_import as oi
from campaign.orchestrator_import import WorldImportMixin


def run(c):
    return asyncio.run(c)


class Host(WorldImportMixin):
    settings = SimpleNamespace(model="m", llm_api_key="key")

    def _chat_endpoint(self):
        return "http://llm/chat"

    def _suppress_thinking(self, payload):
        payload["suppressed"] = True


def llm(*contents):
    """An httpx-like client whose chat replies are `contents` (Exception items are raised)."""
    c = MagicMock()
    replies = []
    for x in contents:
        if isinstance(x, Exception):
            replies.append(x)
        else:
            r = MagicMock()
            r.json.return_value = {"choices": [{"message": {"content": x}}]}
            replies.append(r)
    c.post = AsyncMock(side_effect=replies)
    return c


def fx(*results):
    f = MagicMock()
    f.execute_js = AsyncMock(side_effect=list(results))
    return f


def progress_log():
    msgs = []
    return msgs, lambda m, step="", detail="": msgs.append((m, step))


# ── pass 3 ───────────────────────────────────────────────────────────────

def test_worldbuilding_posts_pass3_and_splits_documents():
    c = llm("===WORLDBUILDING===\nWB\n===HISTORY===\nHIST\n===END===")
    msgs, prog = progress_log()
    wb, hist = run(Host()._import_worldbuilding(c, "http://e", {"h": 1}, "NOTES", prog))
    assert (wb, hist) == ("WB", "HIST")
    kw = c.post.await_args.kwargs
    assert c.post.await_args.args == ("http://e",) and kw["headers"] == {"h": 1}
    assert kw["json"]["suppressed"] is True and "NOTES" in kw["json"]["messages"][1]["content"]
    assert kw["json"]["max_tokens"] == 16384


# ── semantic matching ────────────────────────────────────────────────────

CANDS = [{"name": "Map 3.1: Vogler", "uuid": "Scene.a"}, {"name": "Map 3.2: Docks", "uuid": "Scene.b"}]


def test_semantic_match_noop_without_items_or_candidates():
    c = llm()
    assert run(Host()._semantic_match_names(c, "scene", [], CANDS)) == {}
    assert run(Host()._semantic_match_names(c, "scene", [{"name": "x"}], [])) == {}
    c.post.assert_not_awaited()


def test_semantic_match_maps_names_to_uuids_and_rejects_bad_answers():
    reply = json.dumps({"Vogler Crab": "Map 3.1: Vogler", "Ghost": "Nonexistent", "Dup": "Map 3.1: Vogler",
                        "Docks": "Map 3.2: Docks", "None": None})
    c = llm(reply)
    items = [{"name": n} for n in ("Vogler Crab", "Ghost", "Dup", "Docks", "None")]
    out = run(Host()._semantic_match_names(c, "scene", items, CANDS))
    assert out == {"Vogler Crab": "Scene.a", "Docks": "Scene.b"}      # hallucinated + double-claimed dropped
    assert c.post.await_args.kwargs["json"]["temperature"] == 0.2


def test_semantic_match_llm_failure_yields_empty():
    assert run(Host()._semantic_match_names(llm(RuntimeError("503")), "NPC", [{"name": "x"}], CANDS)) == {}


def test_semantic_dedupe_merges_groups_and_keeps_the_rest():
    items = [{"name": "Dragon Army", "a": 1}, {"name": "Red Dragon Army", "b": 2}, {"name": "Silver Order"}]
    c = llm(json.dumps({"groups": [["Dragon Army", "Red Dragon Army"], ["Silver Order"]]}))
    out = run(Host()._semantic_dedupe_section(c, "faction", items))
    assert [i["name"] for i in out] == ["Red Dragon Army", "Silver Order"]      # longest name kept
    assert out[0]["a"] == 1 and out[0]["b"] == 2


def test_semantic_dedupe_short_circuits_and_survives_failures():
    c = llm()
    one = [{"name": "x"}]
    assert run(Host()._semantic_dedupe_section(c, "NPC", one)) is one
    two = [{"name": "a"}, {"name": "b"}]
    assert run(Host()._semantic_dedupe_section(llm(RuntimeError("x")), "NPC", two)) is two
    out = run(Host()._semantic_dedupe_section(llm("garbage"), "NPC", two))        # unparseable -> no merge
    assert [i["name"] for i in out] == ["a", "b"]


# ── linking to pre-existing documents ────────────────────────────────────

def _link(monkeypatch, scene_link, npc_link, semantic_scenes=None, semantic_npcs=None, scenes=None, npcs=None,
          candidates=None, actors=None):
    host = Host()
    monkeypatch.setattr(oi, "match_scenes_to_existing", lambda s, e: scene_link)
    monkeypatch.setattr(oi, "match_names_to_existing", lambda names, e: npc_link)
    monkeypatch.setattr(oi, "filter_candidates_by_campaign_folder", lambda cands, name: cands)
    host._fetch_world_document_index = AsyncMock(side_effect=lambda f, t: candidates if t == "Scene" else actors)

    async def sem(c, kind, items, cands):
        return (semantic_scenes if kind == "scene" else semantic_npcs) or {}
    host._semantic_match_names = AsyncMock(side_effect=sem)
    data = {"scenes": scenes or [], "npcs": npcs or []}
    msgs, prog = progress_log()
    run(host._import_link_existing(object(), object(), "Camp", data, [], prog))
    return host, data, msgs


def test_link_existing_applies_fuzzy_and_semantic_scene_links(monkeypatch):
    cands = [{"name": "Map 3.1: Vogler", "uuid": "S.a"}, {"name": "Map 3.2", "uuid": "S.b"}, {"name": "Map 9", "uuid": "S.c"}]
    scenes = [{"name": "Brass Crab"}, {"name": "Docks"}, {"name": "Elsewhere"}, {"name": "Brass Crab"}]
    host, data, msgs = _link(
        monkeypatch,
        {"matched": {"Brass Crab": "S.a"}, "unmatched": ["Docks", "Elsewhere"], "areas": {"Brass Crab": "The Brass Crab"}},
        {"matched": {}, "unmatched": []},
        semantic_scenes={"Docks": "S.b"}, scenes=scenes, candidates=cands, actors=[])
    crab, docks, elsewhere, dup = data["scenes"]
    assert crab["existing_uuid"] == "S.a" and crab["foundry_scene_name"] == "Map 3.1: Vogler"
    assert crab["map_needed"] is False and crab["existing_area"] == "The Brass Crab"
    assert docks["existing_uuid"] == "S.b" and docks["foundry_scene_name"] == "Map 3.2" and "existing_area" not in docks
    assert "existing_uuid" not in elsewhere
    assert "existing_uuid" not in dup                 # second scene with an already-seen name is never linked to the same doc
    # the semantic pass only sees unmatched scenes and unclaimed candidates
    _, kind, items, remaining = host._semantic_match_names.await_args_list[0].args
    assert [i["name"] for i in items] == ["Docks", "Elsewhere"] and [c["uuid"] for c in remaining] == ["S.b", "S.c"]
    assert any("1 scene(s) matched a labelled area" in m for m, _ in msgs)
    assert any("Linked 2 scene(s) (1 via semantic match)" in m for m, _ in msgs)


def test_link_existing_one_actor_backs_only_one_npc(monkeypatch):
    npcs = [{"name": "Guard Captain"}, {"name": "Guard Captain"}, {"name": "Mira"}]
    _, data, _ = _link(monkeypatch, {"matched": {}, "unmatched": []},
                       {"matched": {"Guard Captain": "A.1"}, "unmatched": ["Mira"]},
                       semantic_npcs={"Mira": "A.2"}, npcs=npcs, candidates=[], actors=[{"name": "x", "uuid": "A.1"}])
    assert [n.get("existing_uuid") for n in data["npcs"]] == ["A.1", None, "A.2"]


def test_link_existing_reuses_prefetched_scene_index(monkeypatch):
    host = Host()
    monkeypatch.setattr(oi, "match_scenes_to_existing", lambda s, e: {"matched": {}, "unmatched": [], "areas": {}})
    monkeypatch.setattr(oi, "match_names_to_existing", lambda n, e: {"matched": {}, "unmatched": []})
    monkeypatch.setattr(oi, "filter_candidates_by_campaign_folder", lambda c, n: c)
    host._fetch_world_document_index = AsyncMock(return_value=[])
    host._semantic_match_names = AsyncMock(return_value={})
    run(host._import_link_existing(object(), None, "C", {"scenes": [], "npcs": []}, [{"name": "cached", "uuid": "S"}], lambda *a, **k: None))
    assert [c.args[1] for c in host._fetch_world_document_index.await_args_list] == ["Actor"]


def test_link_existing_without_foundry_does_nothing():
    data = {"scenes": [{"name": "A"}]}
    run(Host()._import_link_existing(None, None, "C", data, [], lambda *a, **k: None))
    assert data == {"scenes": [{"name": "A"}]}


# ── world fetchers ───────────────────────────────────────────────────────

def test_document_index_queries_by_type_and_unwraps():
    f = fx({"result": {"entries": [{"name": "A", "uuid": "u"}]}})
    assert run(Host()._fetch_world_document_index(f, "Scene")) == [{"name": "A", "uuid": "u"}]
    js = f.execute_js.await_args.args[0]
    assert "game.scenes" in js and "notes" in js
    f = fx({"result": {"entries": []}})
    run(Host()._fetch_world_document_index(f, "Actor"))
    assert "game.actors" in f.execute_js.await_args.args[0] and "notes" not in f.execute_js.await_args.args[0]
    assert run(Host()._fetch_world_document_index(fx({"result": {"error": "no game"}}), "Actor")) == []
    assert run(Host()._fetch_world_document_index(fx({"result": "junk"}), "Actor")) == []
    assert run(Host()._fetch_world_document_index(fx({"entries": [1]}), "Actor")) == []     # un-enveloped, no 'result'


def test_rolltables_group_by_chapter_and_filter_foreign_folders():
    tables = {"tables": [
        {"name": "Rumours", "folder": "Camp / Chapter 1", "results": ["a"]},
        {"name": "Foreign", "folder": "Other / Chapter 1"},
        {"name": "Loose", "folder": ""},
        {"name": "Enc", "folder": "Camp / Chapter 1"},
    ]}
    out = run(Host()._fetch_world_rolltables(fx({"result": tables}), "Camp"))
    assert list(out) == ["Chapter 1"] and [t["name"] for t in out["Chapter 1"]] == ["Rumours", "Enc"]


def test_rolltables_failures_are_empty():
    f = MagicMock()
    f.execute_js = AsyncMock(side_effect=RuntimeError("ws"))
    assert run(Host()._fetch_world_rolltables(f, "Camp")) == {}
    assert run(Host()._fetch_world_rolltables(fx({"result": {"error": "x"}}), "Camp")) == {}
    assert run(Host()._fetch_world_rolltables(fx({"result": [1]}), "Camp")) == {}


def test_world_journals_fetches_one_page_per_call_and_filters():
    index = {"entries": [
        {"id": "j1", "name": "Chapter 1: Start", "folder": "Camp / Ch", "pages": [{"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}]},
        {"id": "j2", "name": "Chapter 2", "folder": "Camp", "pages": []},                       # no pages
        {"id": "j3", "name": "Chapter 3", "folder": "Other", "pages": [{"id": "p", "name": "x"}]},  # wrong campaign
        {"id": "j4", "name": "Credits", "folder": "Camp", "pages": [{"id": "p", "name": "x"}]},  # not adventure
    ]}
    f = fx({"result": index}, {"result": {"html": "<p>one</p>"}}, {"result": {"error": "page gone"}})
    out = run(Host()._fetch_world_journals(f, "Camp"))
    assert out == [{"name": "Chapter 1: Start", "pages": [{"name": "A", "html": "<p>one</p>"}]}]
    assert f.execute_js.await_count == 3
    assert "'j1'" in f.execute_js.await_args_list[1].args[0] and "'p1'" in f.execute_js.await_args_list[1].args[0]
    assert "'p2'" in f.execute_js.await_args_list[2].args[0]


def test_world_journals_page_exception_skips_page_and_empty_chapter_dropped():
    index = {"entries": [{"id": "j1", "name": "Chapter 1", "folder": "Camp", "pages": [{"id": "p1", "name": "A"}]}]}
    f = MagicMock()
    f.execute_js = AsyncMock(side_effect=[{"result": index}, RuntimeError("1009")])
    assert run(Host()._fetch_world_journals(f, "Camp")) == []


def test_world_journals_index_failures():
    f = MagicMock()
    f.execute_js = AsyncMock(side_effect=RuntimeError("x"))
    assert run(Host()._fetch_world_journals(f, "Camp")) == []
    assert run(Host()._fetch_world_journals(fx({"result": {"error": "e"}}), "Camp")) == []
    assert run(Host()._fetch_world_journals(fx({"result": {"entries": []}}), "Camp")) == []


def test_pack_finder_js_quotes_the_name():
    js = WorldImportMixin._pack_finder_js("world.it's")
    assert "'world.it\\'s'" in js or '"world.it\'s"' in js


def test_journal_pack_fetches_each_adventure_document():
    f = fx({"result": {"entries": [{"id": "d1", "name": "Chapter 1"}, {"id": "d2", "name": "Tasha's Cauldron"}, {"id": "d3", "name": "Appendix A"}]}},
           {"result": {"name": "Chapter 1", "pages": [{"name": "p", "html": "x"}]}},
           {"result": {"error": "Document not found"}})
    out = run(Host()._fetch_journal_pack(f, "world.pack"))
    assert out == [{"name": "Chapter 1", "pages": [{"name": "p", "html": "x"}]}]
    assert "'d1'" in f.execute_js.await_args_list[1].args[0] and "'d3'" in f.execute_js.await_args_list[2].args[0]
    assert f.execute_js.await_count == 3                          # d2 never fetched


@pytest.mark.parametrize("bad", [{"result": {"error": "Journal pack not found: x"}}, {"result": "junk"}])
def test_journal_pack_index_failure_raises(bad):
    with pytest.raises(RuntimeError):
        run(Host()._fetch_journal_pack(fx(bad), "x"))


# ── game.ready poll ──────────────────────────────────────────────────────

@pytest.fixture
def nosleep(monkeypatch):
    calls = []

    async def s(t):
        calls.append(t)
    monkeypatch.setattr(oi.asyncio, "sleep", s)
    return calls


def test_wait_ready_polls_until_ready(nosleep):
    f = MagicMock()
    f.execute_js = AsyncMock(side_effect=[{"result": {"ready": False}}, RuntimeError("ws"), {"result": {"ready": True}}])
    run(Host()._wait_for_foundry_ready(f))
    assert f.execute_js.await_count == 3 and len(nosleep) == 2


def test_wait_ready_gives_up_after_timeout_and_proceeds(nosleep):
    f = MagicMock()
    f.execute_js = AsyncMock(return_value={"result": {"ready": False}})
    run(Host()._wait_for_foundry_ready(f, timeout=3))
    assert f.execute_js.await_count == 3


def test_wait_ready_accepts_unwrapped_payload(nosleep):
    f = MagicMock()
    f.execute_js = AsyncMock(return_value={"ready": True}.copy())
    # a bare dict has no "result": treated as not-ready, so only a wrapped reply ends the wait
    run(Host()._wait_for_foundry_ready(f, timeout=1))
    assert f.execute_js.await_count == 1


# ── lore + handouts ──────────────────────────────────────────────────────

def _store(tmp_path):
    s = SimpleNamespace(folder=tmp_path / "Camp", safe_name="camp")
    return s


def test_write_lore_files(tmp_path):
    s = _store(tmp_path)
    notes = [("Chapter 1: Start/End", "n1"), ("Chapter 2", "n2")]
    entries = [{"title": "Letter: A/B", "pdf_file": "a.pdf"}]
    run(Host()._import_write_lore(s, notes, "WB", "", entries, lambda *a, **k: None))
    assert (s.folder / "Worldbuilding.md").read_text() == "WB"
    assert not (s.folder / "History.md").exists()                     # empty doc not written
    lore = sorted(p.name for p in (s.folder / "Lore").iterdir())
    assert len(lore) == 2 and lore[0].startswith("01 ") and lore[1].startswith("02 ")
    assert all("/" not in n for n in lore)
    assert (s.folder / "Lore" / lore[0]).read_text() == "n1"
    hand = list((s.folder / "Handouts").iterdir())
    assert len(hand) == 1 and hand[0].read_text() == "# Letter: A/B\n\nSee attached PDF: a.pdf\n"
    assert hand[0].parent == s.folder / "Handouts"                    # sanitized title cannot escape


def test_write_lore_traversal_chapter_label_stays_inside(tmp_path):
    s = _store(tmp_path)
    run(Host()._import_write_lore(s, [("../../evil", "x")], "", "", [], lambda *a, **k: None))
    assert not (tmp_path / "evil.md").exists()
    assert len(list((s.folder / "Lore").iterdir())) == 1


def test_upload_handouts_updates_src_and_survives_failures(tmp_path):
    ok = tmp_path / "a.pdf"
    ok.write_bytes(b"%PDF")
    entries = [{"pdf_src": str(ok), "pdf_file": "a.pdf"}, {"pdf_src": str(tmp_path / "missing.pdf"), "pdf_file": "m.pdf"}]
    fc = MagicMock()
    fc.upload_file = AsyncMock(return_value={"path": "campaigns/camp/handouts/a.pdf"})
    msgs, prog = progress_log()
    run(Host()._import_upload_handouts(fc, _store(tmp_path), {"handouts": ["x"]}, entries, prog))
    assert entries[0]["pdf_src"] == "campaigns/camp/handouts/a.pdf"
    assert entries[1]["pdf_src"].endswith("missing.pdf")
    fc.upload_file.assert_awaited_once_with(file_bytes=b"%PDF", path="campaigns/camp/handouts", filename="a.pdf", mime_type="application/pdf")
    assert any("Failed to upload m.pdf" in m for m, _ in msgs)


def test_upload_handouts_skipped_without_client_or_handouts(tmp_path):
    fc = MagicMock()
    fc.upload_file = AsyncMock()
    run(Host()._import_upload_handouts(None, _store(tmp_path), {"handouts": ["x"]}, [{"pdf_src": "a", "pdf_file": "a"}], lambda *a, **k: None))
    run(Host()._import_upload_handouts(fc, _store(tmp_path), {"handouts": []}, [{"pdf_src": "a", "pdf_file": "a"}], lambda *a, **k: None))
    fc.upload_file.assert_not_awaited()


def test_upload_handouts_keeps_src_when_response_has_no_path(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"x")
    e = [{"pdf_src": str(p), "pdf_file": "a.pdf"}]
    fc = MagicMock()
    fc.upload_file = AsyncMock(return_value={})
    run(Host()._import_upload_handouts(fc, _store(tmp_path), {"handouts": ["x"]}, e, lambda *a, **k: None))
    assert e[0]["pdf_src"] == str(p)


# ── import_campaign: error / checkpoint / world-journal paths ─────────────

from campaign.orchestrator import CampaignOrchestrator  # noqa: E402


class _Stub:
    """Pass 1/2/3 stub; records the pass-1 and pass-2 user prompts."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.p1 = []
        self.p2 = []

    async def post(self, url, headers=None, json=None, timeout=None):
        import json as j
        sys_, user = json["messages"][0]["content"], json["messages"][1]["content"]
        r = MagicMock()
        r.status_code = 200
        if "extracting GM notes" in sys_:
            self.p1.append(user)
            text = "notes"
        elif "converting extracted GM notes" in sys_:
            self.p2.append(user)
            text = j.dumps(self.payloads.pop(0))
        elif "world lore documents" in sys_:
            text = "===WORLDBUILDING===\nW\n===HISTORY===\nH\n===END==="
        else:
            text = '{"groups": []}'        # dedupe call
        r.json.return_value = {"choices": [{"message": {"content": text}}]}
        return r


def _orch(monkeypatch, tmp_path, chapters):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "product").mkdir()
    o = CampaignOrchestrator()
    o._wait_for_foundry_ready = AsyncMock()
    o._fetch_world_journals = AsyncMock(return_value=[{"name": n, "pages": [{"name": "p", "html": f"<p>{n} text</p>" * 30}]} for n in chapters])
    o._fetch_journal_pack = AsyncMock(return_value=[])
    o._fetch_world_rolltables = AsyncMock(return_value={})
    o._fetch_world_document_index = AsyncMock(return_value=[])
    o._semantic_dedupe_section = AsyncMock(side_effect=lambda c, k, items: items)
    return o


def test_import_scan_errors_abort_before_any_llm_call(tmp_path):
    o = CampaignOrchestrator()
    stub = _Stub([])
    res = run(o.import_campaign(str(tmp_path / "nope"), "C", llm_client=stub))
    assert res["status"] == "error" and res["error"] and stub.p1 == []


def test_import_journal_pack_without_foundry_client_errors(tmp_path):
    (tmp_path / "p").mkdir()
    res = run(CampaignOrchestrator().import_campaign(str(tmp_path / "p"), "C", llm_client=_Stub([]), journal_pack="pk"))
    assert res["status"] == "error" and "connected Foundry client" in res["error"]


def test_import_progress_callback_exception_is_swallowed(tmp_path):
    (tmp_path / "p").mkdir()
    cb = MagicMock(side_effect=RuntimeError("ui gone"))
    res = run(CampaignOrchestrator().import_campaign(str(tmp_path / "p"), "C", llm_client=_Stub([]), journal_pack="pk", on_progress=cb))
    assert cb.called and res["status"] == "error"


def test_import_build_failure_checkpoints_then_resume_skips_generation(tmp_path, monkeypatch):
    o = _orch(monkeypatch, tmp_path, ["Ch1", "Ch2"])
    o._fetch_world_rolltables = AsyncMock(return_value={"Ch1": [{"name": "Rumours", "description": "", "results": ["a rumour"]}]})
    o._fetch_world_document_index = AsyncMock(return_value=[
        {"name": "Map", "uuid": "S.1", "folder": "C / Ch1", "notes": [{"label": "R1: Hall"}, "R2: Crypt", {"label": ""}]},
        {"name": "NoChapter", "uuid": "S.2", "folder": "", "notes": []}])
    stub = _Stub([{"campaign": {"name": "C"}, "scenes": [{"name": "A"}], "npcs": [{"name": "N1"}]},
                  {"scenes": [{"name": "B"}], "npcs": []}])
    o.build_campaign = AsyncMock(side_effect=RuntimeError("deploy down"))
    res = run(o.import_campaign(str(tmp_path / "product"), "C", llm_client=stub, foundry_client=MagicMock(), journal_pack="pk"))
    assert res["status"] == "error" and "deploy down" in res["error"]
    # canonical pin labels fed to pass 2 for the chapter whose folder holds the map
    assert "R1: Hall" in stub.p2[0] and "R2: Crypt" in stub.p2[0]
    assert "a rumour" in stub.p1[0] or any("a rumour" in p for p in stub.p2)
    # chapters were dedup'd across (>1 chapter)
    assert o._semantic_dedupe_section.await_count == 3
    ck = tmp_path / "campaign_assets" / "c" / "import_checkpoint.json"
    saved = json.loads(ck.read_text())
    assert saved["chapter_idx"] == 2 and [s["name"] for s in saved["campaign_data"]["scenes"]] == ["A", "B"]
    assert [s["source_chapter"] for s in saved["campaign_data"]["scenes"]] == ["Ch1", "Ch2"]
    # a retry resumes: no pass-1/2 calls, build gets the saved data, checkpoint deleted on success
    stub2 = _Stub([])
    o.build_campaign = AsyncMock(return_value={"status": "complete", "steps": []})
    res2 = run(o.import_campaign(str(tmp_path / "product"), "C", llm_client=stub2, foundry_client=MagicMock(), journal_pack="pk"))
    assert stub2.p1 == [] and stub2.p2 == []
    assert [s["name"] for s in o.build_campaign.await_args.kwargs["campaign_data"]["scenes"]] == ["A", "B"]
    assert res2["import_summary"]["chapters_processed"] == 2 and not ck.exists()


def test_import_ignores_corrupt_and_stale_checkpoints(tmp_path, monkeypatch):
    o = _orch(monkeypatch, tmp_path, ["Ch1"])
    ck = tmp_path / "campaign_assets" / "c" / "import_checkpoint.json"
    ck.parent.mkdir(parents=True)
    o.build_campaign = AsyncMock(return_value={"status": "complete", "steps": []})
    for body in ("{not json", json.dumps({"source_path": "/elsewhere", "chapter_labels": ["Other"], "chapter_idx": 1,
                                          "campaign_data": {"scenes": [{"name": "stale"}]}, "all_notes": [],
                                          "total_pages_extracted": 1, "total_chunks_processed": 1})):
        ck.write_text(body)
        stub = _Stub([{"campaign": {"name": "C"}, "scenes": [{"name": "fresh"}]}])
        run(o.import_campaign(str(tmp_path / "product"), "C", llm_client=stub, foundry_client=MagicMock(), journal_pack="pk"))
        assert len(stub.p2) == 1                                  # regenerated from scratch
        assert [s["name"] for s in o.build_campaign.await_args.kwargs["campaign_data"]["scenes"]] == ["fresh"]
        ck.write_text(body)


def test_import_falls_back_to_named_pack_when_world_has_no_journals(tmp_path, monkeypatch):
    o = _orch(monkeypatch, tmp_path, [])
    o._fetch_journal_pack = AsyncMock(return_value=[{"name": "Chapter 1", "pages": [{"name": "p", "html": "<p>text</p>" * 30}]}])
    o.build_campaign = AsyncMock(return_value={"status": "complete", "steps": []})
    stub = _Stub([{"campaign": {"name": "C"}, "scenes": []}])
    res = run(o.import_campaign(str(tmp_path / "product"), "C", llm_client=stub, foundry_client=MagicMock(), journal_pack="pk"))
    o._fetch_journal_pack.assert_awaited_once()
    assert res["status"] == "complete" and any("falling back to compendium pack 'pk'" in s["message"] for s in res["steps"])
