"""Remaining branches of scene/awareness.SceneAwareness."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from scene.awareness import SceneAwareness


def _aw(tokens=None):
    f = MagicMock()
    f.get_scene_details = AsyncMock(return_value={})
    f.get_scene_tokens = AsyncMock(return_value=tokens or [])
    st = MagicMock()
    for n in ("set_scene_data", "set_scene"):
        setattr(st, n, AsyncMock())
    return SceneAwareness(f, st), f


@pytest.mark.asyncio
async def test_returning_visit_refreshes_tokens_instead_of_full_load():
    aw, f = _aw([{"id": "t1"}])
    await aw.load_scene("Cellar")
    aw._current_scene = "Hall"                       # we are elsewhere now
    f.get_scene_details.reset_mock()
    await aw.on_scene_change("Cellar")
    f.get_scene_details.assert_not_called()           # no full reload
    assert aw._current_scene == "Cellar"
    aw.state_tracker.set_scene.assert_awaited_with("Cellar")
    assert aw.state_tracker.set_scene_data.await_args.args[0]["name"] == "Cellar"
    assert "updated_at" in aw.state_tracker.set_scene_data.await_args.args[0]
    assert aw.get_familiarity("Cellar") == 1          # a refresh does not count as a new exploration


@pytest.mark.asyncio
async def test_refresh_recreates_cache_entry_evicted_while_fetching():
    aw, f = _aw()
    await aw.load_scene("Cellar")

    async def evicting_fetch(name):
        del aw._scene_data["Cellar"]                  # LRU eviction lands during the await
        return [{"name": "Rat"}]
    f.get_scene_tokens = AsyncMock(side_effect=evicting_fetch)
    tokens = await aw.refresh_scene_tokens("Cellar")
    assert tokens == [{"name": "Rat"}]
    cached = aw._scene_data["Cellar"]
    assert cached["tokens"] == tokens and "_cached_at" in cached


def test_context_summary_groups_by_disposition():
    aw, _ = _aw()
    assert aw.get_context_summary() == ""
    aw._current_scene = "Cellar"
    aw._scene_data["Cellar"] = {"tokens": []}
    assert aw.get_context_summary() == "**Current Scene:** Cellar"
    aw._scene_data["Cellar"] = {"tokens": [{"name": "Rat", "disposition": -1}]}
    out = aw.get_context_summary()
    assert "**Hostiles:** Rat" in out and "Allies" not in out
    aw._scene_data["Cellar"] = {"tokens": [{"name": "Aria", "disposition": 1}, {"disposition": 0}]}
    out = aw.get_context_summary()
    assert "**Allies/PCs:** Aria, ?" in out and "Hostiles" not in out


@pytest.mark.asyncio
async def test_refresh_failure_returns_empty_and_keeps_cache():
    aw, f = _aw([{"id": "t1"}])
    await aw.load_scene("Cellar")
    f.get_scene_tokens = AsyncMock(side_effect=RuntimeError("relay down"))
    assert await aw.refresh_scene_tokens("Cellar") == []
    assert aw._scene_data["Cellar"]["tokens"] == [{"id": "t1"}]
