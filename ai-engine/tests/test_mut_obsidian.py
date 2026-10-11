"""Mutation-audit tests: delete_campaign's containment check and registry corruption handling."""
import asyncio
import json

import pytest

from campaign import obsidian_sync as obs
from test_cov_campaign_obsidian import _manifest, _reg


def test_delete_campaign_second_layer_rejects_escaping_sanitized_name(tmp_path, monkeypatch):
    (tmp_path / obs.CAMPAIGNS_DIR_NAME).mkdir()
    sibling = tmp_path / "x"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("k")
    monkeypatch.setattr(obs, "_sanitize_filename", lambda n: "../x")
    assert asyncio.run(obs.delete_campaign("anything", str(tmp_path))) is False
    assert (sibling / "keep.txt").exists()


def test_transient_registry_read_error_propagates_without_renaming(tmp_path):
    reg = _reg(tmp_path)
    reg.mkdir()  # reading a directory raises OSError, not a corrupt-JSON error
    with pytest.raises(OSError):
        asyncio.run(obs.save_campaign_registry(tmp_path / "f", _manifest(tmp_path)))
    assert reg.is_dir() and not reg.with_name(reg.name + ".corrupt").exists()


def test_second_corrupt_registry_does_not_overwrite_the_first_copy(tmp_path):
    reg = _reg(tmp_path)
    for bad in ("{first", "{second"):
        reg.write_text(bad)
        asyncio.run(obs.save_campaign_registry(tmp_path / "f", _manifest(tmp_path)))
    kept = sorted(p.read_text() for p in reg.parent.glob(reg.name + ".corrupt*"))
    assert kept == ["{first", "{second"]
    assert json.loads(reg.read_text())["campaigns"][0]["name"] == "Camp"
