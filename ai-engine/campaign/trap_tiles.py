"""Build Monk's Active Tiles trap-trigger tiles from generated scene data.

A generated scene may carry a ``trap_tiles`` array in its ``scene_setup``. Each
entry becomes an invisible Tile with a Monk's Active Tiles ``enter`` trigger that
whispers the trap's details to the GM when a token steps on it. The autonomous
GM (which watches chat) then resolves the save and damage with its normal
actions. Tiles are tagged with an ``aigm-trap`` flag so a redeploy can replace
them rather than stack duplicates.

The tile + MAT flag schema below was verified live against Foundry v14 /
monks-active-tiles (the document round-trips with active/trigger/actions
intact). The trigger *firing* is MAT's own client-side canvas behaviour at play
time — it only exercises when the scene is the viewed canvas and a real token
moves onto the tile.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}


def _trap_message(t: Dict[str, Any]) -> str:
    """A GM-facing one-liner describing what the trap does, for resolution."""
    name = t.get("name") or "Trap"
    detail = []
    ability = str(t.get("save_ability", "")).lower()[:3]
    dc = t.get("save_dc")
    if ability in _ABILITIES and dc:
        detail.append(f"{ability.upper()} save DC {dc}")
    damage = t.get("damage")
    if damage:
        dtype = str(t.get("damage_type", "")).strip()
        detail.append(f"{damage}{(' ' + dtype) if dtype else ''} on fail")
    line = f"⚠️ Trap triggered: {name}"
    if detail:
        line += " — " + ", ".join(detail)
    desc = t.get("description")
    if desc:
        line += f"\n{desc}"
    return line


def build_trap_tile_docs(
    trap_tiles: Optional[List[Dict[str, Any]]], grid_px: int = 64
) -> List[Dict[str, Any]]:
    """Convert scene ``trap_tiles`` (grid coords) into Foundry Tile documents.

    Each entry: ``{name, x, y, w, h, save_ability, save_dc, damage,
    damage_type, description}`` with x/y/w/h in grid squares. Malformed entries
    (non-integer coords) are skipped rather than raising.
    """
    docs: List[Dict[str, Any]] = []
    for t in trap_tiles or []:
        if not isinstance(t, dict):
            continue
        try:
            gx, gy = int(t.get("x", 0)), int(t.get("y", 0))
            gw, gh = max(1, int(t.get("w", 1))), max(1, int(t.get("h", 1)))
        except (TypeError, ValueError):
            continue
        docs.append({
            "x": gx * grid_px,
            "y": gy * grid_px,
            "width": gw * grid_px,
            "height": gh * grid_px,
            "hidden": True,  # invisible to players; it is a trigger region
            "flags": {
                "aigm-trap": {"version": 1, "name": t.get("name", "Trap")},
                "monks-active-tiles": {
                    "active": True,
                    "restriction": "all",
                    "controlled": "all",
                    "trigger": ["enter"],
                    "pertoken": False,
                    "minrequired": 0,
                    "chance": 100,
                    "actions": [{
                        "id": f"aigmtrap-{gx}-{gy}",
                        "action": "chatmessage",
                        "data": {
                            "text": _trap_message(t),
                            "entity": "",
                            "for": "gm",
                            "flavor": "",
                            "incharacter": False,
                            "chatbubble": False,
                        },
                    }],
                },
            },
        })
    return docs
