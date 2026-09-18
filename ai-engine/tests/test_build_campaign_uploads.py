"""Characterisation of build_campaign's asset-upload phase before collapsing it.

Three near-identical 20-line blocks upload maps, portraits and prologue
panels. build_campaign sits at 28% coverage, the lowest of the orchestrator
methods, so these pin the gating rules and the result keys before the
duplication is collapsed into one loop.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.orchestrator import CampaignOrchestrator


def _summary(uploaded=1):
    return {"uploaded": uploaded, "failed": 0, "errors": []}


def _orch():
    orch = CampaignOrchestrator()
    orch.upload_maps_to_foundry = AsyncMock(return_value=_summary())
    orch.upload_portraits_to_foundry = AsyncMock(return_value=_summary())
    orch.upload_prologue_to_foundry = AsyncMock(return_value=_summary())
    return orch


async def _run_uploads(orch, campaign_data, asset_info, foundry_client=MagicMock()):
    """Drive just the upload phase the way build_campaign does."""
    result = {}
    await orch._upload_generated_assets(
        campaign_data, asset_info, foundry_client, "out", "camp", result, lambda *a, **k: None
    )
    return result


@pytest.mark.asyncio
async def test_generated_maps_are_uploaded_and_summarised():
    orch = _orch()

    result = await _run_uploads(orch, {}, {"total_maps": 2})

    orch.upload_maps_to_foundry.assert_awaited_once()
    assert result["upload_summary"]["uploaded"] == 1


@pytest.mark.asyncio
async def test_premade_maps_upload_even_with_nothing_generated():
    """Import mode matches existing files, so total_maps is 0 with real work to do."""
    orch = _orch()

    await _run_uploads(orch, {"scenes": [{"map_file": "crypt.webp"}]}, {"total_maps": 0})

    orch.upload_maps_to_foundry.assert_awaited_once()


@pytest.mark.asyncio
async def test_premade_portraits_upload_even_with_nothing_generated():
    orch = _orch()

    await _run_uploads(orch, {"npcs": [{"portrait_file": "borin.webp"}]}, {"total_portraits": 0})

    orch.upload_portraits_to_foundry.assert_awaited_once()


@pytest.mark.asyncio
async def test_prologue_panels_have_no_premade_fallback():
    """Unlike maps and portraits, prologue uploads only on generated panels."""
    orch = _orch()

    await _run_uploads(orch, {}, {"total_prologue_panels": 0})

    orch.upload_prologue_to_foundry.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_uploader_writes_its_own_result_key():
    orch = _orch()

    result = await _run_uploads(
        orch, {}, {"total_maps": 1, "total_portraits": 1, "total_prologue_panels": 1}
    )

    assert set(result) == {"upload_summary", "portrait_upload_summary", "prologue_upload_summary"}


@pytest.mark.asyncio
async def test_nothing_uploads_without_a_foundry_client():
    orch = _orch()

    result = await _run_uploads(
        orch, {"scenes": [{"map_file": "x"}]}, {"total_maps": 5}, foundry_client=None
    )

    orch.upload_maps_to_foundry.assert_not_awaited()
    assert result == {}


@pytest.mark.asyncio
async def test_one_failing_uploader_does_not_stop_the_others():
    orch = _orch()
    orch.upload_maps_to_foundry.side_effect = ConnectionError("relay down")

    result = await _run_uploads(orch, {}, {"total_maps": 1, "total_portraits": 1})

    orch.upload_portraits_to_foundry.assert_awaited_once()
    assert "upload_summary" not in result
    assert "portrait_upload_summary" in result
