"""Tests for the campaign importer module.

All tests use synthetic files (in-memory text, Pillow-generated images,
pypdf-generated PDFs) so no real product folder is needed.

Style follows test_vault_search.py: plain pytest, independent assertions,
no heavy fixtures.
"""

import os
import pytest
import tempfile
from pathlib import Path

from campaign.importer import (
    normalize_name,
    similarity,
    scan_product_folder,
    chunk_pages,
    match_maps_to_scenes,
    match_tokens_to_npcs,
    prepare_handouts,
    build_import_summary,
    build_pass1_prompt,
    build_pass1_user,
    build_pass2_user,
    build_pass3_user,
    parse_pass3_response,
    journal_entries_to_pages,
    is_adventure_journal_entry,
    match_names_to_existing,
    filter_candidates_by_campaign_folder,
    match_scenes_to_existing,
    extract_map_reference,
    format_rolltables_for_notes,
    is_adventure_content_entry,
    folder_matches_campaign,
    build_semantic_match_prompt,
    parse_semantic_match_response,
    build_dedup_prompt,
    parse_dedup_groups,
    merge_duplicate_group,
    MAX_MAP_UPLOAD_BYTES,
    DPI_PREFIXES,
    VARIANT_PREFIXES,
)


# ─── NORMALIZATION ────────────────────────────────────────────────────────


def test_normalize_strips_extension():
    assert normalize_name("Tavern.jpg") == "tavern"


def test_normalize_strips_dpi_prefix():
    assert normalize_name("300DPI_Tavern_Map.jpg") == "tavern map"


def test_normalize_strips_variant_prefix():
    assert normalize_name("Gridless_Tavern.jpg") == "tavern"


def test_normalize_strips_product_suffix():
    assert normalize_name("Tavern - Dragonlance Campaign") == "tavern"


def test_normalize_multiple_prefixes():
    assert (
        normalize_name("72DPI_Gridless_Tavern_Map - Dragon Quest")
        == "tavern map"
    )


def test_normalize_numbers_removed():
    assert normalize_name("Map_12_Tavern") == "map tavern"


def test_similarity_exact_match():
    assert similarity("Tavern", "Tavern.jpg") == 1.0


def test_similarity_partial_match():
    score = similarity(
        "The Gilded Tavern", "72DPI_The_Gilded_Tavern_Gridless.jpg"
    )
    assert score > 0.8


def test_similarity_no_match():
    score = similarity("Forest", "Dungeon")
    assert score < 0.3


def test_similarity_empty():
    assert similarity("", "something") == 0.0


# ─── FOLDER SCAN ──────────────────────────────────────────────────────────


def test_scan_product_folder_classifies_maps():
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "Maps"
        maps_dir.mkdir()
        (maps_dir / "tavern.jpg").write_text("fake image")

        result = scan_product_folder(tmp)
        assert result["maps"] == [str(maps_dir / "tavern.jpg")]
        assert result["total_files"] == 1
        assert not result["errors"]


def test_scan_product_folder_classifies_tokens():
    with tempfile.TemporaryDirectory() as tmp:
        token_dir = Path(tmp) / "Tokens"
        token_dir.mkdir()
        (token_dir / "npc_hero.png").write_text("fake token")

        result = scan_product_folder(tmp)
        assert result["tokens"] == [str(token_dir / "npc_hero.png")]


def test_scan_product_folder_classifies_handouts():
    with tempfile.TemporaryDirectory() as tmp:
        handout_dir = Path(tmp) / "Handouts"
        handout_dir.mkdir()
        (handout_dir / "reference.pdf").write_text("fake pdf")

        result = scan_product_folder(tmp)
        assert result["handouts"] == [str(handout_dir / "reference.pdf")]


def test_scan_product_folder_prefers_printer_friendly_pdf():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "adventure.pdf").write_text("abc")
        (Path(tmp) / "Printer_Friendly.pdf").write_text("def")

        result = scan_product_folder(tmp)
        # Both are at top-level so they classify as adventure PDFs
        assert len(result["adventure_pdfs"]) == 2
        # Printer Friendly should come first
        assert "Printer_Friendly" in result["adventure_pdfs"][0]


def test_scan_product_folder_detects_icloud_placeholder():
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "Maps"
        maps_dir.mkdir()
        (maps_dir / "tavern.jpg").write_text("")  # 0-byte placeholder

        result = scan_product_folder(tmp)
        assert result["total_files"] == 0
        assert len(result["errors"]) == 1
        assert "0-byte iCloud placeholder" in result["errors"][0]
        assert "brctl download" in result["errors"][0]


def test_scan_product_folder_nonexistent_path():
    result = scan_product_folder("/nonexistent/path/12345")
    assert result["errors"]
    assert "does not exist" in result["errors"][0]


def test_scan_product_folder_skips_leveldb_store():
    """A FoundryVTT compendium's LOCK/*.log files are normally 0 bytes and
    must not be flagged as iCloud placeholders."""
    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "packs" / "ddb-krynn-ddb-journals"
        pack_dir.mkdir(parents=True)
        (pack_dir / "CURRENT").write_text("MANIFEST-000001\n")
        (pack_dir / "LOCK").write_text("")
        (pack_dir / "000003.log").write_text("")

        result = scan_product_folder(tmp)
        assert result["errors"] == []


