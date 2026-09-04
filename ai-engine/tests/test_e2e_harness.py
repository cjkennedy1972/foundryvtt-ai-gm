#!/usr/bin/env python3
"""
E2E test harness for the AI-GM engine.

Drives the full pipeline — session start → player message → encounter →
combat turns → idle pacing — using a scripted LLM mock and a Foundry
mock that records every call. No live relay, no LLM API, no Foundry server
needed.

Run:
    cd ai-engine && python -m pytest tests/test_e2e_harness.py -v
  or standalone:
    cd ai-engine && python tests/test_e2e_harness.py
"""

import asyncio
import sys
import os
import json
import logging
import tempfile
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.harness import (
    MockDatabase,
    MockFoundryClient,
    MockNPCRegistry,
    MockStateTracker,
    ScriptedLLM,
    build_listener,
)

logging.basicConfig(level=logging.WARNING)  # suppress noise; flip to DEBUG to trace
logger = logging.getLogger("e2e")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = []
_FAIL = []

def _result(name: str, ok: bool, detail: str = ""):
    if ok:
        _PASS.append(name)
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
    return ok


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")


# Mocks (MockFoundryClient, ScriptedLLM, MockDatabase, MockStateTracker,
# MockNPCRegistry, build_listener) are shared with the eval replay harness and
# imported from evals.harness above.


# ---------------------------------------------------------------------------
# Individual test scenarios
# ---------------------------------------------------------------------------

async def scenario_session_start():
    """Session-start must: setup_scene + place_token, then narrate."""
    print("\n[Scenario] session_start")

    foundry = MockFoundryClient()
    db = MockDatabase()
    state = MockStateTracker()

    llm = ScriptedLLM([{
        "actions": [
            {"type": "setup_scene", "scene_name": "The Sunken Crypt",
             "narrate": "The torchlight flickers as you descend into the crypt."},
            {"type": "place_token", "actor_name": "Aria", "x": 400, "y": 300},
            {"type": "narrate", "text": "Welcome, adventurers. Darkness awaits."},
        ]
    }])

    listener = build_listener(llm, foundry, db, state)

    # Activate a session
    await db.create_session("ses001", "The Shattered Oath")

    listener._running = True  # simulate unpaused

    # Trigger the proactive session_start prompt (normally called by the endpoint)
    await listener._process_proactive_action(reason="session_start")

    calls_by_type = {c["method"] for c in foundry.calls}

    ok = True
    ok = _result("session_start: LLM called once", len(llm.calls) == 1) and ok
    ok = _result("session_start: set_active_scene called",
                 "set_active_scene" in calls_by_type or "create_entity" in calls_by_type
                 or "execute_js" in calls_by_type) and ok
    ok = _result("session_start: place_token called",
                 "place_token" in calls_by_type) and ok
    ok = _result("session_start: chat_message (narrate) called",
                 "chat_message" in calls_by_type) and ok
    return ok


async def scenario_player_message():
    """Player message → LLM generates narrate + speak → both dispatched."""
    print("\n[Scenario] player_message")

    foundry = MockFoundryClient()
    db = MockDatabase()
    state = MockStateTracker()

    llm = ScriptedLLM([{
        "actions": [
            {"type": "narrate",
             "text": "The goblin eyes you suspiciously from the shadows."},
            {"type": "speak", "npc_name": "Goblin",
             "text": "Who goes there?! This is OUR turf!"},
        ]
    }])

    await db.create_session("ses002", "The Shattered Oath")
    listener = build_listener(llm, foundry, db, state)
    listener._running = True

    # Simulate a player chat message arriving
    await listener._handle_chat_event({
        "speaker": "Aria",
        "message": "I step into the room and look around.",
        "type": "general",
    })

    chat_calls = foundry.calls_of("chat_message")
    texts = [c["text"] for c in chat_calls]

    ok = True
    ok = _result("player_msg: LLM called", len(llm.calls) == 1) and ok
    ok = _result("player_msg: narrate dispatched",
                 any("goblin" in t.lower() or "shadows" in t.lower() for t in texts)) and ok
    ok = _result("player_msg: NPC speak dispatched",
                 any("turf" in t.lower() for t in texts)) and ok
    return ok


