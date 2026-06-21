"""Combat Loop — automatic NPC turn processing during combat encounters."""

import asyncio
import json
import logging
import random
from typing import Any, Callable, Dict, List, Optional

from actions.dispatcher import ActionDispatcher
from foundry.client import FoundryClient
from llm.manager import LLMManager
from persistence.db import Database
from state.tracker import GameStateTracker
from context.loader import CampaignLoader

logger = logging.getLogger(__name__)


class CombatLoop:
    """Manages automatic combat turn processing for NPC actors."""

    def __init__(
        self,
        foundry: FoundryClient,
        llm: LLMManager,
        dispatcher: ActionDispatcher,
        state_tracker: GameStateTracker,
        db: Database,
        campaign_loader: Optional[CampaignLoader] = None,
    ):
        self.foundry = foundry
        self.llm = llm
        self.dispatcher = dispatcher
        self.state_tracker = state_tracker
        self.db = db
        self.campaign_loader = campaign_loader
        self._running = False
        self._current_turn_index = 0
        self._turn_order: List[str] = []
        self._npc_tokens: List[Dict[str, Any]] = []
        self._pc_tokens: List[Dict[str, Any]] = []
        self._round_number = 1
        self._pending_ai_action: Optional[asyncio.Future] = None
        self._on_turn_start_callback: Optional[Callable] = None
        self._on_turn_complete_callback: Optional[Callable] = None
        # Combat lifecycle callbacks
        self._on_combat_start_callback: Optional[Callable] = None
        self._on_combat_end_callback: Optional[Callable] = None
        # PC input handling
        self._pc_turn_event: asyncio.Event = asyncio.Event()
        self._on_turn_advance: Optional[Callable] = None

    async def start_combat_loop(self, token_data: List[Dict[str, Any]]):
        """Start the combat loop with the given token data."""
        self._running = True
        self._round_number = 1
        self._current_turn_index = 0

        # Parse token data into NPCs and PCs
        self._npc_tokens = []
        self._pc_tokens = []
        for token in token_data:
            disp = token.get("disposition", 1)
            if disp >= 0:  # Friendlies ( PCs and allies )
                self._pc_tokens.append(token)
            else:  # Hostiles ( enemies )
                self._npc_tokens.append(token)

        # Build turn order: PC tokens first, then NPCs
        self._turn_order = [t["id"] for t in self._pc_tokens] + [t["id"] for t in self._npc_tokens]
        # Shuffle for initiative (could be randomized based on dex later)
        random.shuffle(self._turn_order)

        # Snapshot state before combat begins (enables rollback if combat goes wrong)
        await self.state_tracker.save_combat_snapshot(tokens=token_data)

        # Update state tracker
        await self.state_tracker.update_combat(
            in_combat=True,
            round_num=1,
            turn=0,
            turn_order=self._turn_order
        )
        await self.state_tracker.set_mode("combat")
        await self.state_tracker.save()

        logger.info(
            f"[Combat] Started round 1. "
            f"PCs: {len(self._pc_tokens)}, NPCs: {len(self._npc_tokens)}"
        )

        # Notify admin panel
        if self._on_turn_start_callback:
            await self._on_turn_start_callback({
                "type": "combat_started",
                "round": 1,
                "turn_order": self._turn_order,
                "pc_count": len(self._pc_tokens),
                "npc_count": len(self._npc_tokens)
            })

        # Notify reinforcement manager
        if self._on_combat_start_callback:
            await self._on_combat_start_callback(token_data)

        # Process turns
        await self._process_turns()

    async def _process_turns(self):
        """Loop through all turns in the current round."""
        while self._running and len(self._turn_order) > 0:
            # Check if current turn token still exists
            current_token_id = self._turn_order[self._current_turn_index % len(self._turn_order)]
            token = None
            for t in self._npc_tokens + self._pc_tokens:
                if t["id"] == current_token_id:
                    token = t
                    break

            if not token:
                logger.warning(f"[Combat] Token {current_token_id} not found, removing from turn order")
                self._turn_order.remove(current_token_id)
                if not self._turn_order:
                    await self._end_combat()
                    break
                self._current_turn_index %= len(self._turn_order)
                continue

            is_npc = token in self._npc_tokens
            actor_name = token.get("name", "Unknown")

            logger.info(f"[Combat] Round {self._round_number}, Turn {self._current_turn_index + 1}: {actor_name} ({'NPC' if is_npc else 'PC'})")

            # Notify admin panel
            if self._on_turn_start_callback:
                await self._on_turn_start_callback({
                    "type": "turn_started",
                    "round": self._round_number,
                    "turn": self._current_turn_index + 1,
                    "actor": actor_name,
                    "is_npc": is_npc
                })

            if is_npc:
                await self._process_npc_turn(token)
            else:
                # PC turn — wait for player input via chat listener
                await self._wait_for_pc_input(token)
                # After player input returns, the loop naturally
                # advances to the next turn below.

            # Advance to next turn
            self._current_turn_index += 1
            await self.state_tracker.update_combat(
                in_combat=True,
                round_num=self._round_number,
                turn=self._current_turn_index,
                turn_order=self._turn_order
            )
            await self.state_tracker.save()

            # Check if round is complete
            if self._current_turn_index >= len(self._turn_order):
                self._current_turn_index = 0
                self._round_number += 1
                logger.info(f"[Combat] Started round {self._round_number}")

                if self._on_turn_start_callback:
                    await self._on_turn_start_callback({
                        "type": "round_started",
                        "round": self._round_number
                    })

                # Check combat end conditions
                if await self._check_combat_end():
                    await self._end_combat()
                    break

    async def _process_npc_turn(self, token: Dict[str, Any]):
        """Process an NPC's turn — LLM decides their action."""
        actor_name = token.get("name", "Unknown")
        actor_uuid = token.get("actorUuid", "")

        # Get scene tokens for positioning info
        scene_tokens = await self.foundry.get_scene_tokens()
        scene_info = json.dumps(scene_tokens, indent=None)

        # Build combat context
        combat_context = f"""
## COMBAT ROUND {self._round_number}
**Current Turn:** {self._current_turn_index + 1}/{len(self._turn_order)}
**Your Token:** {actor_name} (ID: {token['id']})

## ALL COMBATANTS
{self._build_combatant_list()}

## YOUR POSITION
x: {token.get('x', 0)}, y: {token.get('y', 0)}

## AVAILABLE ACTIONS
You may issue up to 2-3 actions for this turn. Use:
- `roll` for attacks, skills, ability checks
- `move_token` to reposition
- `narrate` for descriptive actions
- `update_hp` if you damage yourself (for realism)
- `play_sound` for dramatic effects

## RULES
1. Always respond with valid JSON containing an "actions" array
2. Keep narration 2-3 sentences maximum
3. Be decisive — pick ONE target per attack action
4. Use cover/positioning strategically
5. If HP is low, consider retreating or using defensive abilities
"""

        from config import settings as _settings
        _raw_timeout = getattr(_settings, "llm_combat_timeout", 60)
        llm_timeout = _raw_timeout if _raw_timeout > 0 else None

        try:
            # Ask LLM to decide NPC's action — with timeout to prevent deadlock
            result = await asyncio.wait_for(
                self.llm.generate(
                    user_message=f"{actor_name}'s turn. Decide their action based on the combat context.",
                    game_state_summary=self.state_tracker.get_snapshot(),
                    extra_context=combat_context
                ),
                timeout=llm_timeout,
            )

            actions = result.get("actions", [])
            if not actions:
                logger.warning(f"[Combat] NPC {actor_name} returned no actions")
                await self.foundry.chat_message(
                    f"**{actor_name} hesitates, taking no action this turn.**",
                    speaker="GM"
                )
                return

            # Execute NPC actions
            results = await self.dispatcher.execute_batch(actions)
            logger.info(f"[Combat] NPC {actor_name} took {len(actions)} actions")

            # Notify admin panel
            if self._on_turn_complete_callback:
                await self._on_turn_complete_callback({
                    "type": "turn_complete",
                    "actor": actor_name,
                    "actions": results,
                    "round": self._round_number,
                    "turn": self._current_turn_index + 1
                })

        except asyncio.TimeoutError:
            logger.warning(f"[Combat] LLM timeout for {actor_name} after {llm_timeout}s — using generic behavior")
            fallback_actions = await self._generic_npc_behavior(token, combat_context)
            await self.dispatcher.execute_batch(fallback_actions)
            if self._on_turn_complete_callback:
                await self._on_turn_complete_callback({
                    "type": "turn_complete",
                    "actor": actor_name,
                    "actions": fallback_actions,
                    "round": self._round_number,
                    "turn": self._current_turn_index + 1
                })

        except Exception as e:
            logger.error(f"[Combat] Error processing NPC {actor_name} turn: {e}", exc_info=True)
            # Send error to chat and advance turn to prevent infinite loop
            await self.foundry.chat_message(
                f"[GM Error] Combat error: {str(e)}",
                speaker="GM"
            )
            # Mark turn as complete so the loop advances
            if self._on_turn_complete_callback:
                await self._on_turn_complete_callback({
                    "type": "turn_complete",
                    "actor": actor_name,
                    "actions": [{"action": "error", "error": str(e)}],
                    "round": self._round_number,
                    "turn": self._current_turn_index + 1
                })

    async def _generic_npc_behavior(self, token: Dict[str, Any], combat_context: str) -> List[dict]:
        """Fallback NPC behavior when LLM is unresponsive.

        Moves toward nearest PC and performs a basic attack. Safe, deterministic,
        never blocks combat.
        """
        actor_name = token.get("name", "Unknown")
        token_id = token.get("id", "")

        # Find nearest PC by position
        nearest_pc = None
        if self._pc_tokens:
            tx, ty = token.get("x", 0), token.get("y", 0)
            nearest_pc = min(
                self._pc_tokens,
                key=lambda p: abs(p.get("x", 0) - tx) + abs(p.get("y", 0) - ty),
            )

        actions = []
        if nearest_pc:
            pc_name = nearest_pc.get("name", "adventurer")
            actions.append({
                "action": "narrate",
                "text": f"{actor_name} moves toward {pc_name} and strikes!",
            })
            actions.append({
                "action": "roll",
                "dice": "1d20+4",
                "purpose": f"{actor_name} attacks {pc_name}",
                "target": nearest_pc.get("id", ""),
            })
        else:
            actions.append({
                "action": "narrate",
                "text": f"{actor_name} stands ready, waiting for an opportunity.",
            })

        return actions

    async def _wait_for_pc_input(self, token: Dict[str, Any]):
        """Wait for PC player input during their turn.

        Sets a pending PC input flag so the chat listener routes player
        messages through combat processing, then blocks until the chat
        listener fires _on_turn_advance after the player's action completes.
        """
        actor_name = token.get("name", "Unknown")
        token_id = token.get("id", "")
        await self.foundry.chat_message(
            f"⚔️ **Round {self._round_number}, Turn {self._current_turn_index + 1}:** {actor_name}'s turn. What do you do?",
            speaker="GM",
            whisper=[]
        )
        self._pc_turn_event.clear()
        logger.info(f"[Combat] Waiting for {actor_name}'s input...")
        # Block until chat listener fires the turn-advance callback
        await self._pc_turn_event.wait()
        logger.info(f"[Combat] {actor_name}'s input received, advancing...")

    async def _register_turn_advance(self, callback: Callable):
        """Register a callback that fires when a PC has acted in combat.

        The chat listener invokes this after executing a PC's response
        so the combat loop can advance to the next turn.
        """
        self._on_turn_advance = callback
        logger.info("[Combat] Turn advance callback registered")

    async def _check_combat_end(self) -> bool:
        """Check if combat should end (all NPCs defeated or all PCs defeated).

        Refreshes token positions from Foundry to get live HP values,
        then checks if one side is fully eliminated.  On failure, prunes
        stale tokens from the internal lists to prevent zombie combatants.
        """
        try:
            # Refresh token data from Foundry for live HP
            fresh_tokens = await self.foundry.get_scene_tokens()
            token_map = {t["id"]: t for t in fresh_tokens}

            # Filter to tokens still in the encounter
            alive_npc = []
            for token in self._npc_tokens:
                t = token_map.get(token["id"], token)
                hp = self._get_hp_from_token(t)
                if hp > 0:
                    alive_npc.append(t)

            alive_pc = []
            for token in self._pc_tokens:
                t = token_map.get(token["id"], token)
                hp = self._get_hp_from_token(t)
                if hp > 0:
                    alive_pc.append(t)

            # Capture whether each side had combatants BEFORE updating the
            # lists, otherwise the end conditions can never be true.
            had_npc = bool(self._npc_tokens)
            had_pc = bool(self._pc_tokens)

            # Update internal token lists
            self._npc_tokens = alive_npc
            self._pc_tokens = alive_pc

            if had_npc and not alive_npc:
                logger.info("[Combat] All NPCs defeated — combat ended")
                return True
            if had_pc and not alive_pc:
                logger.info("[Combat] All PCs defeated — combat ended")
                return True

        except Exception as e:
            logger.warning(f"[Combat] Could not check end condition: {e}. "
                           "Pruning stale tokens from lists.")
            # Prune any tokens that no longer exist in Foundry.
            # Fetch tokens once (O(1) RPC) instead of once per token.
            fresh = await self.foundry.get_scene_tokens()
            fresh_ids = {ft["id"] for ft in fresh}
            self._npc_tokens = [
                t for t in self._npc_tokens if t["id"] in fresh_ids
            ]
            self._pc_tokens = [
                t for t in self._pc_tokens if t["id"] in fresh_ids
            ]

        return False

    @staticmethod
    def _get_hp_from_token(token: Dict[str, Any]) -> int:
        """Extract current HP from a token, handling nested Foundry structures.

        Missing or unparseable HP defaults to 0 (defeated/dead), not 1.
        """
        data = token.get("data", {})
        if isinstance(data, dict):
            attrs = data.get("attributes", {})
            if isinstance(attrs, dict):
                hp = attrs.get("hp", {})
                # Only use nested HP if the dict actually has a "value" key
                # (otherwise it's an empty/placeholder dict — fall through to
                # the top-level fallback).
                if isinstance(hp, dict) and "value" in hp:
                    raw = hp["value"]
                    try:
                        return int(raw) if raw is not None else 0
                    except (ValueError, TypeError):
                        return 0
        # Fallback: top-level hp field
        hp = token.get("hp", token.get("currentHP"))
        if hp is None:
            return 0
        try:
            return int(hp)
        except (ValueError, TypeError):
            return 0

    async def _end_combat(self):
        """End the combat encounter."""
        self._running = False
        await self.state_tracker.update_combat(in_combat=False)
        await self.state_tracker.set_mode("exploration")
        await self.state_tracker.save()

        await self.foundry.end_encounter()
        await self.foundry.chat_message(
            "⚔️ **Combat ends!** The encounter is over.",
            speaker="GM"
        )
        logger.info("[Combat] Combat ended")
        self.state_tracker.clear_combat_snapshot()

        # Notify reinforcement manager
        if self._on_combat_end_callback:
            await self._on_combat_end_callback()

        if self._on_turn_complete_callback:
            await self._on_turn_complete_callback({
                "type": "combat_ended",
                "rounds": self._round_number
            })

    def _build_combatant_list(self) -> str:
        """Build a compact combatant list for context injection.

        Limits to 10 most relevant combatants (players first, then NPCs)
        to avoid context bloat in large encounters.
        """
        # Sort: players first, then NPCs; limit to prevent context overflow
        all_tokens = self._pc_tokens + self._npc_tokens
        max_combatants = 10
        selected = all_tokens[:max_combatants]

        lines = []
        for token in selected:
            disp = token.get("disposition", 1)
            side = "🟢 PC" if disp >= 0 else "🔴 NPC"
            lines.append(f"- [{side}] {token.get('name', 'Unknown')} at ({token.get('x', 0)}, {token.get('y', 0)})")
        return "\n".join(lines)

    async def stop(self):
        """Stop the combat loop."""
        self._running = False
        self._pc_turn_event.set()  # Unblock any waiting turn
        logger.info("[Combat] Combat loop stopped")

    def set_turn_start_callback(self, callback: Callable):
        """Set callback for turn start events."""
        self._on_turn_start_callback = callback

    def set_turn_complete_callback(self, callback: Callable):
        """Set callback for turn complete events."""
        self._on_turn_complete_callback = callback

    def set_combat_start_callback(self, callback: Callable):
        """Set callback for combat start events."""
        self._on_combat_start_callback = callback

    def set_combat_end_callback(self, callback: Callable):
        """Set callback for combat end events."""
        self._on_combat_end_callback = callback

    def advance_pc_turn(self):
        """Signal that a PC has completed their turn.

        Called by the chat listener after processing the player's input.
        Unblocks the combat loop's `_wait_for_pc_input` so it can
        advance to the next turn.
        """
        self._pc_turn_event.set()
        logger.info("[Combat] PC turn advanced by chat listener")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_round(self) -> int:
        return self._round_number

    @property
    def current_turn(self) -> int:
        return self._current_turn_index + 1

    @property
    def turn_order(self) -> List[str]:
        return self._turn_order