# ─── JOURNAL PACK TEXT EXTRACTION ─────────────────────────────────────────


def test_journal_entries_to_pages_flattens_and_strips_html():
    entries = [
        {
            "name": "Chapter 1",
            "pages": [
                {"name": "Intro", "html": "<p>The dragon queen stirs in the dark.</p><p>Krynn trembles.</p>"},
                {"name": "Empty", "html": "<p>x</p>"},  # below min_chars_per_page
            ],
        },
    ]
    pages = journal_entries_to_pages(entries, min_chars_per_page=10)
    assert len(pages) == 1
    page_num, text = pages[0]
    assert page_num == 1
    assert "dragon queen stirs" in text
    assert "Krynn trembles" in text
    assert "<p>" not in text


def test_journal_entries_to_pages_empty():
    assert journal_entries_to_pages([]) == []


def test_is_adventure_journal_entry_matches_chapters_and_appendices():
    assert is_adventure_journal_entry("Chapter 3: When Home Burns")
    assert is_adventure_journal_entry("Appendix A: Gear and Magic Items")
    assert is_adventure_journal_entry("chapter 1: character creation")


def test_is_adventure_journal_entry_rejects_unrelated_sourcebooks():
    assert not is_adventure_journal_entry("Player's Handbook")
    assert not is_adventure_journal_entry("Xanathar's Guide to Everything")
    assert not is_adventure_journal_entry("Credits")
    assert not is_adventure_journal_entry("Table of Contents")
    assert not is_adventure_journal_entry("")
    assert not is_adventure_journal_entry(None)


# ─── EXISTING-DOCUMENT MATCHING ────────────────────────────────────────────


def test_match_names_to_existing_matches_exact_and_close_names():
    existing = [
        {"name": "Becklin Uth Viharin", "uuid": "Actor.aaa"},
        {"name": "Lord Bakaris Uth Estide", "uuid": "Actor.bbb"},
    ]
    result = match_names_to_existing(["Becklin Uth Viharin", "Someone Else"], existing)
    assert result["matched"] == {"Becklin Uth Viharin": "Actor.aaa"}
    assert result["unmatched"] == ["Someone Else"]


def test_match_names_to_existing_does_not_double_claim_a_document():
    existing = [{"name": "Vogler", "uuid": "Scene.xyz"}]
    result = match_names_to_existing(["Vogler Prime", "Vogler Secondary"], existing)
    assert len(result["matched"]) <= 1
    assert len(result["unmatched"]) >= 1


def test_match_names_to_existing_empty_inputs():
    assert match_names_to_existing([], []) == {"matched": {}, "unmatched": []}
    assert match_names_to_existing(["Solo"], []) == {"matched": {}, "unmatched": ["Solo"]}


# ─── FOLDER SCOPING ─────────────────────────────────────────────────────


def test_filter_candidates_by_campaign_folder_scopes_to_matching_folder():
    candidates = [
        {"name": "Map 3.1: Vogler", "uuid": "a", "folder": "Dragonlance: Shadow of the Dragon Queen"},
        {"name": "Some Scene", "uuid": "b", "folder": "Icewind Dale: Rime of the Frostmaiden"},
    ]
    result = filter_candidates_by_campaign_folder(candidates, "Dragonlance: Shadow of the Dragon Queen")
    assert result == [candidates[0]]


def test_filter_candidates_by_campaign_folder_falls_back_when_nothing_matches():
    candidates = [{"name": "X", "uuid": "a", "folder": "Unrelated Book"}]
    result = filter_candidates_by_campaign_folder(candidates, "Dragonlance: Shadow of the Dragon Queen")
    assert result == candidates


# ─── SEMANTIC MATCH PROMPT/RESPONSE ─────────────────────────────────────


def test_build_semantic_match_prompt_includes_names_and_context():
    items = [{"name": "Vogler — The Brass Crab", "description": "A tavern in Vogler", "atmosphere": "cozy"}]
    candidates = [{"name": "Map 3.1: Vogler", "folder": "Chapter 3: When Home Burns"}]
    system, user = build_semantic_match_prompt("scene", items, candidates)
    assert "Vogler — The Brass Crab" in user
    assert "A tavern in Vogler" in user
    assert "Map 3.1: Vogler" in user
    assert "Chapter 3: When Home Burns" in user
    assert "JSON" in system


def test_parse_semantic_match_response_plain_json():
    text = '{"Vogler — The Brass Crab": "Map 3.1: Vogler", "Someone": null}'
    assert parse_semantic_match_response(text) == {
        "Vogler — The Brass Crab": "Map 3.1: Vogler",
        "Someone": None,
    }


def test_parse_semantic_match_response_strips_code_fences():
    text = '```json\n{"A": "B"}\n```'
    assert parse_semantic_match_response(text) == {"A": "B"}


