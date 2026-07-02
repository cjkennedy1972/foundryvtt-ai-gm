#!/usr/bin/env python3
"""
Regression test: initiative is rolled via start-encounter's rollAll param.

The relay has no "roll-initiative" message type — calling it errored
'Unknown message type: roll-initiative' and combat started with no initiative
order. start_encounter must pass rollAll, and execute_start_encounter must not
make a separate roll_initiative call.

Run:
    cd ai-engine && python -m pytest tests/test_initiative.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import executors


def test_start_encounter_passes_roll_all():
    foundry = SimpleNamespace(
        get_scene_tokens=AsyncMock(return_value=[{"id": "T1"}, {"id": "T2"}]),
        start_encounter=AsyncMock(return_value={"encounterId": "E1"}),
        # Must NOT be called — the unsupported message type.
        roll_initiative=AsyncMock(side_effect=AssertionError("roll_initiative must not be called")),
    )
    out = asyncio.run(executors.execute_start_encounter(
        foundry=foundry, auto_roll_initiative=True,
    ))
    assert out["success"] is True
    _, kwargs = foundry.start_encounter.await_args
    assert kwargs.get("roll_all") is True
    foundry.roll_initiative.assert_not_called()


def test_start_encounter_respects_disabled_initiative():
    foundry = SimpleNamespace(
        get_scene_tokens=AsyncMock(return_value=[{"id": "T1"}]),
        start_encounter=AsyncMock(return_value={}),
        roll_initiative=AsyncMock(),
    )
    asyncio.run(executors.execute_start_encounter(
        token_ids=["T1"], foundry=foundry, auto_roll_initiative=False,
    ))
    _, kwargs = foundry.start_encounter.await_args
    assert kwargs.get("roll_all") is False


def test_fetch_initiative_order_reads_result_envelope():
    """Regression: the execute-js script must `return` and the reply value is
    under the top-level "result" key — the old code did neither, so the combat
    loop always fell back to a random shuffle instead of Foundry's initiative."""
    from combat.loop import CombatLoop

    loop = CombatLoop.__new__(CombatLoop)  # skip __init__; only foundry is used
    loop.foundry = SimpleNamespace(
        execute_js=AsyncMock(return_value={
            "type": "execute-js-result", "result": ["T2", "T1"],
        }),
    )
    order = asyncio.run(CombatLoop._fetch_initiative_order(loop))
    assert order == ["T2", "T1"]
    script = loop.foundry.execute_js.await_args.args[0]
    assert "return" in script  # module evals as async fn body — no return, no value


if __name__ == "__main__":
    test_start_encounter_passes_roll_all()
    print("PASS  start_encounter passes rollAll, no roll_initiative call")
    test_start_encounter_respects_disabled_initiative()
    print("PASS  start_encounter respects disabled initiative")
    test_fetch_initiative_order_reads_result_envelope()
    print("PASS  _fetch_initiative_order unwraps the result envelope")
    print("All initiative tests passed.")
