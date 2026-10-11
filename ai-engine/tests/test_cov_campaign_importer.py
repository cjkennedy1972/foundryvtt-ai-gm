"""Coverage-gap tests for campaign.importer (edge branches and fallbacks)."""

import builtins
import sys
import types

import pytest
from PIL import Image

import campaign.importer as imp
from campaign.importer import (
    _pick_preferred_pdf,
    _classify_directory,
    chunk_pages,
    extract_pdf_text,
    format_rolltables_for_notes,
    match_maps_to_scenes,
    match_names_to_existing,
    match_scenes_to_existing,
    merge_duplicate_group,
    parse_dedup_groups,
    parse_pass3_response,
    prepare_handouts,
    scan_product_folder,
    similarity,
)


def _img(path, size=(200, 100), mode="RGB"):
    Image.new(mode, size, "red").save(path)
    return str(path)


# ─── scan_product_folder ──────────────────────────────────────────────────


def test_scan_missing_and_non_directory(tmp_path):
    missing = scan_product_folder(str(tmp_path / "nope"))
    assert missing["errors"] == [f"Source path does not exist: {tmp_path / 'nope'}"]
    f = tmp_path / "file.txt"
    f.write_text("x")
    notdir = scan_product_folder(str(f))
    assert notdir["errors"] == [f"Source path is not a directory: {f}"]


def test_scan_skips_icloud_dirs_dotfiles_and_foundry_stores(tmp_path):
    maps = tmp_path / "Maps"
    maps.mkdir()
    (maps / "keep.png").write_bytes(b"x")
    (maps / ".hidden.png").write_bytes(b"x")
    icloud = tmp_path / "Maps.icloud"
    icloud.mkdir()
    (icloud / "ghost.png").write_bytes(b"x")
    store = tmp_path / "Tokens" / "db"
    store.mkdir(parents=True)
    (store / "CURRENT").write_bytes(b"")
    (store / "LOCK").write_bytes(b"")
    (store / "a.png").write_bytes(b"x")

    res = scan_product_folder(str(tmp_path))
    assert res["maps"] == [str(maps / "keep.png")]
    assert res["tokens"] == []
    assert res["total_files"] == 1
    assert res["errors"] == []


# ─── extract_pdf_text ─────────────────────────────────────────────────────


class _FakePage:
    def __init__(self, text):
        self._t = text

    def extract_text(self):
        return self._t


def _fake_pypdf(monkeypatch, pages):
    mod = types.SimpleNamespace(PdfReader=lambda f: types.SimpleNamespace(pages=pages))
    monkeypatch.setitem(sys.modules, "pypdf", mod)


