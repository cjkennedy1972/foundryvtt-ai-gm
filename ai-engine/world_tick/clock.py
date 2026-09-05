"""WorldTick — the off-session clock (CKP-101).

Advances NPC goals while nobody is logged in, on the cheap NPC model tier.
Everything it produces enters the world as a *canon proposal*, never as
canon, and only if the NPC named an in-world delivery path for it: a letter,
a rumour, a ledger note. A change with no delivery path is discarded — a
world that evolves only in SQLite is an expensive no-op.

Bounds, all enforced here rather than intended:
  - a hard token budget for what this tick may spend, measured against the
    campaign's recorded LLM usage;
  - a cap on NPCs ticked per simulated day;
  - deterministic ordering (sorted by npc_id), so a replay reproduces the
    same world;
  - NPCs the recent story has touched are frozen, so the tick cannot
    invalidate a player plan already in motion.
"""

import logging
from typing import Any, Dict, List, Optional

from events.store import EventStore
from events.types import TIME_ADVANCED
from npc.agent import NPCAgent
from npc.memory import NPCMemory
from npc.registry import NPCRegistry
from llm.router import ModelRouter
from persistence.db import Database
from referee.agent import RefereeAgent

logger = logging.getLogger(__name__)

WORLD_SIMULATION = "world_simulation"

# One simulated day is 24h of game time, the unit the token budget is stated in.
_SECONDS_PER_DAY = 86400

# How far back "the recent story" reaches when deciding which NPCs are frozen.
_RECENT_EVENT_WINDOW = 100