def test_parse_semantic_match_response_malformed_returns_empty():
    assert parse_semantic_match_response("not json at all") == {}
    assert parse_semantic_match_response("") == {}


def test_scan_handout_dir_wins_over_printer_friendly_name():
    """A 'Printer Friendly' PDF inside Handouts/ is a handout, not an adventure.

    Real product folders ship player handouts named '... - Printer Friendly.pdf';
    directory classification must beat the name-keyword adventure heuristic.
    """
    with tempfile.TemporaryDirectory() as tmp:
        handout_dir = Path(tmp) / "Handouts"
        handout_dir.mkdir()
        (handout_dir / "Player Handouts - Printer Friendly.pdf").write_text("x")
        (Path(tmp) / "adventure.pdf").write_text("y")

        result = scan_product_folder(tmp)
        assert len(result["handouts"]) == 1
        assert "Printer Friendly" in result["handouts"][0]
        assert len(result["adventure_pdfs"]) == 1
        assert "adventure.pdf" in result["adventure_pdfs"][0]


# ─── PDF EXTRACTION (where pypdf is available) ────────────────────────────


try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
def test_extract_pdf_text_basic():
    from campaign.importer import extract_pdf_text

    # Create a minimal PDF with pypdf
    import io
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()
    # Add a blank page and overlay text annotation
    from pypdf.generic import RectangleObject
    page = writer.add_blank_page(width=612, height=792)

    # Write to bytes
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(buf.read())
        f.flush()
        # We can't easily add extractable text with pypdf alone, so just
        # verify it runs without crashing on a blank PDF
        pages = extract_pdf_text(f.name, min_chars_per_page=0)
        assert isinstance(pages, list)
        os.unlink(f.name)


# ─── CHUNKING ─────────────────────────────────────────────────────────────


def test_chunk_pages_splits_on_boundaries():
    pages = [
        (1, "a" * 20000),   # ~5000 tokens (20k chars / 4)
        (2, "b" * 20000),
        (3, "c" * 20000),
    ]
    chunks = chunk_pages(pages, tokens_per_chunk=12000)
    # 12000 tokens = 48000 chars max per chunk
    # Each page is 20000 chars, so 2 pages per chunk
    assert len(chunks) == 2
    assert "Pages 1-2" in chunks[0]
    assert "Pages 3-3" in chunks[1]


def test_chunk_pages_small_content():
    pages = [
        (1, "hello world"),
        (2, "foo bar"),
    ]
    chunks = chunk_pages(pages, tokens_per_chunk=12000)
    assert len(chunks) == 1
    assert "Pages 1-2" in chunks[0]
    assert "hello world" in chunks[0]


def test_chunk_pages_empty():
    assert chunk_pages([]) == []


# ─── MAP MATCHING ─────────────────────────────────────────────────────────


def test_match_maps_to_scenes_basic():
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "maps"
        maps_dir.mkdir()

        # Create a tiny synthetic image for the map file
        try:
            from PIL import Image
            img = Image.new("RGB", (640, 480))
            map_file = Path(tmp) / "gilded_tavern.jpg"
            img.save(str(map_file), "JPEG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_maps_to_scenes(
            scene_names=["The Gilded Tavern"],
            map_files=[str(map_file)],
            maps_dir=maps_dir,
        )

        assert "The Gilded Tavern" in result["matched_scenes"]
        match = result["matched_scenes"]["The Gilded Tavern"]
        assert match["map_needed"] is False
        assert match["map_file"] == str(maps_dir / "map_the gilded tavern.jpg")
        assert match["grid_width"] == 10  # 640 // 64
        assert match["grid_height"] == 7  # 480 // 64
        assert match["walls"] == []
        assert match["lights"] == []
        assert match["sounds"] == []
        assert not result["unmatched_scenes"]


def test_match_maps_to_scenes_threshold_filters():
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "maps"
        maps_dir.mkdir()

        try:
            from PIL import Image
            img = Image.new("RGB", (100, 100))
            map_file = Path(tmp) / "forest.jpg"
            img.save(str(map_file), "JPEG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_maps_to_scenes(
            scene_names=["Deep Dungeon"],  # Does not match "forest"
            map_files=[str(map_file)],
            maps_dir=maps_dir,
            threshold=0.6,
        )

        assert "Deep Dungeon" in result["unmatched_scenes"]
        assert not result["matched_scenes"]


def test_match_maps_prefers_72dpi():
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "maps"
        maps_dir.mkdir()

        try:
            from PIL import Image
            img72 = Image.new("RGB", (128, 128))
            img300 = Image.new("RGB", (512, 512))
            map72 = Path(tmp) / "72DPI_tavern.jpg"
            map300 = Path(tmp) / "300DPI_tavern.jpg"
            img72.save(str(map72), "JPEG")
            img300.save(str(map300), "JPEG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_maps_to_scenes(
            scene_names=["Tavern"],
            map_files=[str(map300), str(map72)],
            maps_dir=maps_dir,
        )

        match = result["matched_scenes"]["Tavern"]
        # Should prefer 72DPI
        assert "72dpi" in match["source_file"].lower()


