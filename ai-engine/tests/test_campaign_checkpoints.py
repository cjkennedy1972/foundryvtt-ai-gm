import asyncio
import json
from pathlib import Path

from campaign.checkpoints import BuildCheckpoint


def test_checkpoint_round_trip_is_atomic(tmp_path: Path):
    checkpoint = BuildCheckpoint(tmp_path / "campaign" / "build_checkpoint.json")
    asyncio.run(checkpoint.save("assets", campaign_data={"campaign": {"name": "Demo"}}))

    assert asyncio.run(checkpoint.load()) == {
        "phase": "assets",
        "campaign_data": {"campaign": {"name": "Demo"}},
    }
    assert not (tmp_path / "campaign" / "build_checkpoint.tmp").exists()


def test_invalid_checkpoint_is_ignored(tmp_path: Path):
    path = tmp_path / "build_checkpoint.json"
    path.write_text("not json", encoding="utf-8")
    assert asyncio.run(BuildCheckpoint(path).load()) is None
