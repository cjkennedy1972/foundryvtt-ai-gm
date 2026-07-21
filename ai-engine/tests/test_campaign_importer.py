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