def test_match_maps_downscales_300dpi():
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "maps"
        maps_dir.mkdir()

        try:
            from PIL import Image
            img = Image.new("RGB", (1024, 1024))
            map_file = Path(tmp) / "300DPI_tavern.jpg"
            img.save(str(map_file), "JPEG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_maps_to_scenes(
            scene_names=["Tavern"],
            map_files=[str(map_file)],
            maps_dir=maps_dir,
        )

        match = result["matched_scenes"]["Tavern"]
        # 300DPI should be downscaled to 24%
        from PIL import Image
        saved = Image.open(match["map_file"])
        # 1024 * 0.24 = 245.76 -> ~246 or 247
        assert saved.width <= 250
        assert saved.height <= 250
        assert saved.width >= 240
        assert saved.height >= 240


# ─── TOKEN MATCHING ───────────────────────────────────────────────────────


def test_match_tokens_to_npcs_basic():
    with tempfile.TemporaryDirectory() as tmp:
        tokens_dir = Path(tmp) / "tokens"
        tokens_dir.mkdir()

        try:
            from PIL import Image
            img = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
            token_file = Path(tmp) / "sir_valor_portrait.png"
            img.save(str(token_file), "PNG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_tokens_to_npcs(
            npc_names=["Sir Valor"],
            token_files=[str(token_file)],
            tokens_dir=tokens_dir,
        )

        assert "Sir Valor" in result["matched_npcs"]
        match = result["matched_npcs"]["Sir Valor"]
        assert match["portrait_needed"] is False
        assert match["portrait_file"] == str(tokens_dir / "token_sir valor.png")
        assert not result["unmatched_npcs"]


def test_match_tokens_prefers_closeup():
    with tempfile.TemporaryDirectory() as tmp:
        tokens_dir = Path(tmp) / "tokens"
        tokens_dir.mkdir()

        try:
            from PIL import Image
            img1 = Image.new("RGBA", (200, 200))
            img2 = Image.new("RGBA", (200, 200))
            normal = Path(tmp) / "hero.png"
            closeup = Path(tmp) / "hero_CLOSEUP.png"
            img1.save(str(normal), "PNG")
            img2.save(str(closeup), "PNG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_tokens_to_npcs(
            npc_names=["Hero"],
            token_files=[str(normal), str(closeup)],
            tokens_dir=tokens_dir,
        )

        match = result["matched_npcs"]["Hero"]
        assert "closeup" in match["source_file"].lower()


def test_match_tokens_threshold_filters():
    with tempfile.TemporaryDirectory() as tmp:
        tokens_dir = Path(tmp) / "tokens"
        tokens_dir.mkdir()

        try:
            from PIL import Image
            img = Image.new("RGBA", (100, 100))
            token_file = Path(tmp) / "dragon.png"
            img.save(str(token_file), "PNG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_tokens_to_npcs(
            npc_names=["The Ancient Wizard of the North"],
            token_files=[str(token_file)],
            tokens_dir=tokens_dir,
            threshold=0.75,
        )

        # "dragon" vs "The Ancient Wizard" should not meet 0.75 threshold
        assert "The Ancient Wizard of the North" in result["unmatched_npcs"]


# ─── HANDOUTS ─────────────────────────────────────────────────────────────


def test_prepare_handouts():
    handouts = prepare_handouts(
        ["/path/to/Lore - Dragonlance.pdf", "/path/to/Map_Reference.pdf"],
        campaign_data={},
    )
    assert len(handouts) == 2
    assert handouts[0]["title"] == "Lore"
    assert handouts[0]["pdf_file"] == "/path/to/Lore - Dragonlance.pdf"
    assert handouts[0]["pdf_src"] == "/path/to/Lore - Dragonlance.pdf"
    assert handouts[1]["title"] == "Map_Reference"


def test_prepare_handouts_dedupes_color_variants():
    """Full Color vs Printer Friendly of the same handout → one entry.

    Observed on the real Dragonlance folder: both variants normalized to the
    same title, producing duplicate journal entries and colliding Handouts/*.md
    filenames. Full Color wins for on-screen display.
    """
    handouts = prepare_handouts(
        [
            "/h/Campfire Player Handouts - Full Color.pdf",
            "/h/Campfire Player Handouts - Printer Friendly.pdf",
            "/h/Sculpted Effigy Stat Blocks v1 Full Color.pdf",
        ],
        campaign_data={},
    )
    assert len(handouts) == 2
    campfire = [h for h in handouts if h["title"] == "Campfire Player Handouts"]
    assert len(campfire) == 1
    assert "Full Color" in campfire[0]["pdf_file"]


def test_prepare_handouts_printer_friendly_when_no_color_variant():
    handouts = prepare_handouts(
        ["/h/Letters - Printer Friendly.pdf"],
        campaign_data={},
    )
    assert len(handouts) == 1
    assert "Printer Friendly" in handouts[0]["pdf_file"]


# ─── IMPORT SUMMARY ───────────────────────────────────────────────────────


