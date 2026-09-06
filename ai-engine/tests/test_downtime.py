#!/usr/bin/env python3
"""Tests for downtime.resolver.DowntimeResolver — between-session player turns.

CKP-102's two acceptance criteria are what these hold: a downtime turn
resolves with no live session and no Foundry connection, and the outcome is
referenced in-world at the next session rather than only recorded. The rest
cover the boundaries that make those two safe — no session history, a failed
model call, and the rule that an outcome is delivered exactly once.

Run:
    cd ai-engine && python -m pytest tests/test_downtime.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from downtime.resolver import DowntimeResolver
from events.store import EventStore
from events.types import PLAYER_DOWNTIME_RESOLVED
from llm.router import ModelRouter
from persistence.db import Database
from referee.agent import RefereeAgent

CAMPAIGN = "greenrest"
SESSION = "s1"
ACTION = "my ranger spends the week tracking the cult"
OUTCOME = "You find their sign twice and lose it twice; the third trail ends at a mill."


def _llm(actions=None, error=None):
    llm = MagicMock()
    llm.usage_context = (None, "")
    llm.set_usage_context = MagicMock()
    if error is not None:
        llm.generate = AsyncMock(side_effect=error)
    else:
        llm.generate = AsyncMock(return_value={"actions": list(actions or [])})
    return llm


async def _fixture(actions=None, error=None, with_session=True):
    """A resolver over an in-memory campaign. Returns (resolver, db, llm)."""
    db = Database(":memory:")
    await db.init()
    if with_session:
        await db.create_session(SESSION, campaign=CAMPAIGN)
        # A downtime turn happens with nothing connected — the session that
        # anchors it is over.
        await db.close_session(SESSION)

    llm = _llm(actions, error)
    store = EventStore(db)
    resolver = DowntimeResolver(db, ModelRouter(llm), RefereeAgent(), store)
    return resolver, db, llm


def test_resolves_with_no_live_session_and_no_foundry():
    """First acceptance criterion. The RefereeAgent here has no FoundryClient,
    and the campaign's only session is closed."""
    async def run():
        resolver, db, _ = await _fixture([{"type": "narrate", "text": OUTCOME}])

        receipt = await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        assert receipt["resolved"] is True
        assert receipt["stopped_reason"] is None
        assert await db.get_active_session() is None

        events = await EventStore(db).get_events(CAMPAIGN, session_id=SESSION)
        logged = [e for e in events if e["type"] == PLAYER_DOWNTIME_RESOLVED]
        assert len(logged) == 1
        assert logged[0]["payload"]["outcome"] == OUTCOME
        await db.close()

    asyncio.run(run())


def test_receipt_withholds_the_outcome_from_the_submitter():
    """The operator is also a player: the receipt says it happened, not what."""
    async def run():
        resolver, db, _ = await _fixture([{"type": "narrate", "text": OUTCOME}])

        receipt = await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        assert OUTCOME not in str(receipt)
        await db.close()

    asyncio.run(run())


def test_pending_returns_the_outcome_until_it_is_narrated():
    """Second acceptance criterion, the delivery half: the outcome stays
    pending until something actually says it out loud, then never repeats."""
    async def run():
        resolver, db, _ = await _fixture([{"type": "narrate", "text": OUTCOME}])
        receipt = await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        pending = await resolver.pending(CAMPAIGN)
        assert [p["outcome"] for p in pending] == [OUTCOME]

        # The next session opens and the GM narrates it.
        await db.create_session("s2", campaign=CAMPAIGN)
        await resolver.mark_narrated("s2", CAMPAIGN, [receipt["event_id"]])

        assert await resolver.pending(CAMPAIGN) == []
        await db.close()

    asyncio.run(run())


def test_outcome_is_narrated_in_world_at_the_next_session_start():
    """Second acceptance criterion, end to end through GameLoop: the outcome
    reaches the table through the NarrativeSink, and only once."""
    async def run():
        resolver, db, _ = await _fixture([{"type": "narrate", "text": OUTCOME}])
        await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        loop = MagicMock()
        loop._downtime = resolver
        loop.narrative_sink = MagicMock()
        loop.narrative_sink.narration = AsyncMock()

        from foundry.chat_listener import GameLoop
        await db.create_session("s2", campaign=CAMPAIGN)
        await GameLoop._narrate_pending_downtime(loop, "s2", CAMPAIGN)

        spoken = " ".join(
            call.args[0] for call in loop.narrative_sink.narration.call_args_list
        )
        assert OUTCOME in spoken
        assert "Ranger" in spoken

        # A second session start must not replay what the table already heard.
        loop.narrative_sink.narration.reset_mock()
        await db.create_session("s3", campaign=CAMPAIGN)
        await GameLoop._narrate_pending_downtime(loop, "s3", CAMPAIGN)
        assert loop.narrative_sink.narration.call_args_list == []
        await db.close()

    asyncio.run(run())


