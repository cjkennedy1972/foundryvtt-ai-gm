"""load_sources / load_maps must survive bad input and never hand out one id twice."""

from campaign import enrichment


def test_a_corrupt_pdf_is_skipped_and_the_rest_of_the_folder_loads(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    (tmp_path / "notes.md").write_text("A long enough paragraph of campaign notes. " * 5, encoding="utf-8")

    sources = enrichment.load_sources(str(tmp_path))

    assert [s["title"] for s in sources] == ["notes"]


def test_same_named_maps_in_two_folders_get_distinct_ids(tmp_path):
    for folder in ("Foo", "Bar"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "map.jpg").write_bytes(b"\xff\xd8\xff")

    ids = [m["id"] for m in enrichment.load_maps(str(tmp_path))]

    assert len(ids) == 2 and len(set(ids)) == 2