class WorldTick:
    """A bounded off-session advance of NPC and world state."""

    def __init__(
        self,
        db: Database,
        npc_registry: NPCRegistry,
        model_router: ModelRouter,
        referee: RefereeAgent,
        memory: NPCMemory,
        event_store: EventStore,
        npc_cap_per_day: int = 5,
        token_budget_per_day: int = 50_000,
        max_days_per_tick: int = 7,
    ):
        self.db = db
        self.npc_registry = npc_registry
        self.model_router = model_router
        self.referee = referee
        self.memory = memory
        self.event_store = event_store
        self.npc_cap_per_day = npc_cap_per_day
        self.token_budget_per_day = token_budget_per_day
        self.max_days_per_tick = max_days_per_tick

    async def tick(self, campaign: str, days: int = 1) -> Dict[str, Any]:
        """Advance *campaign* by *days* simulated days.

        Returns a summary: the days actually simulated, the NPCs ticked, the
        canon proposals raised, the changes discarded for lack of a delivery
        path, and the tokens spent.
        """
        days = max(0, min(days, self.max_days_per_tick))
        session_id = await self._resolve_session(campaign)
        summary = {
            "campaign": campaign,
            "session_id": session_id,
            "days_simulated": 0,
            "npcs_ticked": 0,
            "proposals": 0,
            "discarded_no_delivery_path": 0,
            "tokens_spent": 0,
            "stopped_reason": None,
        }
        if not session_id:
            summary["stopped_reason"] = "no_session"
            logger.warning(f"World tick for '{campaign}' skipped: campaign has no session history")
            return summary
        if not days:
            return summary

        spent_at_start = await self._tokens_spent(campaign)
        budget = self.token_budget_per_day * days

        for day_index in range(days):
            spent = await self._tokens_spent(campaign) - spent_at_start
            if spent >= budget:
                summary["stopped_reason"] = "token_budget"
                logger.warning(
                    f"World tick for '{campaign}' stopped at day {day_index}: "
                    f"{spent} tokens spent against a {budget} budget"
                )
                break
            await self._simulate_day(campaign, session_id, day_index, summary)
            summary["days_simulated"] += 1

        summary["tokens_spent"] = await self._tokens_spent(campaign) - spent_at_start
        logger.info(
            f"World tick for '{campaign}': {summary['days_simulated']} day(s), "
            f"{summary['npcs_ticked']} NPC(s), {summary['proposals']} proposal(s), "
            f"{summary['tokens_spent']} tokens"
        )
        return summary

    # ─── one simulated day ──────────────────────────────────────────────────

    async def _simulate_day(
        self, campaign: str, session_id: str, day_index: int, summary: Dict[str, Any]
    ) -> None:
        event = {
            "type": TIME_ADVANCED,
            "payload": {
                "duration_seconds": _SECONDS_PER_DAY,
                "off_session": True,
                "day_index": day_index,
            },
        }
        await self.event_store.append(
            session_id,
            TIME_ADVANCED,
            payload=event["payload"],
            description=f"Off-session world advance: day {day_index + 1}",
        )

        frozen = await self._frozen_npc_ids(session_id)
        ticked_today = 0

        # Deterministic ordering by npc_id — the same campaign state ticks the
        # same NPCs in the same order, so a replay reproduces the same world.
        for npc in sorted(self.npc_registry.list_npcs(), key=lambda n: n.npc_id):
            if ticked_today >= self.npc_cap_per_day:
                break
            if npc.npc_id in frozen:
                logger.debug(f"World tick: {npc.npc_name} frozen — the recent story still involves them")
                continue
            if not any(g.status == "active" and g.matches(event) for g in npc.goals):
                continue
            await self._tick_npc(npc, campaign, session_id, event, summary)
            ticked_today += 1
            summary["npcs_ticked"] += 1

    async def _tick_npc(
        self, npc, campaign: str, session_id: str, event: dict, summary: Dict[str, Any]
    ) -> None:
        agent = NPCAgent(npc, self.model_router, self.referee, self.memory)
        rulings = await agent.act(session_id, event)

        for ruling in rulings:
            action = ruling.action or {}
            # THE HARD GATE. A simulated change the player can never encounter
            # is not worth the tokens that made it, so it does not become a
            # proposal at all.
            delivery_path = action.get("delivery_path")
            if not delivery_path:
                summary["discarded_no_delivery_path"] += 1
                logger.info(
                    f"World tick: discarded a change for {npc.npc_name} — no named delivery path "
                    f"(action {action.get('type')})"
                )
                continue

            fact = self._describe(npc, action, delivery_path)
            await self.db.create_canon_proposal(
                session_id=session_id,
                campaign=campaign,
                fact=fact,
                confidence="medium",
                rationale=f"Off-session world tick for {npc.npc_name}",
            )
            await self.event_store.append(
                session_id,
                WORLD_SIMULATION,
                payload={
                    "npc_id": npc.npc_id,
                    "action_type": action.get("type"),
                    "delivery_path": delivery_path,
                },
                description=fact,
            )
            summary["proposals"] += 1

    # ─── bounds ─────────────────────────────────────────────────────────────

    async def _tokens_spent(self, campaign: str) -> int:
        """Tokens this campaign has burned, as the provider reported them.

        Read from the recorded llm_usage rather than estimated locally — the
        spend cap has to bind to what was actually billed.
        """
        usage = await self.db.get_llm_usage(campaign=campaign)
        return int(usage.get("total_tokens", 0))

    async def _frozen_npc_ids(self, session_id: str) -> set:
        """NPCs the recent story has already touched, which the tick must not move.

        The rule the spec asks for is "the tick may not invalidate an
        explicitly stated player plan": if the player said they are ambushing
        the caravan on Thursday, the caravan is there Thursday. What the event
        log actually records is which NPCs recent play named, so that is what
        this freezes — an NPC nobody has mentioned lately is free to act.

        ponytail: name-level protection, not intent parsing. If a player states
        a plan about an NPC nobody has interacted with yet, that NPC is not
        frozen; the upgrade is a structured player-plan event the tick can read.
        """
        events = await self.event_store.get_events(session_id, limit=_RECENT_EVENT_WINDOW)
        frozen = set()
        for event in events:
            if event.get("type") in (WORLD_SIMULATION, TIME_ADVANCED):
                continue  # the tick's own output must not freeze the next tick
            payload = event.get("payload") or {}
            for key in ("npc_id", "source_id", "target_id"):
                if payload.get(key):
                    frozen.add(payload[key])
            haystack = f"{event.get('description') or ''} {payload.get('params') or ''}".lower()
            for npc in self.npc_registry.list_npcs():
                if npc.npc_name and npc.npc_name.lower() in haystack:
                    frozen.add(npc.npc_id)
        return frozen

    # ─── helpers ────────────────────────────────────────────────────────────

    async def _resolve_session(self, campaign: str) -> Optional[str]:
        """The campaign's most recent session — the tick reads its history for
        NPC memory and appends its own events to it, so off-session activity
        stays on the same timeline as play instead of in a parallel log."""
        sessions = await self.db.get_campaign_session_ids(campaign)
        return sessions[0] if sessions else None

    @staticmethod
    def _describe(npc, action: Dict[str, Any], delivery_path: str) -> str:
        detail = action.get("text") or action.get("description") or action.get("type") or "acted"
        return f"{npc.npc_name}: {detail} (reaches the party via {delivery_path})"