def test_campaign_with_no_session_history_is_refused():
    async def run():
        resolver, db, llm = await _fixture(
            [{"type": "narrate", "text": OUTCOME}], with_session=False
        )

        receipt = await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        assert receipt["resolved"] is False
        assert receipt["stopped_reason"] == "no_session"
        # Nothing may be spent on a turn that has nowhere to land.
        llm.generate.assert_not_called()
        await db.close()

    asyncio.run(run())


def test_a_failed_model_call_records_nothing():
    async def run():
        resolver, db, _ = await _fixture(error=RuntimeError("provider down"))

        receipt = await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        assert receipt["stopped_reason"] == "llm_error"
        assert await resolver.pending(CAMPAIGN) == []
        await db.close()

    asyncio.run(run())


def test_an_unnarratable_outcome_records_nothing():
    """A model reply with no prose is not a downtime outcome — recording it
    would leave a pending item the GM can never narrate."""
    async def run():
        resolver, db, _ = await _fixture([{"type": "skill_check", "dc": 15}])

        receipt = await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        assert receipt["stopped_reason"] == "no_outcome"
        assert await resolver.pending(CAMPAIGN) == []
        await db.close()

    asyncio.run(run())


def test_the_turn_is_charged_to_the_session_it_lands_on_then_restored():
    """The spend cap in llm.usage.TokenUsage only binds when a session is set,
    so an unattended turn has to arm it — and put the previous context back."""
    async def run():
        resolver, db, llm = await _fixture([{"type": "narrate", "text": OUTCOME}])
        llm.usage_context = ("live-session", "other-campaign")

        await resolver.resolve(CAMPAIGN, "Ranger", ACTION)

        calls = [call.args for call in llm.set_usage_context.call_args_list]
        assert calls[0] == (SESSION, CAMPAIGN)
        assert calls[-1] == ("live-session", "other-campaign")
        await db.close()

    asyncio.run(run())


def test_empty_action_is_refused_before_any_spend():
    async def run():
        resolver, db, llm = await _fixture([{"type": "narrate", "text": OUTCOME}])

        receipt = await resolver.resolve(CAMPAIGN, "Ranger", "   ")

        assert receipt["stopped_reason"] == "empty_action"
        llm.generate.assert_not_called()
        await db.close()

    asyncio.run(run())


# ─── admin panel (api/routes/downtime.py) ───────────────────────────────────
#
# Direct calls against a mocked AppState, the convention this codebase uses
# for route tests (see test_canon_routes.py). chat_listener is None on
# purpose: that is the admin-panel case this feature exists for — a turn
# submitted with no game loop and nothing connected.


def _state(db, llm):
    from types import SimpleNamespace
    return SimpleNamespace(db=db, llm_manager=llm, chat_listener=None)


def test_admin_panel_can_submit_a_downtime_turn():
    async def run():
        from api.routes.downtime import DowntimeTurn, submit_downtime_turn

        _, db, llm = await _fixture([{"type": "narrate", "text": OUTCOME}])
        turn = DowntimeTurn(player="Ranger", action=ACTION, campaign=CAMPAIGN)

        response = await submit_downtime_turn(turn, _state(db, llm))

        assert response["status"] == "ok"
        assert response["resolved"] is True
        # The panel is the operator's screen: it may confirm, not reveal.
        assert OUTCOME not in str(response)
        await db.close()

    asyncio.run(run())


def test_admin_panel_lists_pending_turns_without_their_outcomes():
    async def run():
        from api.routes.downtime import DowntimeTurn, list_pending_downtime, submit_downtime_turn

        _, db, llm = await _fixture([{"type": "narrate", "text": OUTCOME}])
        await submit_downtime_turn(
            DowntimeTurn(player="Ranger", action=ACTION, campaign=CAMPAIGN), _state(db, llm)
        )

        response = await list_pending_downtime(CAMPAIGN, _state(db, llm))

        assert response["pending"] == [{"player": "Ranger", "action": ACTION}]
        assert OUTCOME not in str(response)
        await db.close()

    asyncio.run(run())