def test_build_import_summary():
    scan_result = {
        "source_path": "/data/campaign",
        "total_files": 10,
        "maps": ["a.jpg", "b.jpg"],
        "tokens": ["c.png"],
        "handouts": ["d.pdf"],
    }
    map_match = {
        "matched_scenes": {"Tavern": {"score": 0.9}},
        "unmatched_scenes": ["Forest"],
        "warnings": ["downscaled map"],
    }
    token_match = {
        "matched_npcs": {"Hero": {"score": 0.8}},
        "unmatched_npcs": ["Villain"],
        "warnings": [],
    }
    handout_entries = [{"title": "Lore"}]

    summary = build_import_summary(scan_result, map_match, token_match, handout_entries)

    assert summary["source_path"] == "/data/campaign"
    assert summary["total_files_scanned"] == 10
    assert summary["maps"]["found"] == 2
    assert summary["maps"]["matched"] == 1
    assert summary["maps"]["unmatched"] == 1
    assert summary["maps"]["matched_scenes"] == ["Tavern"]
    assert summary["tokens"]["found"] == 1
    assert summary["tokens"]["matched"] == 1
    assert summary["handouts"]["prepared"] == 1
    assert "downscaled map" in summary["warnings"]


# ─── CONSTANTS ────────────────────────────────────────────────────────────


def test_dpi_prefixes_populated():
    assert "300" in DPI_PREFIXES
    assert "72" in DPI_PREFIXES


def test_variant_prefixes_populated():
    assert "gridless" in VARIANT_PREFIXES
    assert "closeup" in VARIANT_PREFIXES


def test_max_map_upload_size():
    assert MAX_MAP_UPLOAD_BYTES == 40 * 1024 * 1024


# ─── LLM PASS PROMPTS + PASS-3 PARSING ────────────────────────────────────


def test_pass1_prompt_lists_fixed_headings():
    prompt = build_pass1_prompt("chunk text")
    for heading in (
        "World/History", "Factions", "NPCs", "Locations",
        "Scenes", "Encounters", "Plot Beats", "Handouts",
    ):
        assert f"## {heading}" in prompt
    assert "EXTRACT ONLY" in prompt  # extract-only, no invention


def test_pass1_user_wraps_chunk():
    user = build_pass1_user("PAGE CONTENT HERE")
    assert "PAGE CONTENT HERE" in user


def test_pass2_user_includes_notes_name_and_levels():
    user = build_pass2_user("COMBINED NOTES", "Dragonlance", "1-5")
    assert "COMBINED NOTES" in user
    assert "Dragonlance" in user
    assert "1-5" in user


def test_pass3_user_wraps_notes():
    user = build_pass3_user("NOTES BODY")
    assert "NOTES BODY" in user


def test_parse_pass3_response_markers():
    text = "===WORLDBUILDING===\nWorld facts.\n===HISTORY===\nPast events.\n===END==="
    wb, hist = parse_pass3_response(text)
    assert wb == "World facts."
    assert hist == "Past events."


def test_parse_pass3_response_heading_fallback():
    text = "# Worldbuilding\nWB body\n# History\nHistory body"
    wb, hist = parse_pass3_response(text)
    assert "WB body" in wb
    assert "History body" in hist


def test_parse_pass3_response_plain_text_fallback():
    wb, hist = parse_pass3_response("unstructured lore dump")
    assert wb == "unstructured lore dump"
    assert hist == ""


def test_parse_pass3_response_empty():
    assert parse_pass3_response("") == ("", "")


def test_match_maps_reports_pixel_dimensions():
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "maps"
        maps_dir.mkdir()
        try:
            from PIL import Image
            img = Image.new("RGB", (640, 480))
            map_file = Path(tmp) / "tavern.jpg"
            img.save(str(map_file), "JPEG")
        except ImportError:
            pytest.skip("Pillow not installed")

        result = match_maps_to_scenes(
            scene_names=["Tavern"],
            map_files=[str(map_file)],
            maps_dir=maps_dir,
        )
        match = result["matched_scenes"]["Tavern"]
        assert match["width_px"] == 640
        assert match["height_px"] == 480


def test_match_maps_matches_via_location_alias():
    """A scene whose own name doesn't match a map still inherits a regional
    map named after its containing location."""
    with tempfile.TemporaryDirectory() as tmp:
        maps_dir = Path(tmp) / "maps"
        maps_dir.mkdir()
        try:
            from PIL import Image
            img = Image.new("RGB", (640, 480))
            map_file = Path(tmp) / "Abanasinia.jpg"  # region map, not a scene
            img.save(str(map_file), "JPEG")
        except ImportError:
            pytest.skip("Pillow not installed")

        # Scene name shares nothing with the map; the location does.
        result = match_maps_to_scenes(
            scene_names=["The Astorio Family Parlor"],
            map_files=[str(map_file)],
            maps_dir=maps_dir,
            scene_aliases={"The Astorio Family Parlor": ["Abanasinia"]},
        )

        assert "The Astorio Family Parlor" in result["matched_scenes"]
        assert not result["unmatched_scenes"]

        # Without the alias, the same scene stays unmatched (own name only).
        no_alias = match_maps_to_scenes(
            scene_names=["The Astorio Family Parlor"],
            map_files=[str(map_file)],
            maps_dir=maps_dir,
        )
        assert no_alias["unmatched_scenes"] == ["The Astorio Family Parlor"]