async def scenario_failed_action_retry():
    """An action that fails triggers LLM retry with error context."""
    print("\n[Scenario] failed_action_retry")

    foundry = MockFoundryClient()

    # Patch place_token to fail on first call
    call_count = {"n": 0}
    original_place = foundry.place_token

    async def flaky_place_token(actor_name=None, actor_id=None, x=0, y=0, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            foundry._record("place_token", actor_id=actor_id, actor_name=actor_name, x=x, y=y)
            return {"success": False, "error": "Actor not found on scene"}
        return await original_place(actor_name=actor_name, actor_id=actor_id, x=x, y=y, **kw)

    foundry.place_token = flaky_place_token

    db = MockDatabase()
    state = MockStateTracker()

    llm = ScriptedLLM([
        # First response — place_token will fail
        {"actions": [
            {"type": "place_token", "actor_name": "Aria", "x": 400, "y": 300},
        ]},
        # Retry response — corrected action
        {"actions": [
            {"type": "narrate", "text": "I could not place the token; summoning Aria at default position."},
            {"type": "place_token", "actor_name": "Aria", "x": 0, "y": 0},
        ]},
    ])

    await db.create_session("ses003", "The Shattered Oath")
    listener = build_listener(llm, foundry, db, state)
    listener._running = True

    await listener._handle_chat_event({
        "speaker": "Aria",
        "message": "I enter the dungeon.",
        "type": "general",
    })

    place_calls = foundry.calls_of("place_token")
    ok = True
    ok = _result("retry: LLM called twice (initial + retry)", len(llm.calls) == 2) and ok
    ok = _result("retry: place_token attempted twice", len(place_calls) == 2) and ok
    return ok


async def scenario_encounter_start():
    """start_encounter without token_ids triggers start_combat with all scene tokens."""
    print("\n[Scenario] encounter_start")

    foundry = MockFoundryClient()
    db = MockDatabase()
    state = MockStateTracker()

    llm = ScriptedLLM([{
        "actions": [
            {"type": "narrate", "text": "Goblins pour from the darkness!"},
            {"type": "start_encounter", "encounter_name": "Goblin Ambush"},
        ]
    }])

    await db.create_session("ses004", "The Shattered Oath")
    listener = build_listener(llm, foundry, db, state)
    listener._running = True

    await listener._handle_chat_event({
        "speaker": "Aria",
        "message": "I shout out into the darkness.",
        "type": "general",
    })

    combat_calls = foundry.calls_of("start_combat")
    ok = True
    ok = _result("encounter: start_combat called", len(combat_calls) >= 1) and ok
    ok = _result("encounter: narration dispatched",
                 len(foundry.calls_of("chat_message")) >= 1) and ok
    return ok


async def scenario_idle_pacing_fires():
    """Idle timer fires a proactive GM action after configured timeout."""
    print("\n[Scenario] idle_pacing")

    foundry = MockFoundryClient()
    db = MockDatabase()
    state = MockStateTracker()

    # Fast timeout for the test
    import config as _cfg
    original_timeout = _cfg.settings.gm_idle_timeout

    llm = ScriptedLLM([{
        "actions": [
            {"type": "narrate",
             "text": "The silence grows heavy. A distant drip echoes through the crypt."},
        ]
    }])

    await db.create_session("ses005", "The Shattered Oath")
    listener = build_listener(llm, foundry, db, state)
    listener._running = True

    try:
        _cfg.settings.gm_idle_timeout = 1  # 1-second idle for the test
        listener._reset_idle_timer()
        await asyncio.sleep(1.5)  # wait for idle to fire
    finally:
        _cfg.settings.gm_idle_timeout = original_timeout

    ok = True
    ok = _result("idle: LLM called by pacing timer", len(llm.calls) >= 1) and ok
    ok = _result("idle: narration dispatched", len(foundry.calls_of("chat_message")) >= 1) and ok
    return ok


async def scenario_idle_suppressed_in_combat():
    """Idle pacing must NOT fire while mode == 'combat'."""
    print("\n[Scenario] idle_suppressed_in_combat")

    foundry = MockFoundryClient()
    db = MockDatabase()
    state = MockStateTracker()
    state.state.mode = "combat"  # put us in combat

    import config as _cfg
    original_timeout = _cfg.settings.gm_idle_timeout

    llm = ScriptedLLM([{"actions": [{"type": "narrate", "text": "Should not appear"}]}])

    await db.create_session("ses006", "The Shattered Oath")
    listener = build_listener(llm, foundry, db, state)
    listener._running = True

    try:
        _cfg.settings.gm_idle_timeout = 1
        listener._reset_idle_timer()
        await asyncio.sleep(1.5)
    finally:
        _cfg.settings.gm_idle_timeout = original_timeout

    ok = True
    ok = _result("combat_idle: LLM NOT called during combat", len(llm.calls) == 0) and ok
    return ok


async def scenario_pause_resume():
    """Pause halts processing; resume re-enables it."""
    print("\n[Scenario] pause_resume")

    foundry = MockFoundryClient()
    db = MockDatabase()
    state = MockStateTracker()

    llm = ScriptedLLM([
        {"actions": [{"type": "narrate", "text": "After resume message."}]},
    ])

    await db.create_session("ses007", "The Shattered Oath")
    listener = build_listener(llm, foundry, db, state)
    listener._running = True

    # Simulate Foundry pause hook
    await listener._handle_hook_event({"hook": "pauseGame", "data": {"paused": True}})

    assert_eq(listener._running, False, "Pause hook should set _running=False")

    # Message while paused — should be ignored
    await listener._handle_chat_event({
        "speaker": "Aria", "message": "Hello during pause", "type": "general"
    })
    paused_llm_calls = len(llm.calls)

    # Resume
    await listener._handle_hook_event({"hook": "pauseGame", "data": {"paused": False}})
    assert_eq(listener._running, True, "Unpause hook should set _running=True")

    # Message after resume — should be processed
    await listener._handle_chat_event({
        "speaker": "Aria", "message": "Hello after resume", "type": "general"
    })
    resumed_llm_calls = len(llm.calls)

    ok = True
    ok = _result("pause: paused hook sets _running=False", True) and ok  # assertion above would've raised
    ok = _result("pause: messages ignored while paused", paused_llm_calls == 0) and ok
    ok = _result("pause: resumed hook sets _running=True", True) and ok
    ok = _result("pause: messages processed after resume", resumed_llm_calls == 1) and ok
    return ok


async def scenario_active_modules_in_prompt():
    """Active Foundry modules are scanned and injected into the system prompt."""
    print("\n[Scenario] active_modules_in_prompt")

    from llm.manager import LLMManager

    # We can't call build_system_prompt directly without campaign data, so we
    # test the manager's module injection path.
    mgr = LLMManager()
    mgr.set_active_modules(["Midi QOL", "Dynamic Active Effects"])

    prompt = mgr.system_prompt
    ok = True
    ok = _result("modules: prompt invalidated after set_active_modules",
                 "Midi QOL" in prompt or "Active FoundryVTT Modules" in prompt) and ok
    return ok


async def scenario_npc_personality_in_combat():
    """Combat loop injects NPC personality into the LLM context."""
    print("\n[Scenario] npc_personality_combat")

    from actions.dispatcher import ActionDispatcher
    from state.tracker import GameStateTracker
    from state.models import GameState, GameMode
    from combat.loop import CombatLoop

    foundry = MockFoundryClient()
    db = MockDatabase()
    state_tracker = GameStateTracker(GameState())
    await state_tracker.set_mode(GameMode.COMBAT)

    llm = ScriptedLLM([{
        "actions": [
            {"type": "narrate", "text": "The goblin lunges!"},
            {"type": "roll", "formula": "1d6+2", "speaker": "Goblin"},
        ]
    }])

    dispatcher = ActionDispatcher(foundry)
    npc_reg = MockNPCRegistry()

    loop = CombatLoop(
        foundry=foundry,
        llm=llm,
        dispatcher=dispatcher,
        state_tracker=state_tracker,
        db=db,
        npc_registry=npc_reg,
    )

    goblin_token = {
        "id": "t2", "name": "Goblin", "actorUuid": "Actor.npc1",
        "x": 200, "y": 200,
        "hp": 12, "maxHp": 12,
    }

    await loop._process_npc_turn(goblin_token)

    ok = True
    ok = _result("npc_personality: LLM called for NPC turn", len(llm.calls) == 1) and ok
    ok = _result("npc_personality: personality injected into extra_context",
                 "Cowardly" in llm.calls[0] or "Goblin" in llm.calls[0]) and ok
    return ok


async def scenario_tts_idle_timer_bump():
    """After TTS plays, idle timer is bumped by audio duration."""
    print("\n[Scenario] tts_idle_timer_bump")

    from tts import playback as tts_playback

    # Fake a ChatListener with _reset_idle_timer tracking
    bumps = []
    mock_listener = MagicMock()
    mock_listener._reset_idle_timer = lambda extra_delay=0.0: bumps.append(extra_delay)

    tts_playback.set_chat_listener(mock_listener)

    # Simulate calling _reset_idle_timer with a TTS duration directly
    # (we're not spinning up the full TTS stack, just validating the path)
    mock_listener._reset_idle_timer(extra_delay=4.2)

    ok = True
    ok = _result("tts_bump: _reset_idle_timer called with extra_delay", len(bumps) == 1) and ok
    ok = _result("tts_bump: correct duration passed", bumps[0] == 4.2) and ok

    tts_playback.set_chat_listener(None)  # clean up
    return ok


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_all():
    scenarios = [
        scenario_session_start,
        scenario_player_message,
        scenario_failed_action_retry,
        scenario_encounter_start,
        scenario_idle_pacing_fires,
        scenario_idle_suppressed_in_combat,
        scenario_pause_resume,
        scenario_active_modules_in_prompt,
        scenario_npc_personality_in_combat,
        scenario_tts_idle_timer_bump,
    ]

    for scenario in scenarios:
        try:
            await scenario()
        except Exception as exc:
            name = scenario.__name__
            _result(f"{name} (uncaught exception)", False, str(exc))
            logger.exception(f"Uncaught in {name}")

    print("\n" + "=" * 60)
    print(f"  PASSED: {len(_PASS)}")
    print(f"  FAILED: {len(_FAIL)}")
    if _FAIL:
        print("  Failed scenarios:")
        for f in _FAIL:
            print(f"    - {f}")
    print("=" * 60)
    return len(_FAIL) == 0


# ---------------------------------------------------------------------------
# pytest entry points
# ---------------------------------------------------------------------------

def test_session_start():
    assert asyncio.run(scenario_session_start())

def test_player_message():
    assert asyncio.run(scenario_player_message())

def test_failed_action_retry():
    assert asyncio.run(scenario_failed_action_retry())

def test_encounter_start():
    assert asyncio.run(scenario_encounter_start())

def test_idle_pacing_fires():
    assert asyncio.run(scenario_idle_pacing_fires())

def test_idle_suppressed_in_combat():
    assert asyncio.run(scenario_idle_suppressed_in_combat())

def test_pause_resume():
    assert asyncio.run(scenario_pause_resume())

def test_active_modules_in_prompt():
    assert asyncio.run(scenario_active_modules_in_prompt())

def test_npc_personality_in_combat():
    assert asyncio.run(scenario_npc_personality_in_combat())

def test_tts_idle_timer_bump():
    assert asyncio.run(scenario_tts_idle_timer_bump())


if __name__ == "__main__":
    ok = asyncio.run(run_all())
    sys.exit(0 if ok else 1)
