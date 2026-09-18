"""Characterisation of generate_assets before its four blocks are split.

At 22% this was the least-covered orchestrator method. Four blocks generate
scene maps, location maps, NPC portraits and prologue panels into one results
dict; these pin what each contributes and how failures are absorbed.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.orchestrator import CampaignOrchestrator


def _map_generator(ok=True):
    gen = MagicMock()
    payload = ({"status": "success", "output_file": "out/map.png", "provider": "comfyui"}
               if ok else {"status": "error", "error": "no backend", "provider": "none"})
    gen.generate_map = AsyncMock(return_value=payload)
    gen.generate_map_controlnet = AsyncMock(return_value=payload)
    gen.generate_portrait = AsyncMock(return_value=payload)
    gen.generate_prologue_panel = AsyncMock(return_value=payload)
    gen.generate_layout_mask = AsyncMock(return_value=None)
    return gen


async def _generate(campaign_data, tmp_path, gen=None):
    return await CampaignOrchestrator().generate_assets(
        campaign_data, gen or _map_generator(), Path(tmp_path)
    )


@pytest.mark.asyncio
async def test_an_empty_campaign_returns_the_documented_shape(tmp_path):
    result = await _generate({}, tmp_path)

    assert result["maps"] == [] and result["portraits"] == []
    assert result["status"] == "completed"
    assert result["total_maps"] == 0 and result["total_portraits"] == 0


@pytest.mark.asyncio
async def test_only_scenes_flagged_map_needed_are_generated(tmp_path):
    gen = _map_generator()
    data = {"scenes": [
        {"name": "Crypt", "map_needed": True, "description": "dark"},
        {"name": "Tavern", "map_needed": False, "description": "warm"},
    ]}

    await _generate(data, tmp_path, gen)

    prompts = [c for c in gen.generate_map.call_args_list + gen.generate_map_controlnet.call_args_list]
    assert len(prompts) == 1, "a scene without map_needed must not cost a generation"


@pytest.mark.asyncio
async def test_portraits_are_generated_for_npcs_that_want_one(tmp_path):
    gen = _map_generator()
    data = {"npcs": [
        {"name": "Borin", "portrait_needed": True, "description": "grizzled"},
        {"name": "Mara", "portrait_needed": False},
    ]}

    await _generate(data, tmp_path, gen)

    assert gen.generate_portrait.await_count == 1


@pytest.mark.asyncio
async def test_a_failing_generator_is_absorbed_not_raised(tmp_path):
    """One unavailable backend must not abandon the whole campaign build."""
    gen = _map_generator(ok=False)
    data = {"scenes": [{"name": "Crypt", "map_needed": True, "description": "dark"}]}

    result = await _generate(data, tmp_path, gen)

    assert result["status"] in ("completed", "partial")


@pytest.mark.asyncio
async def test_a_raising_generator_is_absorbed_too(tmp_path):
    gen = _map_generator()
    gen.generate_map.side_effect = ConnectionError("ComfyUI down")
    gen.generate_map_controlnet.side_effect = ConnectionError("ComfyUI down")
    data = {"scenes": [{"name": "Crypt", "map_needed": True, "description": "dark"}]}

    result = await _generate(data, tmp_path, gen)

    assert "maps" in result


@pytest.mark.asyncio
async def test_prologue_panels_are_generated_when_present(tmp_path):
    gen = _map_generator()
    data = {"prologue": {"panels": [{"caption": "A storm gathers", "image_prompt": "storm"}]}}

    await _generate(data, tmp_path, gen)

    assert gen.generate_prologue_panel.await_count == 1