# ─── CROSS-CHAPTER DEDUPLICATION ───────────────────────────────────────────


def test_build_dedup_prompt_lists_all_names():
    items = [{"name": "Red Dragon Army"}, {"name": "Dragon Army"}, {"name": "Knights of Solamnia"}]
    system, user = build_dedup_prompt("faction", items)
    assert "Red Dragon Army" in user
    assert "Dragon Army" in user
    assert "Knights of Solamnia" in user
    assert "JSON" in system


def test_parse_dedup_groups_valid_response():
    names = ["Red Dragon Army", "Dragon Army", "Knights of Solamnia"]
    text = '{"groups": [["Red Dragon Army", "Dragon Army"], ["Knights of Solamnia"]]}'
    groups = parse_dedup_groups(text, names)
    assert sorted(groups, key=len) == [["Knights of Solamnia"], ["Red Dragon Army", "Dragon Army"]]


def test_parse_dedup_groups_malformed_is_a_safe_no_op():
    names = ["A", "B", "C"]
    assert parse_dedup_groups("not json", names) == [["A"], ["B"], ["C"]]
    assert parse_dedup_groups("", names) == [["A"], ["B"], ["C"]]


def test_parse_dedup_groups_ignores_hallucinated_names_and_covers_dropped_ones():
    names = ["A", "B", "C"]
    # "Z" doesn't exist (ignored); "C" is never mentioned (must still get its own group)
    text = '{"groups": [["A", "Z"], ["B"]]}'
    groups = parse_dedup_groups(text, names)
    assert ["A"] in groups or ["A", "Z"] not in groups  # Z never survives into a group
    all_named = {n for g in groups for n in g}
    assert all_named == {"A", "B", "C"}  # nothing lost
    # each original name appears exactly once across all groups
    flat = [n for g in groups for n in g]
    assert sorted(flat) == ["A", "B", "C"]


def test_merge_duplicate_group_picks_longest_name_and_unions_lists():
    items_by_name = {
        "Red Dragon Army": {
            "name": "Red Dragon Army", "goal": "Conquer Ansalon",
            "notable_members": ["Fewmaster Gholcag"], "source_chapter": "Chapter 3",
        },
        "Dragon Army": {
            "name": "Dragon Army", "notable_members": ["Kansaldi Fire-Eyes"],
            "source_chapter": "Chapter 5",
        },
        "The Dragon Armies": {
            "name": "The Dragon Armies", "source_chapter": "Chapter 7",
        },
    }
    merged = merge_duplicate_group(items_by_name, list(items_by_name.keys()))
    assert merged["name"] == "The Dragon Armies"  # longest
    assert merged["goal"] == "Conquer Ansalon"  # first non-empty scalar wins
    assert merged["notable_members"] == ["Fewmaster Gholcag", "Kansaldi Fire-Eyes"]  # union
    assert merged["source_chapters"] == ["Chapter 3", "Chapter 5", "Chapter 7"]
    assert "source_chapter" not in merged


def test_merge_duplicate_group_single_item_passthrough():
    items_by_name = {"Solo": {"name": "Solo", "goal": "x"}}
    assert merge_duplicate_group(items_by_name, ["Solo"]) == {"name": "Solo", "goal": "x"}


# ─── CHAPTER-AWARE SCENE MATCHING ──────────────────────────────────────────

CAMP_FOLDER = "Dragonlance: Shadow of the Dragon Queen"


def _scene_cands(*pairs):
    return [{"name": n, "uuid": f"Scene.{i}", "folder": f"{CAMP_FOLDER} / {ch}"}
            for i, (n, ch) in enumerate(pairs)]


def test_extract_map_reference():
    assert extract_map_reference("The Battlefield (Map 7.5)") == "7.5"
    assert extract_map_reference("Map 3.1: Vogler") == "3.1"
    # Area keys are NOT map numbers - ambiguous between candidates
    assert extract_map_reference("M9: Demelin's Apartment") is None
    assert extract_map_reference("Council Meeting") is None
    assert extract_map_reference("") is None


def test_match_scenes_uses_explicit_map_reference():
    """An explicit 'Map 7.5' beats text similarity, which picks a wrong map."""
    items = [{"name": "The Battlefield (Map 7.5)", "source_chapter": "Chapter 7: Siege of Kalaman"}]
    cands = _scene_cands(
        ("Map 6.3: Occupied Mansion", "Chapter 6: City of Lost Names"),
        ("Map 7.5: Clash of Fallen Flames", "Chapter 7: Siege of Kalaman"),
    )
    res = match_scenes_to_existing(items, cands)
    assert res["matched"]["The Battlefield (Map 7.5)"] == "Scene.1"


