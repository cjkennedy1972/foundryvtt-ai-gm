"""Undo for the AI's own mechanical actions: HP changes, token moves, conditions and exhaustion.

The AI-GM runs unattended, so nothing is approved in advance (see actions.audit);
the counterpart is being able to take one back. The dispatcher records a restore
point after each successful action that has a clean inverse — hit points and
token movement — and undo_last() applies the newest one.

HP is restored to the exact prior value rather than by inverting the damage:
Foundry clamps hp.value to [0, max], so "heal back the damage" overshoots after a
killing blow. The ledger lives in memory for the life of the engine process, which
outlasts Foundry reloads; a restart clears it.
"""

import asyncio
import logging
from collections import deque
from typing import Any, Dict, List, Optional

from actions.executors import _apply_hp_once, _read_hp
from foundry import scripts
from foundry.client import FoundryClient

CONDITION_REMOVE_ATTEMPTS = 4
CONDITION_SETTLE_S = 0.8

logger = logging.getLogger(__name__)

LEDGER_SIZE = 50


class UndoLedger:
    def __init__(self, maxlen: int = LEDGER_SIZE):
        self._entries: deque = deque(maxlen=maxlen)
        self.lock = asyncio.Lock()  # one undo at a time: two would restore the same entry twice

    def record(self, entry: Dict[str, Any]) -> None:
        self._entries.append(entry)

    def last(self) -> Optional[Dict[str, Any]]:
        return self._entries[-1] if self._entries else None

    def discard(self, entry: Dict[str, Any]) -> None:
        """Drop this exact entry. Not "the newest": a restore awaits Foundry, and an action
        recorded in the meantime must keep its own undo."""
        for i, e in enumerate(self._entries):
            if e is entry:
                del self._entries[i]
                return

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        """Newest first, as labels only — what a person choosing to undo would read."""
        return [{"type": e["type"], "label": e["label"]} for e in list(self._entries)[::-1][:n]]


def restore_point(action_type: str, kwargs: Dict[str, Any], result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ledger entry for a successful action, or None when it cannot be undone."""
    if action_type == "update_hp":
        # get_actors only exposes hp.value, so only that path can be read back and restored.
        if kwargs.get("hp_path", "hp.value") != "hp.value" or result.get("hp_before") is None:
            return None
        uuid = result.get("actor_uuid") or kwargs["actor_uuid"]
        return {
            "type": action_type,
            "label": f"HP change on {uuid} (was {result['hp_before']} HP)",
            "restore": {"kind": "hp", "actor_uuid": uuid, "hp_before": result["hp_before"]},
        }
    if action_type == "move_token":
        moved = result.get("result") or {}
        if moved.get("fromX") is None or moved.get("fromY") is None:
            return None
        return {
            "type": action_type,
            "label": f"Move of {moved.get('name') or kwargs['token_id']} (from {moved['fromX']:g}, {moved['fromY']:g})",
            "restore": {"kind": "move", "token_id": moved.get("id") or kwargs["token_id"], "x": moved["fromX"], "y": moved["fromY"]},
        }
    if action_type == "apply_condition":
        # Only a condition THIS action added can be taken back; one the character already had, or one
        # whose prior state could not be read (None), must not be removed.
        if result.get("had_condition") is not False:
            return None
        status = str(kwargs["condition"]).lower()
        return {
            "type": action_type,
            "label": f"{kwargs['condition']} on {kwargs['actor_uuid']}",
            "restore": {"kind": "condition", "actor_uuid": kwargs["actor_uuid"], "status": status},
        }
    if action_type == "set_exhaustion":
        if result.get("previousLevel") is None or result.get("newLevel") is None or result["previousLevel"] == result["newLevel"]:
            return None
        return {
            "type": action_type,
            "label": f"Exhaustion on {kwargs['actor_uuid']} ({result['previousLevel']} -> {result['newLevel']})",
            "restore": {"kind": "exhaustion", "actor_uuid": kwargs["actor_uuid"], "level": result["previousLevel"]},
        }
    return None


async def _js(foundry: FoundryClient, script: str) -> Dict[str, Any]:
    res = await foundry.execute_js(script)
    inner = res.get("result") if isinstance(res, dict) else None
    return inner if isinstance(inner, dict) else {}


async def undo_last(ledger: UndoLedger, foundry: FoundryClient) -> Dict[str, Any]:
    """Reverse the newest recorded action. The entry is dropped only once the restore
    succeeded and, for HP, the sheet reads back the prior value, so a failed attempt
    can be retried and a failure is never reported as success."""
    async with ledger.lock:
        entry = ledger.last()
        if entry is None:
            return {"success": False, "error": "Nothing to undo."}
        r = entry["restore"]
        try:
            if r["kind"] == "hp":
                current, _ = await _read_hp(foundry, r["actor_uuid"])
                if current is None:
                    return {"success": False, "error": f"Could not read the current HP of {r['actor_uuid']}."}
                if current != r["hp_before"]:
                    # Positive damage lowers HP; this lands exactly on hp_before whatever Foundry clamped.
                    written = await _apply_hp_once(foundry, "hp.value", current - r["hp_before"], r["actor_uuid"])
                    if isinstance(written, dict) and written.get("success") is False:
                        return {"success": False, "error": f"Foundry refused the HP restore: {written.get('error') or written}"}
                    now, _ = await _read_hp(foundry, r["actor_uuid"])
                    if now != r["hp_before"]:
                        return {"success": False, "error": f"HP is {now} after the restore, expected {r['hp_before']}."}
            elif r["kind"] == "condition":
                # dnd5e 6 can hold a second copy of the status and re-adds its own right after the first is
                # removed, so remove until it is gone and give it a moment to settle before calling it stuck.
                state: Dict[str, Any] = {}
                for _ in range(CONDITION_REMOVE_ATTEMPTS):
                    try:
                        await foundry.remove_effect(r["actor_uuid"], r["status"])
                    except RuntimeError as e:
                        if "not found" not in str(e).lower():
                            raise                       # "no such status on the actor" means it is already gone
                    await asyncio.sleep(CONDITION_SETTLE_S)
                    state = await _js(foundry, scripts.condition_present(r["actor_uuid"], r["status"]))
                    if not state.get("ok") or not state.get("present"):
                        break
                if not state.get("ok"):
                    return {"success": False, "error": f"Could not confirm {r['status']} was removed: {state.get('error', 'no answer')}"}
                if state.get("present"):
                    return {"success": False, "error": f"{r['status']} is still on {r['actor_uuid']} after trying to remove it."}
            elif r["kind"] == "exhaustion":
                state = await _js(foundry, scripts.set_exhaustion_level(r["actor_uuid"], r["level"]))
                if not state.get("ok") or state.get("newLevel") != r["level"]:
                    return {"success": False, "error": f"Could not set exhaustion back to {r['level']}: {state.get('error', state)}"}
            else:
                moved = await foundry.move_token(r["token_id"], r["x"], r["y"])
                if not (isinstance(moved, dict) and moved.get("ok")):
                    return {"success": False, "error": f"Could not move the token back: {moved}"}
        except Exception as e:
            logger.error(f"[Undo] restore failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        ledger.discard(entry)
        logger.info(f"[Undo] reversed: {entry['label']}")
        return {"success": True, "label": entry["label"]}
