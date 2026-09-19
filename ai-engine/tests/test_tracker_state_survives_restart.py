#!/usr/bin/env python3
"""GameStateTracker changed state in memory and left persistence to the caller.

The tracker holds a Database and its GameState model carries campaign, scene,
combat, session_number and last_event — all of it durable by design, and
load() at startup restores it. But every mutator writes to self._state and
stops there. Only save(), reset() and load() touch the database, and calling
save() was the caller's job.

Thirteen call sites mutate. Seven follow with save(). The six that do not are
the live gameplay paths:

    scene/awareness.py:148        set_scene on scene change
    chat_listener.py:1757, 2566   set_scene
    chat_listener.py:1700, 1716   set_combat_mode
    executors.py:604, 617         set_combat_mode from the model's own action
    session.py:211                set_campaign on session create

Driving a real tracker against a real database, then loading a second tracker
from the same file:

    live    Oakhaven | The Sunken Chapel | combat: True  | session: 2
    reload  Oakhaven |                   | combat: False | session: 1

The dangerous direction is combat. combat/loop.py saves when it enters combat;
execute_end_combat does not save when the model ends it. An engine restarted
after that point comes back believing the fight is still on.

Run:
    cd ai-engine && python -m pytest tests/test_tracker_state_survives_restart.py -v
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence.db import Database
from state.models import GameMode
from state.tracker import GameStateTracker


async def _started():
    """A tracker on its own database, loaded, as startup.py builds it."""
    database = Database(os.path.join(tempfile.mkdtemp(), "tracker.db"))
    await database.init()
    tracker = GameStateTracker(database)
    await tracker.load()
    return database, tracker


async def _reloaded(db):
    """What the engine would come back to after a restart."""
    fresh = GameStateTracker(db)
    await fresh.load()
    return fresh.state


# ── the live gameplay paths ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_scene_change_survives_a_restart():
    """scene/awareness.py sets the scene and never saves."""
    db, tracker = await _started()
    await tracker.set_scene("The Sunken Chapel")

    assert (await _reloaded(db)).current_scene == "The Sunken Chapel"


@pytest.mark.asyncio
async def test_entering_combat_survives_a_restart():
    db, tracker = await _started()
    await tracker.set_combat_mode(in_combat=True, turn_order=["tok1", "tok2"])

    state = await _reloaded(db)
    assert state.combat.in_combat is True
    assert state.mode == GameMode.COMBAT
    assert state.combat.turn_order == ["tok1", "tok2"]


@pytest.mark.asyncio
async def test_leaving_combat_survives_a_restart():
    """The dangerous direction: the loop saves on entry, execute_end_combat
    does not save on exit, so a restart resumed a fight that had ended.

    A fresh database reloads to exploration anyway, so the entry has to be
    persisted the way combat/loop.py persists it, or this test passes on the
    default and proves nothing."""
    db, tracker = await _started()
    await tracker.set_combat_mode(in_combat=True, turn_order=["tok1"])
    await tracker.save()                      # combat/loop.py:194
    assert (await _reloaded(db)).combat.in_combat is True

    await tracker.set_combat_mode(in_combat=False)   # executors.py:617, no save

    state = await _reloaded(db)
    assert state.combat.in_combat is False
    assert state.mode == GameMode.EXPLORATION
    assert state.combat.turn_order == []


@pytest.mark.asyncio
async def test_a_campaign_change_survives_a_restart():
    db, tracker = await _started()
    await tracker.set_campaign("Oakhaven")

    assert (await _reloaded(db)).campaign == "Oakhaven"


@pytest.mark.asyncio
async def test_the_session_number_survives_a_restart():
    db, tracker = await _started()
    await tracker.increment_session()
    await tracker.increment_session()

    assert (await _reloaded(db)).session_number == 3


@pytest.mark.asyncio
async def test_the_last_event_survives_a_restart():
    db, tracker = await _started()
    await tracker.record_event("The party burned the bone key.")

    assert (await _reloaded(db)).last_event == "The party burned the bone key."


@pytest.mark.asyncio
async def test_a_combat_round_survives_a_restart():
    db, tracker = await _started()
    await tracker.update_combat(in_combat=True, round_num=3, turn=2, turn_order=["a", "b"])

    state = await _reloaded(db)
    assert state.combat.round == 3
    assert state.combat.turn == 2


@pytest.mark.asyncio
async def test_the_mode_survives_a_restart():
    db, tracker = await _started()
    await tracker.set_mode(GameMode.COMBAT)

    assert (await _reloaded(db)).mode == GameMode.COMBAT


# ── what must not change ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reset_clears_the_persisted_state_too():
    db, tracker = await _started()
    await tracker.set_scene("The Sunken Chapel")
    await tracker.set_combat_mode(in_combat=True)

    await tracker.reset(campaign="New Campaign")

    state = await _reloaded(db)
    assert state.current_scene == ""
    assert state.combat.in_combat is False
    assert state.campaign == "New Campaign"


@pytest.mark.asyncio
async def test_a_database_that_refuses_the_write_does_not_lose_the_live_state():
    """The engine keeps playing on a bad disk; it just cannot restart onto it."""
    db, tracker = await _started()
    from unittest.mock import AsyncMock

    db.save_state = AsyncMock(side_effect=OSError("disk full"))

    with pytest.raises(OSError):
        await tracker.set_scene("The Sunken Chapel")

    assert tracker.state.current_scene == "The Sunken Chapel"


@pytest.mark.asyncio
async def test_clearing_stale_scene_data_survives_a_restart():
    """Its whole purpose is stopping the model from describing the previous
    scene. set_scene_data persists; the clear did not, so a restart brought
    the old scene's data straight back."""
    db, tracker = await _started()
    await tracker.set_scene_data({"walls": 12, "name": "The Sunken Chapel"})
    await tracker.set_encounter_context("Three ghouls behind the altar.")

    assert await tracker.clear_stale_scene_data() is True

    assert (await _reloaded(db)).scene_data == {}