def test_match_scenes_same_chapter_rescues_a_below_threshold_pair():
    """0.41 is under the global bar but unambiguous within its own chapter."""
    items = [{"name": "High Hill Battlefield", "source_chapter": "Chapter 3: When Home Burns"}]
    cands = _scene_cands(
        ("Map 3.2: Battle of High Hill", "Chapter 3: When Home Burns"),
        ("Map 6.2: City of Lost Names", "Chapter 6: City of Lost Names"),
    )
    assert match_names_to_existing(["High Hill Battlefield"], cands)["matched"] == {}
    assert match_scenes_to_existing(items, cands)["matched"] == {"High Hill Battlefield": "Scene.0"}


def test_match_scenes_strong_name_match_outranks_wrong_chapter_tag():
    """The generated chapter tag is LLM output and can be wrong - a strong
    name match must win over a weak same-chapter one."""
    items = [{"name": "The Bastion of Takhisis", "source_chapter": "Chapter 6: City of Lost Names"}]
    cands = _scene_cands(
        ("Map 6.5: Threshold of the Heavens", "Chapter 6: City of Lost Names"),
        ("Map 7.3: Bastion of Takhisis", "Chapter 7: Siege of Kalaman"),
    )
    res = match_scenes_to_existing(items, cands)
    assert res["matched"]["The Bastion of Takhisis"] == "Scene.1"


def test_match_scenes_does_not_double_claim_within_a_chapter():
    items = [
        {"name": "Wakenreth — The Tower", "source_chapter": "Chapter 5: The Northern Wastes"},
        {"name": "Wakenreth — The Gate", "source_chapter": "Chapter 5: The Northern Wastes"},
    ]
    cands = _scene_cands(("Map 5.4: Wakenreth", "Chapter 5: The Northern Wastes"))
    res = match_scenes_to_existing(items, cands)
    assert len(res["matched"]) == 1 and len(res["unmatched"]) == 1


def test_match_scenes_without_chapter_falls_back_to_global_threshold():
    items = [{"name": "Map 6.1: Path of Memories"}]  # no source_chapter
    cands = _scene_cands(("Map 6.1: Path of Memories", "Chapter 6: City of Lost Names"))
    assert match_scenes_to_existing(items, cands)["matched"] == {"Map 6.1: Path of Memories": "Scene.0"}


def test_folder_scoping_matches_a_path_segment_not_the_whole_path():
    """Scenes live under '<campaign> / <chapter>'; the deepest folder is the
    chapter, so whole-path comparison made this filter a silent no-op."""
    cands = _scene_cands(("Map 3.1: Vogler", "Chapter 3: When Home Burns"))
    cands.append({"name": "Other Book Scene", "uuid": "Scene.X",
                  "folder": "Icewind Dale: Rime of the Frostmaiden / Chapter 1"})
    scoped = filter_candidates_by_campaign_folder(cands, CAMP_FOLDER)
    assert [c["uuid"] for c in scoped] == ["Scene.0"]


# ─── PUBLISHED ROLL TABLES FOLDED INTO NOTES ───────────────────────────────


def test_format_rolltables_strips_html_and_foundry_links():
    """Table results carry inline <a> markup and @Compendium/@UUID document
    links; the LLM must see labels, not opaque Foundry ids."""
    tables = [{
        "name": "Encounter",
        "description": "<p>Roll each hour.</p>",
        "results": [
            "A family of @Compendium[world.ddb-krynn-ddb-monsters.ddbCommoner16829]{commoners} flees.",
            "<a>Airborne Assassin</a> (see below)",
            "See @UUID[JournalEntry.abc]{Vogler Gazetteer}",
        ],
    }]
    out = format_rolltables_for_notes(tables)
    assert "### Encounter" in out
    assert "Roll each hour." in out
    assert "- A family of commoners flees." in out
    assert "- Airborne Assassin (see below)" in out
    assert "- See Vogler Gazetteer" in out
    for leaked in ("@Compendium", "@UUID", "ddbCommoner16829", "<a>", "<p>"):
        assert leaked not in out


def test_format_rolltables_empty_is_empty_string():
    assert format_rolltables_for_notes([]) == ""


def test_journal_text_strips_foundry_enrichers():
    entries = [{"name": "Ch", "pages": [
        {"name": "p", "html": "<p>The @Compendium[world.pack.id]{Brass Crab} sits on the wharf here.</p>"}]}]
    pages = journal_entries_to_pages(entries, min_chars_per_page=5)
    text = pages[0][1]
    assert "Brass Crab" in text
    assert "@Compendium" not in text and "world.pack.id" not in text


# ─── WORLD-JOURNAL SCOPING ─────────────────────────────────────────────────


def test_folder_matches_campaign_on_any_segment():
    camp = "Dragonlance: Shadow of the Dragon Queen"
    assert folder_matches_campaign(f"{camp} / Chapter 3: When Home Burns", camp)
    assert folder_matches_campaign(camp, camp)
    assert not folder_matches_campaign("Icewind Dale: Rime of the Frostmaiden / Chapter 1", camp)
    assert not folder_matches_campaign("", camp)
    assert not folder_matches_campaign(None, camp)


