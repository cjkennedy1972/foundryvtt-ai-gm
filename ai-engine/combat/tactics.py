"""Live battlefield analysis: distances, flanking, and wall-based cover.

CombatMechanics has shipped flanking/cover math since day one — computed over
an always-empty positions dict, because nothing ever fed it live scene data.
This module is the feeder: one scene-state fetch in, a compact tactical text
block for the combat LLM out.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from combat.mechanics import CombatMechanics

logger = logging.getLogger(__name__)

Point = Tuple[float, float]
Segment = Tuple[float, float, float, float]


def _ccw(a: Point, b: Point, c: Point) -> bool:
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """Standard orientation test; ignores collinear grazing (good enough here)."""
    return _ccw(p1, p3, p4) != _ccw(p2, p3, p4) and _ccw(p1, p2, p3) != _ccw(p1, p2, p4)


def blocking_segments(walls: List[dict]) -> List[Segment]:
    """Wall segments that grant cover: solid walls and non-open doors."""
    segs: List[Segment] = []
    for w in walls or []:
        c = w.get("c") or []
        if len(c) != 4:
            continue
        if w.get("door", 0) and w.get("ds", 0) == 1:  # open door
            continue
        segs.append((float(c[0]), float(c[1]), float(c[2]), float(c[3])))
    return segs


def cover_between(a: Point, b: Point, wall_segs: List[Segment]) -> Optional[str]:
    """SRD-lite cover from walls crossing the attack line (pixel coords).

    One crossing = half cover (+2 AC), two or more = three-quarters (+5 AC).
    Full cover (no line of effect) is treated as three_quarter — the LLM
    should pick a better target either way.
    """
    crossings = sum(
        1
        for (x0, y0, x1, y1) in wall_segs
        if _segments_intersect(a, b, (x0, y0), (x1, y1))
    )
    if crossings == 0:
        return None
    return "half" if crossings == 1 else "three_quarter"


def _center_px(tok: dict, grid: float) -> Point:
    return (
        float(tok.get("x", 0)) + float(tok.get("width", 1)) * grid / 2,
        float(tok.get("y", 0)) + float(tok.get("height", 1)) * grid / 2,
    )


def render_snapshot(current_id: str, scene_state: dict) -> str:
    """Pure renderer: scene state (from scripts.tactical_scene_state) → text block.

    Returns '' when there is nothing tactical to say (no enemies, actor not
    found), so callers can concatenate unconditionally.
    """
    if not isinstance(scene_state, dict):
        return ""
    grid = float(scene_state.get("grid") or 64)
    tokens = scene_state.get("tokens") or []
    me = next((t for t in tokens if t.get("id") == current_id), None)
    if not me:
        return ""

    my_disp = me.get("disposition") or 0
    # Opposite-sign dispositions are enemies; same sign (excluding self) allies.
    # Neutral (0) actors treat hostiles (-1) as enemies.
    def _is_enemy(t):
        d = t.get("disposition")
        if d is None or t.get("id") == current_id:
            return False
        return (d * my_disp < 0) if my_disp else d == -1

    # Hidden tokens other than the actor are unrevealed GM information.
    visible = [t for t in tokens if not t.get("hidden") or t.get("id") == current_id]
    enemies = [t for t in visible if _is_enemy(t)]
    allies = [
        t for t in visible
        if t.get("disposition") == my_disp and t.get("id") != current_id
    ]
    if not enemies:
        return ""

    walls = blocking_segments(scene_state.get("walls") or [])

    mech = CombatMechanics()
    for t in visible:
        cx, cy = _center_px(t, grid)
        mech.update_position(
            t.get("id", ""), cx / grid, cy / grid,
            size=float(t.get("width", 1)) * 5,
        )
    ally_ids = [t.get("id", "") for t in allies]
    enemy_ids = [t.get("id", "") for t in enemies]

    my_c = _center_px(me, grid)
    lines: List[str] = []
    for t in sorted(enemies, key=lambda t: mech.get_distance(current_id, t.get("id", "")) or 9e9)[:10]:
        tid = t.get("id", "")
        dist = mech.get_distance(current_id, tid)
        if dist is None:
            continue
        dist_ft = max(5, int(round(dist / 5.0)) * 5)
        parts = [f"{t.get('name', 'enemy')}: {dist_ft} ft"]
        cover = cover_between(my_c, _center_px(t, grid), walls)
        if cover == "half":
            parts.append("half cover from you (+2 AC)")
        elif cover == "three_quarter":
            parts.append("heavy cover from you (+5 AC — consider repositioning)")
        if mech.is_flanking(current_id, tid, ally_ids):
            parts.append("you are FLANKING with an ally → attack with ADVANTAGE")
        lines.append("- " + ", ".join(parts))

    flankers = [
        t.get("name", "an enemy") for t in enemies
        if mech.is_flanking(t.get("id", ""), current_id, enemy_ids)
    ]
    if flankers:
        lines.append(
            f"⚠ You are being flanked by {', '.join(flankers[:3])} — "
            "their melee attacks on you have advantage; consider disengaging."
        )

    if not lines:
        return ""
    return (
        "\n## TACTICAL SNAPSHOT (computed from scene geometry — trust these numbers)\n"
        + "\n".join(lines)
    )


async def build_tactical_snapshot(foundry, current_token_id: str) -> str:
    """Fetch live scene state and render the tactical block; '' on any failure."""
    from foundry import scripts

    try:
        res = await foundry.execute_js(scripts.tactical_scene_state())
        scene_state = res.get("result") if isinstance(res, dict) else None
        return render_snapshot(current_token_id, scene_state)
    except Exception as e:
        logger.debug(f"[Tactics] snapshot failed: {e}")
        return ""
