"""Tests for utils.path_safety — filename sanitization and path-traversal defense.

This is security-critical code (it guards every file write that uses
LLM-generated or user-supplied names), so it gets thorough coverage of the
attack shapes it exists to stop.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.path_safety import (
    sanitize_filename,
    validate_contained_path,
    validate_and_open_file,
    validate_and_delete_tree,
)


# ── sanitize_filename ────────────────────────────────────────────────────────

def test_sanitize_plain_name_and_extension_preserved():
    assert sanitize_filename("scene_map.jpg") == "scene_map.jpg"


def test_sanitize_strips_path_separators():
    # Both POSIX and Windows separators are removed from the basename.
    assert "/" not in sanitize_filename("a/b/c.txt")
    assert "\\" not in sanitize_filename("a\\b\\c.txt")


def test_sanitize_replaces_dangerous_characters():
    out = sanitize_filename('na*me?".txt')
    for ch in '*?"<>|:':
        assert ch not in out


def test_sanitize_removes_leading_dots():
    assert not sanitize_filename("...hidden.txt").startswith(".")


def test_sanitize_collapses_double_dots():
    assert ".." not in sanitize_filename("foo..bar.txt")


def test_sanitize_traversal_attempt_is_neutralized():
    out = sanitize_filename("../../etc/passwd")
    assert "/" not in out and ".." not in out


def test_sanitize_truncates_but_keeps_extension():
    name = "a" * 300 + ".png"
    out = sanitize_filename(name, max_length=20)
    assert len(out) <= 20
    assert out.endswith(".png")


def test_sanitize_truncates_without_extension():
    out = sanitize_filename("b" * 300, max_length=16)
    assert len(out) <= 16


def test_sanitize_rejects_empty_and_non_string():
    with pytest.raises(ValueError):
        sanitize_filename("")
    with pytest.raises(ValueError):
        sanitize_filename(None)  # type: ignore[arg-type]


def test_sanitize_rejects_only_invalid_characters():
    with pytest.raises(ValueError):
        sanitize_filename("/////")


def test_sanitize_rejects_reserved_device_name():
    with pytest.raises(ValueError):
        sanitize_filename("CON.txt")
    with pytest.raises(ValueError):
        sanitize_filename("nul")


def test_sanitize_extension_is_limited_to_alphanumeric():
    out = sanitize_filename("file.t@x!t")
    # extension keeps only alnum chars
    assert out.split(".")[-1] == "txt"


# ── validate_contained_path ──────────────────────────────────────────────────

def test_contained_path_allows_child(tmp_path):
    result = validate_contained_path("sub/child.txt", str(tmp_path))
    assert str(result).startswith(str(tmp_path.resolve()))


def test_contained_path_blocks_dotdot_escape(tmp_path):
    with pytest.raises(ValueError):
        validate_contained_path("../escape.txt", str(tmp_path))


def test_contained_path_absolute_outside_is_blocked(tmp_path):
    # An absolute path replaces the base when joined; the relative_to check
    # still rejects it because it lands outside base.
    with pytest.raises(ValueError):
        validate_contained_path("/etc/passwd", str(tmp_path))


def test_contained_path_rejects_absolute_when_disallowed(tmp_path):
    with pytest.raises(ValueError):
        validate_contained_path("/anything", str(tmp_path), allow_relative=False)


def test_contained_path_rejects_non_string(tmp_path):
    with pytest.raises(ValueError):
        validate_contained_path("", str(tmp_path))
    with pytest.raises(ValueError):
        validate_contained_path(None, str(tmp_path))  # type: ignore[arg-type]


# ── validate_and_open_file ───────────────────────────────────────────────────

def test_open_read_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_and_open_file("missing.txt", str(tmp_path), mode="r")


def test_open_write_creates_parent_and_writes(tmp_path):
    path, fh = validate_and_open_file("nested/dir/out.txt", str(tmp_path), mode="w")
    try:
        fh.write("hello")
    finally:
        fh.close()
    assert path.exists()
    assert path.read_text() == "hello"


def test_open_read_existing_returns_handle(tmp_path):
    (tmp_path / "data.txt").write_text("x")
    path, fh = validate_and_open_file("data.txt", str(tmp_path), mode="r")
    try:
        assert fh.read() == "x"
    finally:
        fh.close()


# ── validate_and_delete_tree ─────────────────────────────────────────────────

def test_delete_tree_nonexistent_raises(tmp_path):
    with pytest.raises(ValueError):
        validate_and_delete_tree(str(tmp_path / "nope"))


def test_delete_tree_refuses_without_confirm_false(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    with pytest.raises(PermissionError):
        validate_and_delete_tree(str(tmp_path), confirm=True)


def test_delete_tree_refuses_home_directory():
    with pytest.raises(ValueError):
        validate_and_delete_tree(os.path.expanduser("~"), confirm=False)


def test_delete_tree_deletes_when_confirmed_false(tmp_path):
    target = tmp_path / "victim"
    target.mkdir()
    (target / "a.txt").write_text("x")
    (target / "b.txt").write_text("y")
    count = validate_and_delete_tree(str(target), confirm=False)
    assert count >= 2
    assert not target.exists()
