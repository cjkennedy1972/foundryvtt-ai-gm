"""Undo for the AI's own mechanical actions.

The AI-GM runs unattended, so nothing is approved in advance (see actions.audit);
the counterpart is being able to take one back. The dispatcher records a restore
point after each successful action that has a clean inverse — hit points and
token movement — and undo_last() applies the newest one.

HP is restored to the exact prior value rather than by inverting the damage:
Foundry clamps hp.value to [0, max], so "heal back the damage" overshoots after a
killing blow. The ledger lives in memory for the life of the engine process, which
outlasts Foundry reloads; a restart clears it.
"""

import logging
from collections import deque
from typing import Any, Dict, List, Optional

from actions.executors import _apply_hp_once, _read_hp
from foundry.client import FoundryClient

logger = logging.getLogger(__name__)

LEDGER_SIZE = 50


class UndoLedger:
    def __init__(self, maxlen: int = LEDGER_SIZE):
        self._entries: deque = deque(maxlen=maxlen)

    def record(self, entry: Dict[str, Any]) -> None:
        self._entries.append(entry)

    def last(self) -> Optional[Dict[str, Any]]:
        return self._entries[-1] if self._entries else None

    def discard_last(self) -> None:
        self._entries.pop()

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
    return None


async def undo_last(ledger: UndoLedger, foundry: FoundryClient) -> Dict[str, Any]:
    """Reverse the newest recorded action. The entry is dropped only once the restore
    succeeded, so a failed attempt can be retried."""
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
                await _apply_hp_once(foundry, "hp.value", current - r["hp_before"], r["actor_uuid"])
        else:
            moved = await foundry.move_token(r["token_id"], r["x"], r["y"])
            if not (isinstance(moved, dict) and moved.get("ok")):
                return {"success": False, "error": f"Could not move the token back: {moved}"}
    except Exception as e:
        logger.error(f"[Undo] restore failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    ledger.discard_last()
    logger.info(f"[Undo] reversed: {entry['label']}")
    return {"success": True, "label": entry["label"]}
