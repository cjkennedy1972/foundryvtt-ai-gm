"""RefereeAgent — the rules authority that sits between the narrator LLM's
proposed actions and ActionDispatcher's execution.

ActionDispatcher already validates *mechanical* correctness (schema shape,
field whitelisting, damage clamping). The Referee checks *rules consistency*
instead: DC sanity-checking for skill checks/saving throws, and — given a
FoundryClient — spell-slot legality for cast_spell, read live off the
actor's real dnd5e sheet (foundry/scripts.py get_spell_slots) rather than a
duplicate SQLite ledger, since Foundry's actor sheet is already the sole
source of truth for resources (execute_cast_spell/use_spell_slot already
consume from it on the execution side). Without a FoundryClient (e.g. in
tests, or NPCAgent turns that don't need it) the slot check is skipped and
the action is approved unchanged — adjudication degrades gracefully rather
than blocking on data it can't reach.
"""

import logging
from typing import Any, Dict, List, Optional

from referee.models import Ruling
from rules.engine import RulesEngine

logger = logging.getLogger(__name__)

# Action types that carry a "dc" field subject to band-consistency checking.
_DC_ACTION_TYPES = {"skill_check", "saving_throw"}

# A DC further than this from the nearest standard band is treated as
# implausible rather than a deliberate in-between value.
_DC_BAND_TOLERANCE = 5


class RefereeAgent:
    """Adjudicates a single proposed action against the rules engine."""

    def __init__(self, rules_engine: RulesEngine = None, foundry=None):
        self.rules = rules_engine or RulesEngine()
        self.foundry = foundry

    async def adjudicate(self, action: Dict[str, Any]) -> Ruling:
        """Return a Ruling for one proposed action. Never raises — an
        adjudication error approves the action unchanged rather than
        blocking play, and logs the failure for investigation."""
        action_type = action.get("type")
        try:
            if action_type in _DC_ACTION_TYPES and "dc" in action:
                return self._check_dc_band(action)
            if action_type == "cast_spell" and self.foundry is not None:
                return await self._check_spell_slot(action)
            return Ruling(approved=True, action=action)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Referee adjudication failed for {action_type}: {exc}", exc_info=True)
            return Ruling(approved=True, action=action, notes=[f"adjudication error: {exc}"])

    async def adjudicate_batch(self, actions: List[Dict[str, Any]]) -> List[Ruling]:
        return [await self.adjudicate(a) for a in actions]

    def _check_dc_band(self, action: Dict[str, Any]) -> Ruling:
        dc = action["dc"]
        bands = self.rules.dc_by_difficulty.values()
        nearest = min(bands, key=lambda band: abs(band - dc))
        if abs(nearest - dc) <= _DC_BAND_TOLERANCE:
            return Ruling(approved=True, action=action)

        adjusted = {**action, "dc": nearest}
        reason = (
            f"DC {dc} is more than {_DC_BAND_TOLERANCE} off every standard "
            f"difficulty band; clamped to nearest band ({nearest})."
        )
        logger.info(f"Referee: {reason}")
        return Ruling(approved=True, action=adjusted, reason=reason)

    async def _check_spell_slot(self, action: Dict[str, Any]) -> Ruling:
        spell_level = action.get("spell_level", 0)
        # Cantrips (level 0) and ritual casts don't consume a slot — nothing
        # to check. execute_cast_spell independently verifies the ritual
        # claim itself (check_spell_ritual) before skipping slot use; the
        # Referee doesn't duplicate that, it only needs to know not to
        # reject a legitimate ritual cast for lacking a slot.
        if spell_level < 1 or action.get("ritual"):
            return Ruling(approved=True, action=action)

        actor_uuid = action.get("actor_uuid")
        if not actor_uuid:
            return Ruling(approved=True, action=action)

        # scripts.get_spell_slots (foundry/scripts.py) returns
        # {"1": {value, max}, ..., "pact": {value, max, casterLevel}} —
        # only levels with max > 0 are present; {} means no spellcasting
        # (or actor not found). No "slots" wrapper key.
        slots = await self.foundry.get_spell_slots(actor_uuid)
        if not isinstance(slots, dict) or not slots:
            # Couldn't read the sheet (actor not found, relay hiccup) —
            # fail open rather than block a cast we can't actually verify.
            return Ruling(approved=True, action=action)

        # 5e rule: a slot at spell_level OR HIGHER can cast it (upcasting).
        # Pact Magic slots are all castable at the Warlock's casterLevel.
        has_available_slot = any(
            slot.get("value", 0) > 0 and (
                slot.get("casterLevel", 0) >= spell_level if level == "pact"
                else int(level) >= spell_level
            )
            for level, slot in slots.items()
        )
        if has_available_slot:
            return Ruling(approved=True, action=action)

        reason = f"No available spell slot at or above level {spell_level} for {actor_uuid}."
        logger.info(f"Referee: rejected cast_spell — {reason}")
        return Ruling(approved=False, action=action, reason=reason)
