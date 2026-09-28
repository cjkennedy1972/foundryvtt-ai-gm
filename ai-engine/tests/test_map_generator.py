"""Portrait workflow checks for MapGenerator (no ComfyUI needed)."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.map_generator import MapGenerator


def _portrait_workflow(description, tmp_path):
    gen = MapGenerator()
    with patch.object(MapGenerator, "_checked_output_dir", lambda self, d: tmp_path), \
         patch.object(MapGenerator, "_submit_and_wait", new=AsyncMock(return_value={})) as submit:
        asyncio.run(gen.generate_portrait_comfyui(description, tmp_path, seed=1))
    return submit.await_args.args[0]


def test_portrait_is_framed_single_subject_and_not_tall(tmp_path):
    wf = _portrait_workflow(
        "A dwarf captain of the Ironclad Regiment mercenaries. He owes Kalaman "
        "money and hides it from his men.", tmp_path)

    positive = wf["4"]["inputs"]["text"]
    assert positive.startswith("head and shoulders portrait of A dwarf captain")
    assert "owes Kalaman" not in positive          # story sentences dropped
    negative = wf["5"]["inputs"]["text"]
    for term in ("two faces", "multiple people", "split image", "full body", "text"):
        assert term in negative
    # 512x768 produced stacked/doubled faces on SD 1.5
    assert (wf["7"]["inputs"]["width"], wf["7"]["inputs"]["height"]) == (512, 640)
