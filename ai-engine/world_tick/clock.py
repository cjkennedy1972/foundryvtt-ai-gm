"""
The World Tick: an off-session simulation clock that advances NPC and world state.
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from npc.agent import NPCAgent
from npc.registry import NPCRegistry
from persistence.db import Database
from events.store import EventStore # Import EventStore
from context.canon import parse_canon_proposals
from llm.router import ModelRouter
from referee.agent import RefereeAgent
from npc.memory import NPCMemory

logger = logging.getLogger(__name__)

class WorldTick:
    """
    The 'Moat': a bounded real-time tick that advances NPC goals and world state
    when no player is logged in.
    """
    def __init__(
        self, 
        db: Database, 
        npc_registry: NPCRegistry, 
        model_router: ModelRouter, 
        referee: RefereeAgent,
        memory_provider: Any, # Should be a way to get NPCMemory for any NPC
        event_store: EventStore # Add EventStore
    ):
        self.db = db
        self.npc_registry = npc_registry
        self.model_router = model_router
        self.referee = referee
        self.memory_provider = memory_provider
        self.event_store = event_store # Store EventStore
        
        # Hard Bounds
        self.NPC_TICK_CAP_PER_CYCLE = 5
        self.TOKEN_BUDGET_PER_DAY = 50000
        self.MAX_SIM_DAYS_PER_TICK = 1

    async def tick(self, campaign_id: str, days_to_advance: int = 1):
        """
        Advances the world by N days.
        """
        if days_to_advance > self.MAX_SIM_DAYS_PER_TICK:
            days_to_advance = self.MAX_SIM_DAYS_PER_TICK

        logger.info(f"World Tick: Advancing campaign {campaign_id} by {days_to_advance} days")

        for day in range(days_to_advance):
            await self._simulate_day(campaign_id, day)

    async def _simulate_day(self, campaign_id: str, day_index: int):
        """
        Simulates a single day of off-session activity.
        """
        # 1. Check Token Budget
        current_usage = await self.db.get_llm_usage(campaign=campaign_id)
        if current_usage.get("total_tokens", 0) > self.TOKEN_BUDGET_PER_DAY:
            logger.warning(f"Token budget exceeded for campaign {campaign_id}. Skipping simulation.")
            return

        # 2. Avoid invalidating explicit player plans
        # Check for player intent in the event log that matches this day/time.
        # For now, we scan recent events for "plan" or "ambush" keywords and a day reference.
        # A more robust solution would involve structured player plans.
        recent_events = await self.event_store.get_events(session_id=f"world-tick-{campaign_id}", limit=100)
        player_plans = [e for e in recent_events if "plan" in e["description"].lower() or "ambush" in e["description"].lower()]
        
        # If significant player plans are detected, we might adjust the tick behavior or skip it.
        if player_plans:
            logger.info(f"Player plans detected for campaign {campaign_id}: {len(player_plans)} relevant events.")
            # TODO: Implement logic to process player plans and prevent contradictions

        # 3. Create the triggering event for the day
        tick_event = {
            "type": "world_tick",
            "payload": {
                "day_index": day_index,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "description": f"Off-session world advance: Day {day_index}",
                "active_player_plans": [p["description"] for p in player_plans] # Pass descriptions for now
            }
        }

        # 4. Deterministic NPC Selection
        # Use a stable sort on NPC IDs to ensure reproducibility
        all_npcs = self.npc_registry.list_npcs()
        sorted_npcs = sorted(all_npcs, key=lambda x: x.npc_id)
        
        ticked_count = 0
        for npc in sorted_npcs:
            if ticked_count >= self.NPC_TICK_CAP_PER_CYCLE:
                break

            # Check if NPC has goals matching the tick event
            # Note: Goal.matches is defined in npc/goals.py
            matching_goals = [g for g in npc.goals if g.matches(tick_event)]
            if not matching_goals:
                continue

            # Process the NPC action
            await self._process_npc_tick(npc, tick_event, campaign_id)
            ticked_count += 1

    async def _process_npc_tick(self, npc, event, campaign_id):
        """
        Ticks a single NPC and pushes results as canon proposals.
        """
        # Instantiate agent
        memory = self.memory_provider.get_memory_for_npc(npc.npc_id)
        agent = NPCAgent(npc, self.model_router, self.referee, memory)
        
        # Use a generic off-session session_id
        session_id = f"world-tick-{campaign_id}"
        
        # Act
        rulings = await agent.act(session_id, event)
        
        for ruling in rulings:
            # HARD GATE: Delivery Path Enforcement
            # Simulation output must be as canon proposals, and must have a delivery path.
            # For the purpose of this implementation, the 'Ruling' must contain 
            # a 'delivery_path' in its payload or it is discarded.
            delivery_path = ruling.payload.get("delivery_path")
            if not delivery_path:
                logger.warning(f"Discarding simulated change for {npc.npc_name}: No named delivery path.")
                continue

            # Construct the canon fact
            fact = f"{npc.npc_name} {ruling.description}. Delivered via {delivery_path}."
            
            # Push as Canon Proposal
            await self.db.create_canon_proposal(
                session_id=session_id,
                campaign=campaign_id,
                fact=fact,
                confidence="medium",
                rationale=f"Simulated off-session activity for NPC {npc.npc_name}",
                contradiction_note=None
            )
            
            # Record the event in the log for reproducibility/history
            await self.db.record_typed_event(
                session_id=session_id,
                event_type="world_simulation",
                payload={"npc_id": npc.npc_id, "ruling": ruling.description, "delivery_path": delivery_path},
                description=f"World Tick: {npc.npc_name} {ruling.description}"
            )