def test_is_adventure_content_entry_keeps_non_chapter_prose():
    """'War Comes to Krynn' is 67k chars of real adventure text that the
    Chapter-N/Appendix-X regex dropped."""
    assert is_adventure_content_entry("War Comes to Krynn")
    assert is_adventure_content_entry("Chapter 3: When Home Burns")
    assert not is_adventure_journal_entry("War Comes to Krynn")  # the old filter drops it


def test_is_adventure_content_entry_rejects_known_non_content():
    for junk in ("Credits", "Table of Contents", "DDB Meta-Data Notes",
                 "sequencerDatabase", "Rich Info Tooltips"):
        assert not is_adventure_content_entry(junk)


# ─── MAP PIN (NOTE LABEL) MATCHING ─────────────────────────────────────────


def _pin_cands(*specs):
    """specs: (map_name, chapter, [pin_label, ...])"""
    return [{"name": n, "uuid": f"Scene.{i}", "folder": f"{CAMP_FOLDER} / {ch}",
             "notes": [{"label": l, "x": 0, "y": 0} for l in pins]}
            for i, (n, ch, pins) in enumerate(specs)]


def test_pin_label_match_beats_a_useless_map_title():
    """'The Brass Crab' scores ~0.2 against 'Map 3.1: Vogler' but is an exact
    pin on it — the pin is what carries the linkage."""
    items = [{"name": "The Dock behind the Brass Crab",
              "source_chapter": "Chapter 3: When Home Burns"}]
    cands = _pin_cands(("Map 3.1: Vogler", "Chapter 3: When Home Burns",
                        ["The Brass Crab", "Wharf", "Market"]))
    assert match_names_to_existing(["The Dock behind the Brass Crab"], cands)["matched"] == {}
    res = match_scenes_to_existing(items, cands)
    assert res["matched"]["The Dock behind the Brass Crab"] == "Scene.0"
    assert res["areas"]["The Dock behind the Brass Crab"] == "The Brass Crab"


def test_pin_matches_are_chapter_restricted():
    """'Hall of Sight' (Chapter 2) must not take the 'R1: Hall of Knights'
    pin on a Chapter 4 map just because both say "Hall"."""
    items = [{"name": "Hall of Sight", "source_chapter": "Chapter 2: Prelude to War"}]
    cands = _pin_cands(("Map 4.4: Raided Catacombs", "Chapter 4: Shadow of War",
                        ["R1: Hall of Knights", "R2: Crypts"]))
    res = match_scenes_to_existing(items, cands)
    assert res["matched"] == {}
    assert res["unmatched"] == ["Hall of Sight"]


def test_several_scenes_may_share_one_map_via_different_pins():
    """A town map carries many areas; linking each area scene to it is the
    point — it stops the pipeline generating a fake map per area."""
    ch = "Chapter 6: City of Lost Names"
    items = [{"name": "M1: Hall of Betrayal", "source_chapter": ch},
             {"name": "M5: Bone Gauntlet", "source_chapter": ch},
             {"name": "M9: Demelin's Apartment", "source_chapter": ch}]
    cands = _pin_cands(("Map 6.1: Path of Memories", ch,
                        ["M1: Hall of Betrayal", "M5: Bone Gauntlet", "M9: Demelin’s Apartment"]))
    res = match_scenes_to_existing(items, cands)
    assert len(res["matched"]) == 3
    assert set(res["matched"].values()) == {"Scene.0"}
    assert len(res["areas"]) == 3


def test_dedicated_map_outranks_a_pin_on_an_overview_map():
    """'Blue Phoenix Shrine — Altar Room' should take the shrine's own map,
    not the 'C: Blue Phoenix Shrine' pin on the regional overview."""
    ch = "Chapter 5: The Northern Wastes"
    items = [{"name": "Blue Phoenix Shrine — Altar Room", "source_chapter": ch}]
    cands = _pin_cands(
        ("Map 5.1: Kalaman And Northern Wastes", ch, ["C: Blue Phoenix Shrine", "E: Wakenreth"]),
        ("Map 5.2: Blue Phoenix Shrine", ch, []),
    )
    res = match_scenes_to_existing(items, cands)
    assert res["matched"]["Blue Phoenix Shrine — Altar Room"] == "Scene.1"
    assert "Blue Phoenix Shrine — Altar Room" not in res["areas"]


def test_known_areas_block_instructs_verbatim_naming():
    from campaign.importer import build_known_areas_block
    block = build_known_areas_block(["The Brass Crab", "Wharf"])
    assert "The Brass Crab" in block and "Wharf" in block
    assert "VERBATIM" in block
    assert build_known_areas_block([]) == ""
    assert build_known_areas_block(None) == ""


def test_pass2_prompts_carry_known_areas():
    u1 = build_pass2_user("notes", "Camp", "1-5", known_areas=["The Brass Crab"])
    assert "The Brass Crab" in u1
    from campaign.importer import build_pass2_chapter_user
    u2 = build_pass2_chapter_user("notes", "Camp", "1-5", "Chapter 3", {}, known_areas=["Wharf"])
    assert "Wharf" in u2
