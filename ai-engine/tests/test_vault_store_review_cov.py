"""CampaignStore load/save/deployment round trips."""

import asyncio
import json

import pytest

from campaign import vault
from campaign.vault import CampaignNotFound, CampaignStore


def run(c):
    return asyncio.run(c)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = CampaignStore("My Camp", vault_path=str(tmp_path / "vault"))
    s.folder.mkdir(parents=True, exist_ok=True)
    return s


def test_load_missing_raises(store):
    assert not store.exists
    with pytest.raises(CampaignNotFound):
        run(store.load())


def test_save_then_load_roundtrip_keeps_unicode(store):
    run(store.save({"name": "Café", "scenes": []}))
    assert "Café" in store.campaign_file.read_text(encoding="utf-8")
    assert store.exists
    assert run(store.load(normalize=False)) == {"name": "Café", "scenes": []}


def test_load_normalizes_nested_sections(store):
    store.campaign_file.write_text(json.dumps({"campaign": {"name": "X", "scenes": [{"name": "s"}]}}))
    raw = run(store.load(normalize=False))
    assert "scenes" not in raw
    assert run(store.load())["scenes"] == [{"name": "s"}]


def test_deployment_state(store):
    assert run(store.load_deployment()) == {}
    run(store.save_deployment({"actors": {"a": "id1"}}))
    assert store.deployment_file.parent == store.assets_dir
    assert run(store.load_deployment()) == {"actors": {"a": "id1"}}
    store.deployment_file.write_text("{corrupt")
    assert run(store.load_deployment()) == {}


def test_paths_are_derived_from_sanitized_name(store):
    assert store.safe_name == vault.sanitize_filename("my camp")
    assert store.maps_dir.name == store.safe_name + "_maps"
