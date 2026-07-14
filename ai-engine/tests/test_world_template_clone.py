"""Cloning a template world produces a configured, uniquely-named new world."""

import json
from pathlib import Path

import pytest

from config import settings
from foundry import world_template


def _make_template(worlds: Path, template_id: str = "_ai-gm-template") -> Path:
    """A minimal template world: manifest + a LevelDB-style settings store."""
    tdir = worlds / template_id
    settings_db = tdir / "data" / "settings"
    settings_db.mkdir(parents=True)
    (settings_db / "000076.ldb").write_bytes(b"module-config-payload")
    (settings_db / "LOCK").write_bytes(b"")  # process-local; must not be copied
    (tdir / "world.json").write_text(json.dumps({
        "id": template_id, "title": "Template", "system": "dnd5e",
        "coreVersion": "14.364", "systemVersion": "5.3.3",
    }))
    return tdir


@pytest.fixture
def worlds(tmp_path, monkeypatch):
    wdir = tmp_path / "Data" / "worlds"
    wdir.mkdir(parents=True)
    monkeypatch.setattr(settings, "foundry_data_path", str(tmp_path / "Data"))
    monkeypatch.setattr(settings, "foundry_world_template_id", "_ai-gm-template")
    return wdir


def test_clone_rewrites_identity_and_preserves_settings(worlds):
    _make_template(worlds)

    result = world_template.clone_world("The Sunless Citadel!", description="A dark hole.")

    new_dir = worlds / result.world_id
    manifest = json.loads((new_dir / "world.json").read_text())
    assert result.world_id == "the-sunless-citadel"      # slug of the title
    assert result.world_name == "The Sunless Citadel!"    # title kept verbatim
    assert result.system == "dnd5e"
    assert manifest["id"] == "the-sunless-citadel"
    assert manifest["title"] == "The Sunless Citadel!"
    assert manifest["description"] == "A dark hole."
    assert manifest["coreVersion"] == "14.364"            # version fields inherited
    # Module config (settings store) copied byte-for-byte; LOCK stripped.
    assert (new_dir / "data" / "settings" / "000076.ldb").read_bytes() == b"module-config-payload"
    assert not (new_dir / "data" / "settings" / "LOCK").exists()


def test_clone_suffixes_on_id_collision(worlds):
    _make_template(worlds)
    (worlds / "goblins").mkdir()  # pre-existing world with the target slug

    result = world_template.clone_world("Goblins")

    assert result.world_id == "goblins-2"
    assert (worlds / "goblins-2" / "world.json").is_file()


def test_clone_rejects_system_mismatch(worlds):
    _make_template(worlds)
    with pytest.raises(ValueError, match="system"):
        world_template.clone_world("PF2e Game", expected_system="pf2e")


def test_missing_template_is_actionable(worlds):
    with pytest.raises(ValueError, match="Template world"):
        world_template.clone_world("No Template")