def test_extract_pdf_text_drops_short_and_none_pages(monkeypatch, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    _fake_pypdf(monkeypatch, [_FakePage("x" * 60), _FakePage("short"), _FakePage(None)])
    assert extract_pdf_text(str(pdf)) == [(1, "x" * 60)]


def test_extract_pdf_text_stops_at_page_cap(monkeypatch, tmp_path, caplog):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(imp, "MAX_PDF_PAGES", 2)
    _fake_pypdf(monkeypatch, [_FakePage("y" * 60)] * 5)
    with caplog.at_level("WARNING"):
        out = extract_pdf_text(str(pdf))
    assert [p for p, _ in out] == [1, 2]
    assert "stopping at 2 pages" in caplog.text


def test_extract_pdf_text_reraises_missing_pypdf(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "pypdf":
            raise ImportError("no pypdf")
        return real_import(name, *a, **k)

    monkeypatch.delitem(sys.modules, "pypdf", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ImportError):
        extract_pdf_text(str(tmp_path / "a.pdf"))


# ─── chunk_pages oversize handling ────────────────────────────────────────


def test_chunk_pages_splits_oversized_single_page():
    # budget = 10 tokens * 4 = 40 chars. paragraphs: 30, 5 (merge), 100 (hard slice)
    text = "a" * 30 + "\n\n" + "b" * 5 + "\n\n" + "c" * 100
    chunks = chunk_pages([(7, text)], tokens_per_chunk=10)
    bodies = [c.split("=" * 40 + "\n", 1)[1] for c in chunks]
    assert all(len(b) <= 40 + 2 * len("\n\n") * 3 for b in bodies)
    joined = "".join(bodies).replace("\n\n", "")
    assert joined == "a" * 30 + "b" * 5 + "c" * 100
    assert all(c.startswith("Pages 7-7") for c in chunks)
    assert len(chunks) >= 3


def test_split_oversized_page_text_no_parts_returns_text():
    assert imp._split_oversized_page_text("", 10) == [""]


# ─── similarity / names ───────────────────────────────────────────────────


def test_similarity_empty_name_scores_zero():
    assert similarity("", "Tavern") == 0.0
    assert imp._document_similarity("", "Tavern") == 0.0
    assert imp._document_similarity("Map 3.2: Battle", "") == 0.0


def test_similarity_numeric_only_name_falls_back_to_zero():
    # digits are stripped, so nothing is left to compare
    assert similarity("123", "456") == 0.0


def test_match_names_skips_docs_without_uuid_and_claimed():
    existing = [{"name": "Goblin Chief"}, {"name": "Goblin Chief", "uuid": "U1"}]
    res = match_names_to_existing(["Goblin Chief", "Goblin Chief"], existing)
    assert res["matched"] == {"Goblin Chief": "U1"}
    assert res["unmatched"] == ["Goblin Chief"]


# ─── match_maps_to_scenes ─────────────────────────────────────────────────


def test_maps_tie_prefers_72dpi_over_300dpi(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    big = _img(src / "Tavern_300DPI.png", (1000, 500))
    small = _img(src / "Tavern_72DPI.png", (240, 120))
    out = tmp_path / "out"
    out.mkdir()
    for order in ([big, small], [small, big]):
        res = match_maps_to_scenes(["Tavern"], order, out)
        assert res["matched_scenes"]["Tavern"]["map_file"].endswith("map_tavern.jpg")
        # 72DPI (240x120) copied, never downscaled -> dims preserved
        assert res["matched_scenes"]["Tavern"]["width_px"] == 240


def test_maps_tie_prefers_gridless(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    a = _img(src / "Grid_Crypt.png", (64, 64))
    b = _img(src / "Gridless_Crypt.png", (128, 128))
    out = tmp_path / "out"
    out.mkdir()
    res = match_maps_to_scenes(["Crypt"], [a, b], out)
    assert res["matched_scenes"]["Crypt"]["width_px"] == 128


def test_maps_300dpi_downscaled_and_rgba_flattened(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = _img(src / "Keep_300DPI.png", (1000, 500), mode="RGBA")
    out = tmp_path / "out"
    out.mkdir()
    res = match_maps_to_scenes(["Keep"], [f], out)
    m = res["matched_scenes"]["Keep"]
    assert (m["width_px"], m["height_px"]) == (240, 120)
    assert "converted 'Keep_300DPI.png' to 240×120" in res["warnings"][0]
    with Image.open(m["map_file"]) as im:
        assert im.mode == "RGB"


def test_maps_oversize_non300_is_scaled_to_fit(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    f = _img(src / "Vault.png", (400, 400))
    monkeypatch.setattr(imp, "MAX_MAP_UPLOAD_BYTES", 1000)
    out = tmp_path / "out"
    out.mkdir()
    res = match_maps_to_scenes(["Vault"], [f], out)
    m = res["matched_scenes"]["Vault"]
    assert m["width_px"] < 400 and m["height_px"] < 400
    assert res["warnings"][0].startswith("Map 'Vault': converted 'Vault.png'")


def test_maps_unreadable_300dpi_copies_original_with_warning(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "Lair_300DPI.png"
    f.write_bytes(b"not an image")
    out = tmp_path / "out"
    out.mkdir()
    res = match_maps_to_scenes(["Lair"], [str(f)], out)
    assert (out / "map_lair.jpg").read_bytes() == b"not an image"
    assert any("resize failed" in w and "copied original (12 bytes)" in w for w in res["warnings"])
    m = res["matched_scenes"]["Lair"]
    assert (m["grid_width"], m["grid_height"]) == (16, 12)
    assert (m["width_px"], m["height_px"]) == (1024, 768)
    assert any("could not read image size" in w for w in res["warnings"])


# ─── match_tokens_to_npcs ─────────────────────────────────────────────────


def test_tokens_tie_prefers_closeup_and_png(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    a = _img(src / "Bob.jpg", (10, 10))
    b = _img(src / "Closeup_Bob.jpg", (20, 20))
    out = tmp_path / "out"
    out.mkdir()
    res = imp.match_tokens_to_npcs(["Bob"], [a, b], out)
    assert res["matched_npcs"]["Bob"]["source_file"] == b
    c = _img(src / "Ann.jpg", (10, 10))
    d = _img(src / "Ann.png", (30, 30))
    res = imp.match_tokens_to_npcs(["Ann"], [c, d], out)
    assert res["matched_npcs"]["Ann"]["source_file"] == d


def test_tokens_rgba_saved_and_unreadable_copied(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    good = _img(src / "Zed.png", (10, 10), mode="RGBA")
    bad = src / "Yan.png"
    bad.write_bytes(b"junk")
    out = tmp_path / "out"
    out.mkdir()
    res = imp.match_tokens_to_npcs(["Zed", "Yan", "Nobody"], [good, str(bad)], out)
    assert set(res["matched_npcs"]) == {"Zed", "Yan"}
    assert res["unmatched_npcs"] == ["Nobody"]
    assert (out / "token_yan.png").read_bytes() == b"junk"
    assert any(w.startswith("Token 'Yan': conversion failed") for w in res["warnings"])
    with Image.open(out / "token_zed.png") as im:
        assert im.mode == "RGBA"


# ─── rolltables ───────────────────────────────────────────────────────────


def test_rolltables_empty_text_results_skipped_and_description_kept():
    out = format_rolltables_for_notes([
        {"name": None, "description": "<p>Roll d6</p>", "results": ["<a>Wolf</a>", "<p></p>"]},
    ])
    assert "### Unnamed Table" in out
    assert "Roll d6" in out
    assert "- Wolf" in out
    assert out.count("- ") == 1


# ─── match_scenes_to_existing ─────────────────────────────────────────────


def test_scenes_map_reference_claims_candidate_once():
    items = [{"name": "Battle (Map 7.5)"}, {"name": "Rematch (Map 7.5)"}]
    cands = [{"name": "Map 7.5: Clash", "uuid": "S1"}]
    res = match_scenes_to_existing(items, cands)
    matched = res["matched"] if "matched" in res else res
    assert list(matched.values()) == ["S1"]
    assert len(matched) == 1


# ─── dedup parsing / merging ──────────────────────────────────────────────


def test_parse_dedup_groups_ignores_junk_and_appends_missing():
    names = ["A", "B", "C", "D"]
    text = '{"groups": [["A", "B"], "oops", [], ["Zed"], ["B", "C"]]}'
    assert parse_dedup_groups(text, names) == [["A", "B"], ["C"], ["D"]]


def test_parse_dedup_groups_non_dict_and_empty_fall_back():
    names = ["A", "B"]
    assert parse_dedup_groups("[1]", names) == [["A"], ["B"]]
    assert parse_dedup_groups('{"groups": []}', names) == [["A"], ["B"]]
    assert parse_dedup_groups('{"groups": "x"}', names) == [["A"], ["B"]]


def test_merge_duplicate_group_unions_lists_and_tracks_chapters():
    items = {
        "Bakaris": {"name": "Bakaris", "source_chapter": "Ch1", "tags": ["a"], "role": "", "mix": "s"},
        "Lord Bakaris": {"name": "Lord Bakaris", "source_chapter": "Ch2", "tags": ["a", "b"],
                         "role": "villain", "mix": ["x"]},
    }
    merged = merge_duplicate_group(items, ["Bakaris", "Lord Bakaris"])
    assert merged["name"] == "Lord Bakaris"
    assert merged["tags"] == ["a", "b"]
    assert merged["role"] == "villain"
    assert merged["mix"] == "s"  # scalar already set, list value does not clobber it
    assert merged["source_chapters"] == ["Ch1", "Ch2"]
    assert merge_duplicate_group(items, ["nobody"]) == {}
    assert merge_duplicate_group(items, ["Bakaris"]) is items["Bakaris"]


# ─── handouts / pdf pick / classify ───────────────────────────────────────


def test_prepare_handouts_prefers_full_color_then_printer():
    pdfs = ["/h/Notes - Printer Friendly.pdf", "/h/Notes - Full Color.pdf", "/h/Notes - Plain.pdf"]
    out = prepare_handouts(pdfs, {})
    assert len(out) == 1 and out[0]["pdf_file"].endswith("Full Color.pdf")
    out = prepare_handouts(["/h/Notes - Plain.pdf", "/h/Notes - Printer Friendly.pdf"], {})
    assert out[0]["pdf_file"].endswith("Printer Friendly.pdf")
    out = prepare_handouts(["/h/Notes - Printer Friendly.pdf", "/h/Notes - Plain.pdf"], {})
    assert out[0]["pdf_file"].endswith("Printer Friendly.pdf")


def test_pick_preferred_pdf_orders_printer_friendly_first():
    assert _pick_preferred_pdf(["a.pdf", "b_Printer_Friendly.pdf"]) == ["b_Printer_Friendly.pdf", "a.pdf"]
    assert _pick_preferred_pdf(["a.pdf", "b.pdf"]) == ["a.pdf", "b.pdf"]


def test_classify_directory_adventure_and_generic():
    assert _classify_directory("my adventure") == "adventure"
    assert _classify_directory("stuff") == "generic"


# ─── pass 3 ───────────────────────────────────────────────────────────────


def test_parse_pass3_heading_fallback_both_orders():
    wb_first = "# Worldbuilding\nlore\n# History\npast"
    assert parse_pass3_response(wb_first) == ("# Worldbuilding\nlore", "# History\npast")
    hist_first = "# History\npast\n# Worldbuilding\nlore"
    assert parse_pass3_response(hist_first) == ("# Worldbuilding\nlore", "# History\npast")
