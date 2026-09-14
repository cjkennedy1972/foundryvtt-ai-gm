#!/usr/bin/env python3
"""Mock-based unit tests for scene/awareness.SceneAwareness.

Covers:
- load_scene: success path (state tracker, LRU cache, familiarity, callback),
  callback exception isolation, foundry failure -> {}
- on_scene_change: no-op on same scene, first visit vs returning visit,
  campaign_loader/llm_manager wiring
- refresh_scene_tokens: fresh cache update, stale cache reload, evicted
  recreation, foundry failure -> []
- get_scene_description: unknown scene, cached content, stale reload,
  hostile/ally disposition split
- get_familiarity / get_context_summary / LRU eviction at the limit

All Foundry/state/loader I/O is mocked — no relay, no network.

Run:
    cd ai-engine && python -m pytest tests/test_scene_awareness.py -v
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scene.awareness import SceneAwareness, MAX_CACHED_SCENES, SCENE_CACHE_TTL_SECONDS


def make_foundry(details=None, tokens=None, fail=False):
    f = MagicMock()
    if fail:
        f.get_scene_details = AsyncMock(side_effect=RuntimeError("relay down"))
        f.get_scene_tokens = AsyncMock(side_effect=RuntimeError("relay down"))
    else:
        f.get_scene_details = AsyncMock(return_value=details if details is not None else {"name": "s"})
        f.get_scene_tokens = AsyncMock(return_value=tokens if tokens is not None else [])
    return f


def make_state_tracker():
    st = MagicMock()
    st.set_scene_data = AsyncMock()
    st.set_scene = AsyncMock()
    st.set_npc_context = AsyncMock()
    st.set_encounter_context = AsyncMock()
    return st


def make_loader(npc_context="npc ctx", world_context="world ctx", encounter=None):
    loader = MagicMock()
    loader.get_npc_context_sync = MagicMock(return_value=npc_context)
    loader.get_world_context_sync = MagicMock(return_value=world_context)
    loader.get_encounter_context_for_scene = MagicMock(return_value=encounter)
    return loader


def make_awareness(foundry=None, loader=None, llm=None):
    return SceneAwareness(
        foundry=foundry or make_foundry(),
        state_tracker=make_state_tracker(),
        campaign_loader=loader,
        llm_manager=llm,
    )


TOKENS = [
    {"name": "Goblin", "x": 100, "y": 200, "disposition": -1},
    {"name": "Aria", "x": 300, "y": 400, "disposition": 1},
]


# ── load_scene ───────────────────────────────────────────────────────────────


def test_load_scene_success_updates_tracker_cache_and_familiarity():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    ctx = asyncio.run(aw.load_scene("Cellar"))

    assert ctx["name"] == "Cellar"
    assert ctx["tokens"] == TOKENS
    assert aw._current_scene == "Cellar"
    assert "Cellar" in aw._scene_data
    assert aw.get_familiarity("Cellar") == 1
    st = aw.state_tracker
    st.set_scene_data.assert_awaited_once()
    payload = st.set_scene_data.await_args.args[0]
    assert payload["token_count"] == 2
    # Second visit bumps familiarity
    asyncio.run(aw.load_scene("Cellar"))
    assert aw.get_familiarity("Cellar") == 2


def test_load_scene_notifies_callback_with_familiarity():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    cb = AsyncMock()
    aw.set_scene_change_callback(cb)
    asyncio.run(aw.load_scene("Hall"))

    cb.assert_awaited_once()
    event = cb.await_args.args[0]
    assert event["type"] == "scene_loaded"
    assert event["scene_name"] == "Hall"
    assert event["token_count"] == 2
    assert event["familiarity"] == 1


def test_load_scene_callback_exception_does_not_kill_load():
    """A crashing callback must not turn a good load into a failure."""
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    aw.set_scene_change_callback(AsyncMock(side_effect=RuntimeError("cb broke")))
    ctx = asyncio.run(aw.load_scene("Hall"))
    assert ctx["name"] == "Hall"
    assert aw._current_scene == "Hall"


def test_load_scene_foundry_failure_returns_empty_dict():
    aw = make_awareness(foundry=make_foundry(fail=True))
    assert asyncio.run(aw.load_scene("Hall")) == {}
    assert "Hall" not in aw._scene_data


# ── on_scene_change ──────────────────────────────────────────────────────────


def test_on_scene_change_no_op_when_already_there():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    st = aw.state_tracker
    asyncio.run(aw.on_scene_change("Cellar"))
    st.set_scene.assert_awaited_once()
    # Second call for the same scene must not re-set or reload.
    st.set_scene.reset_mock()
    asyncio.run(aw.on_scene_change("Cellar"))
    st.set_scene.assert_not_awaited()
    aw.foundry.get_scene_details.assert_awaited()  # only the first visit loaded


def test_on_scene_change_first_visit_full_loads():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.on_scene_change("Cellar"))
    assert "Cellar" in aw._scene_data
    assert aw.get_familiarity("Cellar") == 1


def test_on_scene_change_returning_visit_refreshes_not_reloads():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.on_scene_change("Cellar"))  # first visit -> full load
    aw.foundry.get_scene_details.reset_mock()
    # Second visit: cache is fresh, so a token refresh (not a full load)
    asyncio.run(aw.on_scene_change("Cellar"))
    aw.foundry.get_scene_details.assert_not_awaited()
    aw.foundry.get_scene_tokens.assert_awaited()  # refresh path fired


def test_on_scene_change_wires_loader_and_llm():
    loader = make_loader(encounter={"id": "enc1", "name": "Goblin attack"})
    llm = MagicMock()
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS), loader=loader, llm=llm)
    asyncio.run(aw.on_scene_change("Dungeon"))

    llm.set_dynamic_npc_context.assert_called_once_with("npc ctx")
    llm.set_dynamic_world_context.assert_called_once_with("world ctx")
    llm.set_current_scene.assert_called_once_with("Dungeon")
    st = aw.state_tracker
    st.set_npc_context.assert_awaited_once()
    st.set_encounter_context.assert_awaited_with({"id": "enc1", "name": "Goblin attack"})


def test_on_scene_change_no_encounter_clears_context():
    loader = make_loader(encounter=None)
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS), loader=loader)
    asyncio.run(aw.on_scene_change("Dungeon"))
    aw.state_tracker.set_encounter_context.assert_awaited_with("")


# ── refresh_scene_tokens ─────────────────────────────────────────────────────


def test_refresh_updates_fresh_cache_in_place():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.load_scene("Cellar"))
    aw.state_tracker.set_scene_data.reset_mock()

    new_tokens = TOKENS + [{"name": "Skeleton", "x": 1, "y": 2, "disposition": -1}]
    aw.foundry.get_scene_tokens = AsyncMock(return_value=new_tokens)
    out = asyncio.run(aw.refresh_scene_tokens("Cellar"))

    assert out == new_tokens
    assert aw._scene_data["Cellar"]["tokens"] == new_tokens
    aw.state_tracker.set_scene_data.assert_awaited_once()
    # Full-load path must NOT have fired for the refresh.
    aw.foundry.get_scene_details.assert_awaited_once()  # only the original load


def test_refresh_stale_cache_triggers_full_reload():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.load_scene("Cellar"))
    # Backdate the cache entry beyond the TTL.
    aw._scene_data["Cellar"]["_cached_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=SCENE_CACHE_TTL_SECONDS + 5
    )
    assert aw._is_scene_cache_stale("Cellar")

    out = asyncio.run(aw.refresh_scene_tokens("Cellar"))
    assert out == TOKENS
    # Full reload happened (a second get_scene_details call).
    assert aw.foundry.get_scene_details.await_count == 2


def test_refresh_evicted_scene_recreates_cache_entry():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    # Never loaded -> not in cache; refresh must create a minimal entry.
    out = asyncio.run(aw.refresh_scene_tokens("New Place"))
    assert out == TOKENS
    assert "New Place" in aw._scene_data
    assert aw._scene_data["New Place"]["tokens"] == TOKENS


def test_refresh_foundry_failure_returns_empty_list():
    aw = make_awareness(foundry=make_foundry(fail=True))
    assert asyncio.run(aw.refresh_scene_tokens("Cellar")) == []


# ── staleness helpers ────────────────────────────────────────────────────────


def test_stale_when_missing_or_untimestamped():
    aw = make_awareness()
    assert aw._is_scene_cache_stale("missing")
    aw._scene_data["x"] = {"tokens": []}  # no _cached_at
    assert aw._is_scene_cache_stale("x")


def test_stale_by_age_boundary():
    aw = make_awareness()
    fresh = datetime.now(timezone.utc)
    aw._scene_data["a"] = {"_cached_at": fresh}
    assert not aw._is_scene_cache_stale("a")
    old = fresh - timedelta(seconds=SCENE_CACHE_TTL_SECONDS + 1)
    aw._scene_data["b"] = {"_cached_at": old}
    assert aw._is_scene_cache_stale("b")


# ── description / summaries ──────────────────────────────────────────────────


def test_description_unknown_scene_message():
    aw = make_awareness(foundry=make_foundry())
    st = aw.state_tracker
    st.state = MagicMock()
    st.state.current_scene = None
    assert "Unknown scene" in asyncio.run(aw.get_scene_description())


def test_description_from_cache_with_dispositions():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.load_scene("Cellar"))
    desc = asyncio.run(aw.get_scene_description("Cellar"))

    assert desc.startswith("## Scene: Cellar")
    assert "Familiarity" in desc
    assert "Entities on scene (2)" in desc
    assert "- Goblin (hostile) at position (100, 200)" in desc
    assert "- Aria (ally/PC) at position (300, 400)" in desc


def test_description_stale_cache_reloads_first():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.load_scene("Cellar"))
    aw._scene_data["Cellar"]["_cached_at"] = datetime.now(timezone.utc) - timedelta(seconds=9999)
    asyncio.run(aw.get_scene_description("Cellar"))
    assert aw.foundry.get_scene_details.await_count == 2  # reload fired


def test_description_falls_back_to_tracker_scene():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.load_scene("Cellar"))  # sets cache
    st = aw.state_tracker
    st.state = MagicMock()
    st.state.current_scene = "Cellar"
    desc = asyncio.run(aw.get_scene_description())  # no explicit name
    assert desc.startswith("## Scene: Cellar")


def test_context_summary_groups_hostiles_and_allies():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    asyncio.run(aw.load_scene("Cellar"))
    summary = aw.get_context_summary()
    assert "**Current Scene:** Cellar" in summary
    assert "**Entities on scene:** 2" in summary
    assert "**Hostiles:** Goblin" in summary
    assert "**Allies/PCs:** Aria" in summary


def test_context_summary_empty_when_no_scene():
    aw = make_awareness()
    assert aw.get_context_summary() == ""


# ── LRU eviction ─────────────────────────────────────────────────────────────


def test_lru_evicts_oldest_beyond_limit():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    for i in range(MAX_CACHED_SCENES + 3):
        name = f"Scene {i}"
        aw._cache_scene(name, {"name": name, "tokens": []})

    assert len(aw._scene_data) == MAX_CACHED_SCENES
    # Oldest (Scene 0..2) evicted, newest kept.
    assert "Scene 0" not in aw._scene_data
    assert "Scene 1" not in aw._scene_data
    assert "Scene 2" not in aw._scene_data
    assert f"Scene {MAX_CACHED_SCENES + 2}" in aw._scene_data


def test_lru_update_moves_entry_to_mru():
    aw = make_awareness(foundry=make_foundry(tokens=TOKENS))
    for i in range(MAX_CACHED_SCENES):
        aw._cache_scene(f"S{i}", {"name": f"S{i}"})
    # Re-touch S0 -> it becomes MRU, so the next eviction hits S1.
    aw._cache_scene("S0", {"name": "S0"})
    aw._cache_scene("New", {"name": "New"})

    assert "S1" not in aw._scene_data
    assert "S0" in aw._scene_data
    assert "New" in aw._scene_data
