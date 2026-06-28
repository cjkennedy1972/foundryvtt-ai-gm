#!/usr/bin/env python3
"""
Regression test: enemies the AI rolls for but never places get auto-placed.

Live play: the AI rolled for 'Skeleton Archer' and 'Revenant' (world actors)
but only ever place_token'd the PC, so the foes never appeared on the map. The
reconciler must place each rolled-for world actor that has no token on the
scene, using its prototype disposition, and must NOT touch actors already on
the map (e.g. the PC).

Run:
    cd ai-engine && python -m pytest tests/test_combatant_autoplace.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import ChatListener


def _listener(scene_tokens, actors, dispositions):
    foundry = AsyncMock()
    foundry._ai_name = None
    foundry.get_actors = AsyncMock(return_value=actors)
    foundry.get_scene_tokens = AsyncMock(return_value=scene_tokens)
    foundry.get_actor_dispositions = AsyncMock(return_value=dispositions)
    foundry.place_token = AsyncMock(return_value={"ok": True})
    from unittest.mock import MagicMock
    return ChatListener(
        foundry=foundry, llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    ), foundry


def test_rolled_enemy_is_placed_with_real_disposition():
    listener, foundry = _listener(
        scene_tokens=[{"name": "Beringar", "id": "T1", "disposition": 1, "x": 300, "y": 500}],
        actors=[
            {"name": "Beringar", "uuid": "Actor.PC"},
            {"name": "Skeleton", "uuid": "Actor.SK"},
        ],
        dispositions={"Skeleton": -1},
    )
    actions = [
        {"type": "roll", "formula": "1d20+4", "speaker": "Skeleton Archer"},
        {"type": "roll", "formula": "1d20", "speaker": "Beringar"},  # already on map
    ]
    asyncio.run(listener._place_referenced_combatants(actions))

    foundry.place_token.assert_awaited_once()
    args, kwargs = foundry.place_token.await_args
    assert args[0] == "Skeleton"               # the matched world actor
    assert kwargs.get("disposition") == -1     # real prototype disposition (hostile)


def test_no_rolls_no_placement():
    listener, foundry = _listener(
        scene_tokens=[], actors=[{"name": "Skeleton", "uuid": "Actor.SK"}], dispositions={},
    )
    asyncio.run(listener._place_referenced_combatants(
        [{"type": "narrate", "text": "skeletons everywhere"}]
    ))
    foundry.place_token.assert_not_called()


if __name__ == "__main__":
    test_rolled_enemy_is_placed_with_real_disposition()
    print("PASS  rolled-for enemy auto-placed with real disposition")
    test_no_rolls_no_placement()
    print("PASS  no roll speakers -> no placement")
    print("All combatant-autoplace tests passed.")
