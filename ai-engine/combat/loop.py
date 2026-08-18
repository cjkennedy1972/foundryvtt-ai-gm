"""Combat Loop — automatic NPC turn processing during combat encounters."""

import asyncio
import json
import logging
import random
from typing import Any, Callable, Dict, List, Optional

from actions.dispatcher import ActionDispatcher
from actions.executors import execute_death_save, get_death_save_status
from foundry.client import FoundryClient
from llm.manager import LLMManager
from persistence.db import Database
from state.tracker import GameStateTracker
from context.loader import CampaignLoader

logger = logging.getLogger(__name__)


def _limit_multiattack_actions(actions: List[Dict[str, Any]], attack_limit: int) -> List[Dict[str, Any]]:
    """Keep at most ``attack_limit`` attack actions in an NPC turn."""
    remaining = max(1, int(attack_limit))
    limited = []
    for action in actions:
        if action.get("type") == "attack_with_item":
            if remaining <= 0:
                continue
            remaining -= 1
        limited.append(action)
    return limited


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
        npc_registry=None,
        active_modules: Optional[Dict[str, Any]] = None,
    ):
        self.foundry = foundry
        self.llm = llm
        self.dispatcher = dispatcher
        self.state_tracker = state_tracker
        self.db = db
        self.campaign_loader = campaign_loader
        self.npc_registry = npc_registry
        self._running = False
        self._current_turn_index = 0
        self._turn_order: List[str] = []
        self._npc_tokens: List[Dict[str, Any]] = []
        self._pc_tokens: List[Dict[str, Any]] = []
        self._dead_pc_tokens: set[str] = set()  # Token IDs of PCs at 0 HP waiting for death save turn
        self._round_number = 1
        self._on_turn_start_callback: Optional[Callable] = None
        self._on_turn_complete_callback: Optional[Callable] = None
        # Combat lifecycle callbacks
        self._on_combat_start_callback: Optional[Callable] = None
        self._on_combat_end_callback: Optional[Callable] = None
        # PC input handling
        self._pc_turn_event: asyncio.Event = asyncio.Event()
        self._on_turn_advance: Optional[Callable] = None

        # Multiattack tracking: {actor_uuid: attack_count_used_this_turn}
        # Reset at the start of each NPC's turn
        self._attacks_used_this_turn: Dict[str, int] = {}

        # ── Module-aware combat configuration ──────────────────────────────
        self._active_modules = active_modules or {}
        self._has_midi_qol = "midi-qol" in self._active_modules
        self._has_dae = "dae" in self._active_modules
        self._has_autoanimations = "autoanimations" in self._active_modules
        logger.info(f"[Combat] Initialized with modules: midi-qol={self._has_midi_qol}, "
                    f"dae={self._has_dae}, autoanimations={self._has_autoanimations}")

    async def start_combat_loop(self, token_data: List[Dict[str, Any]]):
        """Start the combat loop with the given token data."""
        # Guard against double-starting: several callers (the /gm command, the
        # Foundry encounter-started event, and the REST endpoint) can race to
        # start combat. A second concurrent loop would corrupt shared turn state.
        if self._running:
            logger.warning("[Combat] start_combat_loop called while already running — ignoring")
            return

        # ── Detect active modules at combat start ─────────────────────────
        try:
            world_info = await self.foundry.get_world_info()
            mods = world_info.get("modules", [])
            self._active_modules = {
                m["id"]: {"title": m.get("title", m["id"]), "version": m.get("version", "")}
                for m in mods if m.get("active")
            }
            self._has_midi_qol = "midi-qol" in self._active_modules
            self._has_dae = "dae" in self._active_modules
            self._has_autoanimations = "autoanimations" in self._active_modules
            logger.info(f"[Combat] Combat start detected modules: {list(self._active_modules.keys())}")
        except Exception as e:
            logger.debug(f"[Combat] Could not detect modules at start: {e}")

        self._round_number = 1
        self._current_turn_index = 0

        # Parse token data into NPCs and PCs.
        # Disposition: 1=friendly, 0=neutral, -1=hostile. A token MUST have
        # explicit disposition to enter combat. Missing disposition is a safety issue.
        self._npc_tokens = []
        self._pc_tokens = []
        invalid_tokens = []

        for token in token_data:
            disp = token.get("disposition")
            token_id = token.get("id", "unknown")
            token_name = token.get("name", token_id)

            if disp is None:
                # Missing disposition — FAIL FAST. This is likely a misconfigured player token.
                invalid_tokens.append({
                    "id": token_id,
                    "name": token_name,
                    "reason": "disposition is undefined (check Foundry token disposition setting)"
                })
            elif disp >= 0:  # Explicit friendly/neutral → PC/ally
                self._pc_tokens.append(token)
            else:  # Hostile or explicitly unknown → AI-controlled
                self._npc_tokens.append(token)

        # FAIL FAST: If any tokens have undefined disposition, refuse to start combat
        if invalid_tokens:
            error_msg = "Combat cannot start due to misconfigured tokens:\n"
            for t in invalid_tokens:
                error_msg += f"  - {t['name']} ({t['id']}): {t['reason']}\n"
            error_msg += "\nIn Foundry, set disposition (friendly/neutral/hostile) for all combatants."
            logger.error(f"[Combat] {error_msg}")
            # Send error to chat so the human GM sees it
            try:
                await self._foundry.chat_message(f"❌ {error_msg}", speaker="GM")
            except Exception as chat_err:
                logger.debug(f"[Combat] Could not post error to chat: {chat_err}")
            raise ValueError(error_msg)

        # Build turn order: PC tokens first, then NPCs
        self._turn_order = [t["id"] for t in self._pc_tokens] + [t["id"] for t in self._npc_tokens]

        # Prefer Foundry's rolled initiative so the AI's turn order matches the
        # combat tracker players actually see; fall back to a shuffle.
        initiative_order = await self._fetch_initiative_order()
        if initiative_order:
            known = set(self._turn_order)
            ordered = [tid for tid in initiative_order if tid in known]
            # Append any combatants Foundry didn't return, preserving them.
            ordered += [tid for tid in self._turn_order if tid not in set(ordered)]
            self._turn_order = ordered
        else:
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

        # Only after all validation and setup succeeds: mark combat as running
        self._running = True

        logger.info(
            f"[Combat] Started round 1. "
            f"PCs: {len(self._pc_tokens)}, NPCs: {len(self._npc_tokens)}"
        )

        # ── Sync a real Foundry Combat document ────────────────────────────
        # Without this, this loop's turn order lived only in engine memory —
        # Foundry's own combat tracker (and anything skinning it, e.g.
        # Carousel Combat Tracker, Combat Booster) showed nothing during
        # AI-run combat. Best-effort: combat still runs via chat/dispatched
        # actions if this fails.
        await self._sync_foundry_combat()

        # Spotlight any boss combatant with a Bossbar health bar for this fight.
        await self._apply_bossbar()

        # ── Announce initiative to chat ────────────────────────────────────
        await self._announce_initiative()

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

    async def _sync_foundry_combat(self) -> None:
        """Create/update the active scene's Combat document to mirror
        self._turn_order, then set it to round 1 / turn 0.

        Best-effort — this loop's own turn state (self._turn_order,
        self._current_turn_index, self._round_number) is authoritative for
        AI decision-making regardless of whether this succeeds; a failure
        here only costs Foundry-side tracker visibility, not gameplay.
        """
        try:
            from foundry import scripts
            res = await self.foundry.execute_js(scripts.sync_combat_combatants(self._turn_order))
            result = res.get("result") if isinstance(res, dict) else None
            if not (isinstance(result, dict) and result.get("ok")):
                logger.warning(f"[Combat] Foundry Combat sync returned unexpected result: {result}")
                return
            await self.foundry.execute_js(scripts.set_combat_turn(self._round_number, self._current_turn_index))
        except Exception as e:
            logger.warning(f"[Combat] Foundry Combat sync failed: {e}")

    async def _apply_bossbar(self) -> None:
        """Show a Bossbar health bar for boss-flagged combatants on the active scene.

        Bosses are marked at deploy with the ``aigm.boss`` actor flag
        (campaign/modules/bossbar.py). Bossbar reads the scene flag
        ``bossbar.actors`` (each ``{uuid, style}``), so this collects boss
        combatants and writes that list. Best-effort — cosmetic only.
        """
        if "bossbar" not in self._active_modules:
            return
        js = (
            "const s=game.scenes.active; if(!s||!game.combat) return false;"
            "const seen=new Set(), list=[];"
            "for(const c of game.combat.combatants){const a=c.actor;"
            "if(a&&a.getFlag('aigm','boss')&&!seen.has(a.uuid)){seen.add(a.uuid);"
            "list.push({uuid:a.uuid, style:'default'});}}"
            "await s.setFlag('bossbar','actors',list); return list.length;"
        )
        try:
            await self.foundry.execute_js(js)
        except Exception as e:
            logger.debug(f"[Combat] Bossbar apply failed (non-fatal): {e}")

    async def _clear_bossbar(self) -> None:
        """Remove the Bossbar health bar(s) when combat ends."""
        if "bossbar" not in self._active_modules:
            return
        try:
            await self.foundry.execute_js(
                "const s=game.scenes.active; if(s) await s.unsetFlag('bossbar','actors'); return true;"
            )
        except Exception as e:
            logger.debug(f"[Combat] Bossbar clear failed (non-fatal): {e}")

    async def _sync_foundry_combat_turn(self) -> None:
        """Push this loop's current round/turn into Foundry's Combat document."""
        try:
            from foundry import scripts
            await self.foundry.execute_js(scripts.set_combat_turn(self._round_number, self._current_turn_index))
        except Exception as e:
            logger.debug(f"[Combat] Foundry Combat turn sync failed: {e}")

    async def _announce_initiative(self) -> None:
        """Announce the full initiative order to chat."""
        try:
            # Build a readable turn order message
            turn_lines = ["⚔️ **INITIATIVE ORDER**\n"]

            for i, token_id in enumerate(self._turn_order, 1):
                # Find token name
                token_name = "Unknown"
                for t in self._pc_tokens + self._npc_tokens:
                    if t.get("id") == token_id:
                        token_name = t.get("name", "Unknown")
                        is_pc = any(pc.get("id") == token_id for pc in self._pc_tokens)
                        emoji = "👤" if is_pc else "⚔️"
                        break

                current_marker = " ← **YOUR TURN**" if i == 1 else ""
                turn_lines.append(f"{i}. {emoji} {token_name}{current_marker}")

            initiative_message = "\n".join(turn_lines)
            await self.foundry.chat_message(
                initiative_message,
                speaker="GM",
                whisper=[]
            )
            logger.info("[Combat] Announced initiative order to chat")
        except Exception as e:
            logger.warning(f"[Combat] Could not announce initiative: {e}")

    async def _announce_current_turn(self, actor_name: str, is_npc: bool, turn_num: int, round_num: int) -> None:
        """Announce whose turn it is."""
        try:
            emoji = "⚔️" if is_npc else "👤"
            actor_type = "NPC" if is_npc else "PLAYER"

            message = f"\n---\n\n**Round {round_num}, Turn {turn_num}:** {emoji} {actor_name}'s Turn"

            # If it's a player's turn, add a call to action
            if not is_npc:
                message += "\n\n**What is your next action?**"

            await self.foundry.chat_message(
                message,
                speaker="GM",
                whisper=[]
            )
            logger.info(f"[Combat] Announced turn: {actor_name} (turn {turn_num})")
        except Exception as e:
            logger.warning(f"[Combat] Could not announce turn: {e}")

    def _get_module_features_summary(self) -> str:
        """Build a summary of active module features for combat context."""
        features = []

        if self._has_midi_qol:
            features.append("• MIDI QOL: Attacks auto-resolve. Specify targets and damage type.")

        if self._has_dae:
            features.append("• DAE: Active effects auto-apply/remove. Buffs and conditions tracked.")

        if self._has_autoanimations:
            features.append("• AutoAnimations: Spells/attacks have visual effects.")

        if features:
            return "\n".join(features)
        return "No automated combat modules active."

    async def _track_active_effects(self, token: Dict[str, Any]) -> None:
        """Track and log active effects on a token (if DAE module active)."""
        if not self._has_dae:
            return

        actor_uuid = token.get("actorUuid", "")
        actor_name = token.get("name", "Unknown")

        try:
            # Query token for active effects
            from foundry import scripts
            res = await self.foundry.execute_js(scripts.get_active_effects(actor_uuid))
            effects_data = res.get("result") if isinstance(res, dict) else None

            if isinstance(effects_data, list):
                effect_summary = ", ".join([e["name"] for e in effects_data if not e["disabled"]])
                if effect_summary:
                    logger.info(f"[Combat] {actor_name} has active effects: {effect_summary}")
        except Exception as e:
            logger.debug(f"[Combat] Could not query effects for {actor_name}: {e}")

    async def _fetch_initiative_order(self) -> List[str]:
        """Return the active Foundry combat's turn order as a list of token ids.

        Reads ``game.combat.turns`` via execute-js so the AI loop follows the
        same initiative the players see in the tracker. Returns [] when no
        combat exists or the read fails (caller falls back to a shuffle).
        """
        from foundry import scripts

        try:
            result = await self.foundry.execute_js(scripts.get_initiative_order())
            order = result.get("result") if isinstance(result, dict) else result
            if isinstance(order, list):
                return [str(t) for t in order if t]
        except Exception as e:
            logger.debug(f"[Combat] Could not read Foundry initiative order: {e}")
        return []

    async def _process_turns(self):
        """Loop through all turns in the current round."""
        while self._running and len(self._turn_order) > 0:
            # Check if current turn token still exists
            current_token_id = self._turn_order[self._current_turn_index % len(self._turn_order)]
            token = None
            is_dead_pc = False
            # Search in priority order: alive PCs, alive NPCs, then dead PCs
            # (0 HP but still alive — waiting for death save turn).
            for t in self._pc_tokens:
                if t["id"] == current_token_id:
                    token = t
                    break
            if not token:
                for t in self._npc_tokens:
                    if t["id"] == current_token_id:
                        token = t
                        break
            if not token and current_token_id in self._dead_pc_tokens:
                token = {"id": current_token_id, "name": "Unknown", "_dead_pc": True}
                is_dead_pc = True

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

            # ── Announce turn to chat ──────────────────────────────────────
            await self._announce_current_turn(actor_name, is_npc, self._current_turn_index + 1, self._round_number)

            # Notify admin panel
            if self._on_turn_start_callback:
                await self._on_turn_start_callback({
                    "type": "turn_started",
                    "round": self._round_number,
                    "turn": self._current_turn_index + 1,
                    "actor": actor_name,
                    "is_npc": is_npc
                })

            # ── Skip dead PCs silently (they never reached death-save stage) ─
            if is_dead_pc:
                logger.info(f"[Combat] {actor_name} is dead — skipping turn")
                self._current_turn_index += 1
                await self.state_tracker.update_combat(
                    in_combat=True,
                    round_num=self._round_number,
                    turn=self._current_turn_index,
                    turn_order=self._turn_order
                )
                await self.state_tracker.save()
                if await self._check_combat_end():
                    await self._end_combat()
                    break
                if self._current_turn_index >= len(self._turn_order):
                    self._current_turn_index = 0
                    self._round_number += 1
                    logger.info(f"[Combat] Started round {self._round_number}")
                await self._sync_foundry_combat_turn()
                continue

            # Wrap `_maybe_death_save` in try/except so a transient relay failure
            # here doesn't kill the combat task — the loop is a fire-and-forget
            # background task, so an unhandled exception silently freezes combat.
            try:
                if await self._maybe_death_save(token):
                    pass  # dead/stable (turn skipped) or a death save was just made (turn consumed)
                elif is_npc:
                    await self._process_npc_turn(token)
                else:
                    await self._wait_for_pc_input(token)
            except Exception as de:
                logger.error(f"[Combat] _maybe_death_save/_process_npc_turn/_wait_for_pc_input failed for {actor_name}: {de}", exc_info=True)

            # Legendary creatures may act at the end of any OTHER creature's
            # turn (RAW) — never their own, checked above via _maybe_death_save's
            # is_npc/else split not touching this token's own turn.
            # Wrap in try/except so a transient relay failure here doesn't kill
            # the combat task — see _process_npc_turn's exception handling.
            try:
                await self._maybe_legendary_actions(token)
            except Exception as le:
                logger.error(f"[Combat] _maybe_legendary_actions failed for {actor_name}: {le}", exc_info=True)

            # Advance to next turn
            self._current_turn_index += 1
            await self.state_tracker.update_combat(
                in_combat=True,
                round_num=self._round_number,
                turn=self._current_turn_index,
                turn_order=self._turn_order
            )
            await self.state_tracker.save()

            # Check end conditions after every turn so a fight stops the moment
            # one side is wiped out, not just at the round boundary.
            if await self._check_combat_end():
                await self._end_combat()
                break

            # Check if round is complete
            if self._current_turn_index >= len(self._turn_order):
                self._current_turn_index = 0
                self._round_number += 1
                logger.info(f"[Combat] Started round {self._round_number}")

                # Check if we've hit the round cap (stalemate)
                if self._round_number > settings.combat_round_cap:
                    stalemate_msg = f"⚔️ **Combat stalemate reached after round {settings.combat_round_cap}.** Neither side has broken through. GM must intervene (end encounter, shift tactics, or manually resolve)."
                    await self._foundry.chat_message(stalemate_msg, speaker="GM")
                    logger.info(f"[Combat] {stalemate_msg}")
                    await self._end_combat()
                    break

                # Lair actions happen at initiative count 20 (start of each round)
                try:
                    await self._maybe_lair_actions()
                except Exception as le:
                    logger.error(f"[Combat] _maybe_lair_actions failed for round {self._round_number}: {le}", exc_info=True)

                if self._on_turn_start_callback:
                    await self._on_turn_start_callback({
                        "type": "round_started",
                        "round": self._round_number
                    })

            await self._sync_foundry_combat_turn()

    async def _process_npc_turn(self, token: Dict[str, Any]):
        """Process an NPC's turn — LLM decides their action."""
        actor_name = token.get("name", "Unknown")
        actor_uuid = token.get("actorUuid", "")

        # Legendary creatures regain spent legendary actions at the start
        # of their own turn (RAW) — no-ops for non-legendary NPCs.
        if actor_uuid:
            try:
                from foundry import scripts as _scripts
                await self.foundry.execute_js(_scripts.reset_legendary_resource(actor_uuid))
            except Exception as _le:
                logger.debug(f"[Combat] Legendary-action reset failed for {actor_name}: {_le}")

        # ── Module-aware turn processing ──────────────────────────────────
        # Track active effects at start of turn (DAE)
        if self._has_dae:
            await self._track_active_effects(token)

        # Get scene tokens for positioning info
        scene_tokens = await self.foundry.get_scene_tokens()
        scene_info = json.dumps(scene_tokens, indent=None)

        # Inject NPC personality from registry if available
        personality_block = ""
        _npc_reg = getattr(self, "npc_registry", None)
        if _npc_reg is None:
            # Some instantiation paths store it on a different attribute
            _npc_reg = getattr(self, "_npc_registry", None)
        if _npc_reg:
            try:
                _rec = _npc_reg.get_npc_by_name(actor_name)
                if _rec:
                    parts = []
                    if getattr(_rec, "description", None):
                        parts.append(f"Background: {_rec.description[:400]}")
                    if getattr(_rec, "personality_traits", None):
                        parts.append(f"Personality: {_rec.personality_traits}")
                    if getattr(_rec, "combat_style", None):
                        parts.append(f"Combat style: {_rec.combat_style}")
                    if parts:
                        personality_block = "\n## NPC PERSONALITY\n" + "\n".join(parts)
            except Exception as _pe:
                logger.debug(f"[Combat] Could not load personality for {actor_name}: {_pe}")

        # Live geometry: distances, wall cover, flanking — computed, not guessed.
        tactical_block = ""
        try:
            from combat.tactics import build_tactical_snapshot
            tactical_block = await build_tactical_snapshot(self.foundry, token["id"])
        except Exception as _te:
            logger.debug(f"[Combat] Tactical snapshot failed: {_te}")

        # Real weapon/spell items this NPC can attack_with_item — without this
        # the LLM has no way to know a valid item_name to pass.
        attack_items_block = ""
        if actor_uuid:
            try:
                from foundry import scripts
                items_res = await self.foundry.execute_js(scripts.get_attack_items(actor_uuid))
                item_names = items_res.get("result") if isinstance(items_res, dict) else None
                if isinstance(item_names, list) and item_names:
                    attack_items_block = f"\n## YOUR ATTACK ITEMS\n{', '.join(item_names)}"
            except Exception as _ie:
                logger.debug(f"[Combat] Could not fetch attack items for {actor_name}: {_ie}")

        # Real remaining spell slots (including Pact Magic) — read live
        # from the sheet, not a static per-class table, so the LLM knows
        # whether this caster can actually cast_spell this turn.
        spell_slots_block = ""
        if actor_uuid:
            try:
                from foundry import scripts
                slots_res = await self.foundry.execute_js(scripts.get_spell_slots(actor_uuid))
                slots = slots_res.get("result") if isinstance(slots_res, dict) else None
                if isinstance(slots, dict) and slots:
                    parts = [
                        f"{'Pact' if lvl == 'pact' else f'Level {lvl}'}: {info.get('value', 0)}/{info.get('max', 0)}"
                        for lvl, info in slots.items()
                    ]
                    spell_slots_block = f"\n## YOUR SPELL SLOTS\n{', '.join(parts)}"
            except Exception as _se:
                logger.debug(f"[Combat] Could not fetch spell slots for {actor_name}: {_se}")

        # Multiattack tracking — reset at start of this NPC's turn
        self._attacks_used_this_turn[actor_uuid] = 0
        multiattack_block = ""
        multiattack_count = 1
        if actor_uuid:
            try:
                multiattack_info = await self.foundry.get_multiattack_count(actor_uuid)
                multiattack_count = multiattack_info.get("count", 1) if isinstance(multiattack_info, dict) else 1
                attacks_used = self._attacks_used_this_turn.get(actor_uuid, 0)
                attacks_remaining = multiattack_count - attacks_used
                multiattack_block = f"\n## MULTIATTACK\nYou can make {multiattack_count} attack(s) per turn. Used: {attacks_used}, Remaining: {attacks_remaining}"
            except Exception as _me:
                logger.debug(f"[Combat] Could not fetch multiattack count for {actor_name}: {_me}")

        # Build combat context
        combat_context = f"""
## COMBAT ROUND {self._round_number}
**Current Turn:** {self._current_turn_index + 1}/{len(self._turn_order)}
**Your Token:** {actor_name} (ID: {token['id']}){personality_block}

## ALL COMBATANTS
{self._build_combatant_list()}

## YOUR POSITION
x: {token.get('x', 0)}, y: {token.get('y', 0)}
{tactical_block}{attack_items_block}{multiattack_block}{spell_slots_block}

## AVAILABLE ACTIONS
You may issue up to 2-3 actions for this turn. Use:
- `attack_with_item` for attacks — pass a target_token_id from ALL COMBATANTS and an item_name from YOUR ATTACK ITEMS above (if you have any listed). Resolves for real: real roll, real hit check, real damage.
- `roll` for attacks with no real item behind them, or for skills/ability checks
- `move_token` to reposition
- `narrate` for descriptive actions
- `update_hp` if you damage yourself (for realism)
- `play_sound` for dramatic effects
""" + (f"""
## COMBAT AUTOMATION (Module Features Active)
{self._get_module_features_summary()}
""" if self._active_modules else "") + """
## RULES
1. Always respond with valid JSON containing an "actions" array
2. Keep narration 2-3 sentences maximum
3. Be decisive — pick ONE target per attack action
4. Use cover/positioning strategically
5. If HP is low, consider retreating or using defensive abilities"""

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
            actions = _limit_multiattack_actions(actions, multiattack_count)
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

            # Track multiattack usage: count how many attack_with_item actions were used
            attack_count = sum(
                1 for action, action_result in zip(actions, results)
                if action.get("type") == "attack_with_item"
                and action_result.get("success", True)
            )
            if attack_count > 0:
                self._attacks_used_this_turn[actor_uuid] = self._attacks_used_this_turn.get(actor_uuid, 0) + attack_count
                logger.info(f"[Combat] {actor_name} has used {self._attacks_used_this_turn[actor_uuid]} of their multiattacks")

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
            fallback_actions = _limit_multiattack_actions(fallback_actions, multiattack_count)
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
            # Advance the turn to prevent an infinite loop. Keep the player-facing
            # message generic; the detail is in the log above.
            await self.foundry.chat_message(
                f"**{actor_name} hesitates for a moment.**",
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

        # NOTE: keys MUST match the dispatcher/schema contract — actions are
        # keyed by "type", and a roll needs "formula" + "speaker". Earlier this
        # used "action"/"dice", which the dispatcher silently rejected, so a slow
        # LLM meant the NPC did nothing at all.
        actions = []
        if nearest_pc:
            pc_name = nearest_pc.get("name", "adventurer")
            actions.append({
                "type": "narrate",
                "text": f"{actor_name} moves toward {pc_name} and strikes!",
            })
            actions.append({
                "type": "roll",
                "formula": "1d20+4",
                "speaker": actor_name,
                "flavor": f"{actor_name} attacks {pc_name}",
            })
        else:
            actions.append({
                "type": "narrate",
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
        # Clear BEFORE the chat_message await so a signal set during the await
        # (player typed during the previous NPC turn) is not discarded.
        self._pc_turn_event.clear()
        await self.foundry.chat_message(
            f"⚔️ **Round {self._round_number}, Turn {self._current_turn_index + 1}:** {actor_name}'s turn. What do you do?",
            speaker="GM",
            whisper=[]
        )
        logger.info(f"[Combat] Waiting for {actor_name}'s input...")

        # Block until the chat listener fires the turn-advance callback, but cap
        # the wait so an AFK player (or a message lost to whisper/echo filtering)
        # can't deadlock the whole encounter. 0 falls back to the 180s default.
        _DEFAULT_PC_TIMEOUT = 180
        from config import settings as _settings
        timeout = max(1, getattr(_settings, "pc_turn_timeout", _DEFAULT_PC_TIMEOUT) or _DEFAULT_PC_TIMEOUT)
        try:
            await asyncio.wait_for(self._pc_turn_event.wait(), timeout=timeout)
            logger.info(f"[Combat] {actor_name}'s input received, advancing...")
        except asyncio.TimeoutError:
            logger.warning(f"[Combat] No input from {actor_name} after {timeout}s — skipping turn")
            await self.foundry.chat_message(
                f"⏭️ **{actor_name} hesitates and the moment passes — their turn is skipped.**",
                speaker="GM"
            )

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

            # PCs at 0 HP are moved to _dead_pc_tokens (waiting for death save
            # turn) — they're still alive, just unconscious, and MUST get a
            # death save on their own turn before being fully removed.
            alive_pc = []
            newly_dead = []
            for token in self._pc_tokens:
                t = token_map.get(token["id"], token)
                hp = self._get_hp_from_token(t)
                uuid = t.get("actorUuid", "")
                if hp <= 0:
                    # Check death/stable status
                    is_dead = False
                    is_stable = False
                    if uuid:
                        try:
                            from foundry import scripts
                            status = await self.foundry.execute_js(
                                scripts.get_death_save_status(uuid)
                            )
                            is_dead = (status.get("result") or {}).get("isDead", False)
                            is_stable = (status.get("result") or {}).get("isStable", False)
                        except Exception:
                            pass
                    if is_dead or is_stable:
                        newly_dead.append(t)
                    else:
                        alive_pc.append(t)  # still dying, needs death save
                else:
                    alive_pc.append(t)

            # Capture whether each side had combatants BEFORE updating the
            # lists, otherwise the end conditions can never be true.
            had_npc = bool(self._npc_tokens)
            had_pc = bool(self._pc_tokens)

            # Update internal token lists
            self._npc_tokens = alive_npc
            self._pc_tokens = alive_pc

            # Move newly dead PCs to the death-save queue
            for d in newly_dead:
                self._dead_pc_tokens.add(d.get("id", ""))

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
            # Guard with its own try/except — the same relay failure that triggered
            # the outer except will also fail this RPC, and an uncaught exception
            # here would propagate through _process_turns and kill the combat task.
            try:
                fresh = await self.foundry.get_scene_tokens()
                fresh_ids = {ft["id"] for ft in fresh}
                self._npc_tokens = [
                    t for t in self._npc_tokens if t["id"] in fresh_ids
                ]
                self._pc_tokens = [
                    t for t in self._pc_tokens if t["id"] in fresh_ids
                ]
            except Exception as prune_e:
                logger.warning(f"[Combat] Prune RPC also failed ({prune_e}) — keeping current token lists")

        return False

    async def _maybe_death_save(self, token: Dict[str, Any]) -> bool:
        """If token's actor is at 0 HP and still dying, trigger a death save
        for their turn instead of a normal action — same rule for PCs and
        NPCs, players roll their own via execute_death_save's PC-defer.

        Returns True if the turn should be fully skipped: dead, stable/
        unconscious (no further saves needed), or a save was just made (a
        dying creature can't act beyond the save itself that turn).
        """
        actor_uuid = token.get("actorUuid", "")
        actor_name = token.get("name", "Unknown")
        if not actor_uuid:
            if self._get_hp_from_token(token) <= 0:
                logger.info(f"[Combat] {actor_name} is down — skipping turn")
                return True
            return False

        status = await get_death_save_status(actor_uuid, self.foundry)
        if not status or (status.get("hp") or 0) > 0:
            return False
        if status.get("isDead") or status.get("isStable"):
            logger.info(
                f"[Combat] {actor_name} is {'dead' if status.get('isDead') else 'stable'} — skipping turn"
            )
            return True

        await execute_death_save(actor_uuid, foundry=self.foundry)
        return True

    async def _maybe_legendary_actions(self, acted_token: Dict[str, Any]) -> None:
        """After acted_token's turn resolves, let any OTHER legendary NPC
        still in the fight spend legendary actions — RAW: usable only at
        the end of another creature's turn, one at a time, never on the
        legendary creature's own turn (reset happens in _process_npc_turn).

        Reads the real legendary-action resource from the sheet
        (system.resources.legact) rather than a hardcoded per-monster list,
        so this works for any legendary compendium monster out of the box.
        """
        from foundry import scripts

        acted_id = acted_token.get("id")
        for token in list(self._npc_tokens):
            if token.get("id") == acted_id:
                continue
            actor_uuid = token.get("actorUuid", "")
            actor_name = token.get("name", "Unknown")
            if not actor_uuid or self._get_hp_from_token(token) <= 0:
                continue

            try:
                res = await self.foundry.execute_js(scripts.get_legendary_resource(actor_uuid))
                legact = res.get("result") if isinstance(res, dict) else None
            except Exception as e:
                logger.debug(f"[Combat] Legendary-action check failed for {actor_name}: {e}")
                continue
            if not isinstance(legact, dict) or (legact.get("value") or 0) <= 0:
                continue

            remaining = legact["value"]
            legendary_context = f"""
## LEGENDARY ACTION
{acted_token.get('name', 'A creature')}'s turn just ended. **{actor_name}** has {remaining} legendary action(s) left this round.

## ALL COMBATANTS
{self._build_combatant_list()}

You may spend ONE legendary action right now (`attack_with_item`, `roll`, or `move_token`), or pass — return an empty actions array, or a `narrate`-only response, to take no legendary action. This is a single quick action, not a full turn.
"""
            try:
                result = await asyncio.wait_for(
                    self.llm.generate(
                        user_message=f"{actor_name} may use a legendary action now, or pass.",
                        game_state_summary=self.state_tracker.get_snapshot(),
                        extra_context=legendary_context,
                    ),
                    timeout=30,
                )
            except Exception as e:
                logger.debug(f"[Combat] Legendary-action LLM call failed for {actor_name}: {e}")
                continue

            actions = result.get("actions", []) if isinstance(result, dict) else []
            spent = any(a.get("type") != "narrate" for a in actions)
            if actions:
                await self.dispatcher.execute_batch(actions)
            if spent:
                new_value = max(0, remaining - 1)
                try:
                    await self.foundry.execute_js(scripts.set_legendary_resource(actor_uuid, new_value))
                except Exception as e:
                    logger.debug(f"[Combat] Legendary-action spend failed for {actor_name}: {e}")
                logger.info(f"[Combat] {actor_name} spent a legendary action ({new_value} left)")

    async def _maybe_lair_actions(self) -> None:
        """At the start of each round (initiative count 20), environmental lair
        actions may occur if any legendary NPCs in the fight have them defined.

        Lair actions are not tied to a specific creature — they affect the
        environment. The engine prompts for descriptive narration rather than
        discrete mechanical actions.
        """
        lair_context = f"""
## LAIR ACTIONS (Initiative Count 20)
The environment itself may respond. If any legendary creatures in this lair have
prepared environmental lair actions, describe 1-3 of them now (or narrate nothing
if no lair actions are prepared). Lair actions do NOT cost legendary actions or
resource uses — they're environmental effects the lair itself produces.

Examples: A dragon lair might summon fire, a lich's phylactery chamber might
spawn undead, a demon prince's throne room might open portals.

Return an action array with `narrate` action(s) describing the lair's response
(or empty actions array if no lair effects trigger).
"""
        try:
            result = await asyncio.wait_for(
                self.llm.generate(
                    user_message=f"Round {self._round_number}: Any lair actions at initiative count 20?",
                    game_state_summary=self.state_tracker.get_snapshot(),
                    extra_context=lair_context,
                ),
                timeout=15,
            )
        except Exception as e:
            logger.debug(f"[Combat] Lair-actions LLM call failed for round {self._round_number}: {e}")
            return

        actions = result.get("actions", []) if isinstance(result, dict) else []
        if actions:
            await self.dispatcher.execute_batch(actions)
            logger.info(f"[Combat] Round {self._round_number}: Lair actions executed")

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

        # Guard the relay calls: a failure here must not kill the combat task
        # after state already says exploration (the end-of-combat message and
        # callbacks below would otherwise never fire).
        try:
            await self.foundry.end_encounter()
        except Exception as e:
            logger.warning(f"[Combat] end_encounter failed: {e}")
        try:
            from foundry import scripts
            await self.foundry.execute_js(scripts.end_combat())
        except Exception as e:
            logger.warning(f"[Combat] Foundry Combat cleanup failed: {e}")

        await self._clear_bossbar()
        try:
            await self.foundry.chat_message(
                "⚔️ **Combat ends!** The encounter is over.",
                speaker="GM"
            )
        except Exception as e:
            logger.warning(f"[Combat] Could not announce combat end: {e}")
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
            # Classify by the same rule as the turn-order split: a token is a
            # PC/ally only when its disposition is explicitly friendly/neutral.
            disp = token.get("disposition")
            side = "🟢 PC" if (disp is not None and disp >= 0) else "🔴 NPC"
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
