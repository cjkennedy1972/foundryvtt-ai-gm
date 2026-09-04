import asyncio
import json
from pathlib import Path

from campaign.checkpoints import BuildCheckpoint


def test_checkpoint_round_trip_is_atomic(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checkpoint = BuildCheckpoint("campaign")
    asyncio.run(checkpoint.save("assets", campaign_data={"campaign": {"name": "Demo"}}))

    assert asyncio.run(checkpoint.load()) == {
        "phase": "assets",
        "campaign_data": {"campaign": {"name": "Demo"}},
    }
    assert not (tmp_path / "campaign_assets" / "campaign" / "build_checkpoint.tmp").exists()


def test_invalid_checkpoint_is_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checkpoint = BuildCheckpoint("campaign")
    checkpoint.path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.path.write_text("not json", encoding="utf-8")
    assert asyncio.run(checkpoint.load()) is None
