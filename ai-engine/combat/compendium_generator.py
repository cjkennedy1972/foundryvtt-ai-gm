"""Encounter generation from Foundry D&D 5e compendium.

Queries real D&D 5e monsters from Foundry's compendiums, balances them against
the party using the DMG encounter-building math (per-character XP thresholds by
*level* and party *size*, plus the monster-count multiplier), and places them on
the map within the real scene bounds. Replaces LLM-based hallucinated monster
generation with verified stat blocks.

Balance math (DMG p.82):
  budget        = per_character_threshold[level][difficulty] * party_size
  adjusted_xp   = sum(monster_xp) * encounter_multiplier(count, party_size)
  an encounter fits when adjusted_xp <= budget.

Positioning is deliberately role-agnostic in Phase 1: real role detection
(melee vs. ranged) requires loading each stat block's actions, which is a
per-monster document fetch deferred to Phase 2. We cluster hostiles in a
grid-snapped block within the real scene instead of guessing role from CR.
"""

import logging
import math
import random
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from foundry.client import FoundryClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# D&D 5e encounter-building tables (DMG p.82). Single source of truth.
# ---------------------------------------------------------------------------

# CR -> XP. Authoritative monster XP values.
CR_XP = {
    0: 10, 0.125: 25, 0.25: 50, 0.5: 100,
    1: 200, 2: 450, 3: 700, 4: 1100,
    5: 1800, 6: 2300, 7: 2900, 8: 3900,
    9: 5000, 10: 5900, 11: 7200, 12: 8400,
    13: 10000, 14: 11500, 15: 13000, 16: 15000,
    17: 18000, 18: 20000, 19: 22000, 20: 25000,
    21: 33000, 22: 41000, 23: 50000, 24: 62000, 25: 75000,
    26: 90000, 27: 105000, 28: 120000, 29: 135000, 30: 155000,
}

# Per-character XP threshold by level -> (easy, medium, hard, deadly).
LEVEL_XP_THRESHOLDS = {
    1: (25, 50, 75, 100),       2: (50, 100, 150, 200),
    3: (75, 150, 225, 400),     4: (125, 250, 375, 500),
    5: (250, 500, 750, 1100),   6: (300, 600, 900, 1400),
    7: (350, 750, 1100, 1700),  8: (450, 900, 1400, 2100),
    9: (550, 1100, 1600, 2400), 10: (600, 1200, 1900, 2800),
    11: (800, 1600, 2400, 3600), 12: (1000, 2000, 3000, 4500),
    13: (1100, 2200, 3400, 5100), 14: (1250, 2500, 3800, 5700),
    15: (1400, 2800, 4300, 6400), 16: (1600, 3200, 4800, 7200),
    17: (2000, 3900, 5900, 8800), 18: (2100, 4200, 6300, 9500),
    19: (2400, 4900, 7300, 10900), 20: (2800, 5700, 8500, 12700),
}

DIFFICULTY_INDEX = {"easy": 0, "medium": 1, "hard": 2, "deadly": 3}

# Encounter multiplier tiers by monster count: 1, 2, 3-6, 7-10, 11-14, 15+.
# (DMG p.82.) Party-size adjustment shifts one step up (<3 PCs) or down (>=6 PCs).
MULTIPLIER_TIERS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]

# Encounter "shape" -> (min_count, max_count). The generator picks a shape per
# encounter so fights vary between a lone boss and a swarm instead of always
# defaulting to one big monster.
SHAPES = {
    "solo": (1, 1),    # a single strong threat / boss
    "duo": (2, 2),     # a pair
    "group": (3, 5),   # a small band
    "horde": (6, 8),   # many weak creatures
}

# Situational weighting: deadly/hard lean toward a boss; easy/trivial lean toward
# packs. Weights are relative (any positive numbers).
SHAPE_WEIGHTS = {
    "trivial": {"solo": 1, "duo": 2, "group": 4, "horde": 4},
    "easy":    {"solo": 1, "duo": 2, "group": 4, "horde": 3},
    "medium":  {"solo": 2, "duo": 3, "group": 3, "horde": 1},
    "hard":    {"solo": 3, "duo": 3, "group": 2, "horde": 1},
    "deadly":  {"solo": 4, "duo": 3, "group": 2, "horde": 1},
}


@dataclass
class Monster:
    """A candidate combatant — a compendium monster or a hostile world NPC."""
    name: str
    cr: float
    xp: int
    uuid: str
    size: str = "medium"
    environment: str = ""
    source: str = "compendium"  # "compendium" | "world" (an existing campaign NPC)

    @property
    def is_world_actor(self) -> bool:
        return self.source == "world"


def cr_to_xp(cr: float) -> int:
    """Map a Challenge Rating to its XP value (0 if unknown)."""
    return CR_XP.get(cr, 0)


def calc_budget(party_level: int, party_size: int, difficulty: str) -> float:
    """XP budget for a party = per-character threshold * party size.

    'trivial' is below the easy threshold (half of easy); other difficulties use
    the DMG table directly. Level is clamped to [1, 20].
    """
    level = max(1, min(20, int(party_level)))
    thresholds = LEVEL_XP_THRESHOLDS[level]
    if difficulty == "trivial":
        per_char = thresholds[0] * 0.5
    else:
        per_char = thresholds[DIFFICULTY_INDEX.get(difficulty, 1)]  # default medium
    return per_char * max(1, party_size)


def encounter_multiplier(monster_count: int, party_size: int) -> float:
    """DMG encounter multiplier by monster count, adjusted for party size."""
    if monster_count <= 0:
        return 1.0
    if monster_count == 1:
        tier = 0
    elif monster_count == 2:
        tier = 1
    elif monster_count <= 6:
        tier = 2
    elif monster_count <= 10:
        tier = 3
    elif monster_count <= 14:
        tier = 4
    else:
        tier = 5

    # Party-size adjustment: small parties feel encounters harder, large parties
    # easier, so shift the multiplier one tier accordingly.
    if party_size < 3:
        tier += 1
    elif party_size >= 6:
        tier -= 1

    tier = max(0, min(len(MULTIPLIER_TIERS) - 1, tier))
    return MULTIPLIER_TIERS[tier]


def adjusted_xp(monsters: List[Monster], party_size: int) -> float:
    """Total XP scaled by the encounter multiplier — the real difficulty figure."""
    raw = sum(m.xp for m in monsters)
    return raw * encounter_multiplier(len(monsters), party_size)


class CompendiumEncounterGenerator:
    """Generate balanced encounters from Foundry D&D 5e compendium monsters."""

    def __init__(
        self,
        foundry: FoundryClient,
        scene_width: int = 800,
        scene_height: int = 600,
        grid_size: int = 100,
    ):
        """
        Args:
            foundry: FoundryClient for executing JS queries and placement.
            scene_width: Real scene width in pixels (pass the actual scene's).
            scene_height: Real scene height in pixels.
            grid_size: Grid square size in pixels; placements snap to it.
        """
        self.foundry = foundry
        self.scene_width = scene_width
        self.scene_height = scene_height
        self.grid_size = max(1, grid_size)

    async def generate(
        self,
        party_level: int,
        party_size: int,
        difficulty: str = "medium",
        environment: Optional[str] = None,
        max_creatures: int = 8,
        shape: Optional[str] = None,
        rng: Optional[random.Random] = None,
    ) -> Dict[str, Any]:
        """
        Generate a balanced encounter from the compendium.

        The encounter *shape* (solo / duo / group / horde) is chosen randomly,
        weighted by difficulty, so fights vary. Pass ``shape`` to force one, or
        ``rng`` (a seeded ``random.Random``) for deterministic output.

        Returns a dict with creatures, placements, the XP budget, the adjusted
        XP actually used, and notes. ``adjusted_xp <= budget`` is guaranteed for
        any non-empty result.
        """
        rng = rng or random.Random()

        if shape and shape in SHAPES:
            lo, hi = SHAPES[shape]
            chosen_shape, target_count = shape, rng.randint(lo, hi)
        else:
            chosen_shape, target_count = self._choose_shape(difficulty, rng, party_size)
        target_count = max(1, min(target_count, max_creatures))

        logger.info(
            f"[CompendiumEncounter] Generating {difficulty} encounter "
            f"(shape={chosen_shape}, target={target_count}): "
            f"party_level={party_level}, party_size={party_size}, env={environment}"
        )

        budget = calc_budget(party_level, party_size, difficulty)
        logger.debug(f"[CompendiumEncounter] XP budget: {budget}")

        candidates = await self._query_compendium(party_level, environment)
        candidates = self._apply_environment_filter(candidates, environment)
        logger.debug(f"[CompendiumEncounter] {len(candidates)} candidates after filtering")

        selected = self._select_for_count(candidates, budget, party_size, target_count)
        used = adjusted_xp(selected, party_size)
        logger.info(
            f"[CompendiumEncounter] Selected {len(selected)} creatures "
            f"(shape={chosen_shape}), adjusted XP {used:.0f} / budget {budget:.0f}"
        )

        placements = self._position_cluster(selected)

        return {
            "creatures": [
                {"name": m.name, "cr": m.cr, "xp": m.xp, "uuid": m.uuid, "size": m.size}
                for m in selected
            ],
            "placements": placements,
            "budget": budget,
            "adjusted_xp": used,
            "total_xp_raw": sum(m.xp for m in selected),
            "difficulty_rating": difficulty,
            "shape": chosen_shape,
            "target_count": target_count,
            "party_level": party_level,
            "party_size": party_size,
            "notes": self._generate_notes(selected, budget, used, difficulty, chosen_shape),
        }

    def _choose_shape(
        self, difficulty: str, rng: random.Random, party_size: int = 4
    ) -> tuple:
        """Pick an encounter shape (weighted by difficulty) and a concrete count."""
        weights = dict(SHAPE_WEIGHTS.get(difficulty, SHAPE_WEIGHTS["medium"]))
        if party_size <= 2:
            # DMG's small-party multiplier already makes multi-enemy fights
            # substantially harder. Avoid horde action-economy spikes entirely
            # for solo/duet play; a single threat or pair is still playable.
            weights["solo"] = max(weights["solo"], 5)
            weights["duo"] = max(weights["duo"], 4)
            weights["group"] = 0
            weights["horde"] = 0
        shapes = list(weights.keys())
        shape = rng.choices(shapes, weights=[weights[s] for s in shapes], k=1)[0]
        lo, hi = SHAPES[shape]
        return shape, rng.randint(lo, hi)

    async def _query_compendium(
        self, party_level: int, environment: Optional[str] = None
    ) -> List[Monster]:
        """Gather candidate combatants from the live world.

        Two sources, merged:
          - **Compendium monsters** — discovered dynamically across all Actor
            packs (never a hardcoded pack id; worlds vary: SRD, D&D Beyond import,
            etc.), CR band [0, party_level + 3].
          - **Hostile world NPCs** — existing campaign opponents (Actor type
            'npc' with hostile prototype-token disposition), CR band
            [0, party_level + 5] so a near-tier recurring villain is eligible for
            deadly fights. These carry world UUIDs and place without importing.

        The relay wraps execute-js as an async function body, so the script uses
        `return await ...` (not an async IIFE, whose value the relay drops) and
        the result is unwrapped from the relay envelope via `.get("result")`.
        """
        try:
            max_cr = party_level + 3
            max_cr_world = party_level + 5
            js_query = f"""
            const deny = ['hero','pc','character','jb2a','evocation','token','vehicle'];
            const packs = game.packs.filter(p => p.documentName === 'Actor'
                && (p.index?.size ?? 0) > 0
                && !deny.some(d => p.collection.toLowerCase().includes(d)
                    || (p.metadata.label || '').toLowerCase().includes(d)));
            const lists = await Promise.all(packs.map(p =>
                p.getIndex({{ fields: ['system.details.cr','system.traits.size','system.details.type','system.details.environment'] }})
                 .then(idx => idx.contents.filter(m => {{
                       const cr = m.system?.details?.cr ?? -1; return cr >= 0 && cr <= {max_cr};
                   }}).map(m => ({{
                       name: m.name, cr: m.system?.details?.cr ?? 0, uuid: m.uuid,
                       size: m.system?.traits?.size ?? 'med',
                       environment: m.system?.details?.environment ?? '', source: 'compendium'
                   }})))
                 .catch(() => []) ));
            const compendium = lists.flat().slice(0, 400);
            const world = game.actors.filter(a => a.type === 'npc'
                    && (a.prototypeToken?.disposition ?? 0) < 0
                    && (a.system?.details?.cr ?? -1) >= 0
                    && (a.system?.details?.cr ?? 99) <= {max_cr_world})
                .map(a => ({{
                    name: a.name, cr: a.system?.details?.cr ?? 0, uuid: a.uuid,
                    size: a.system?.traits?.size ?? 'med',
                    environment: '', source: 'world'
                }}));
            return {{ compendium, world }};
            """
            res = await self.foundry.execute_js(js_query)
            payload = res.get("result") if isinstance(res, dict) else res
            if not isinstance(payload, dict):
                logger.warning(
                    f"[CompendiumEncounter] Candidate query returned "
                    f"{type(payload).__name__}, expected dict"
                )
                return []

            monsters: List[Monster] = []
            dropped = 0
            for source_key in ("world", "compendium"):
                for m in payload.get(source_key, []) or []:
                    if not isinstance(m, dict):
                        continue
                    cr = m.get("cr", 0)
                    xp = cr_to_xp(cr)
                    if xp <= 0:
                        dropped += 1
                        continue
                    monsters.append(Monster(
                        name=m.get("name", "Unknown"),
                        cr=cr,
                        xp=xp,
                        uuid=m.get("uuid", ""),
                        size=m.get("size", "med"),
                        environment=str(m.get("environment", "") or ""),
                        source=m.get("source", source_key),
                    ))

            n_world = sum(1 for m in monsters if m.is_world_actor)
            logger.info(
                f"[CompendiumEncounter] Gathered {len(monsters)} candidates "
                f"({n_world} campaign NPCs, {len(monsters) - n_world} compendium; "
                f"{dropped} dropped for unknown CR)"
            )
            return monsters

        except Exception as e:
            logger.error(f"[CompendiumEncounter] Candidate query failed: {e}", exc_info=True)
            return []

    def _apply_environment_filter(
        self, candidates: List[Monster], environment: Optional[str]
    ) -> List[Monster]:
        """Soft environment filter.

        Restrict to monsters whose environment tag matches *environment* — but
        only if enough match to build an encounter. Compendium environment data
        is sparse and unreliable, so we fall back to the full pool rather than
        returning an empty encounter. (ponytail: substring match; upgrade to a
        tag taxonomy if false matches become a problem.)
        """
        if not environment:
            return candidates
        env = environment.strip().lower()
        matches = [m for m in candidates if env in m.environment.lower()]
        if len(matches) >= 3:
            logger.debug(f"[CompendiumEncounter] Environment '{env}': {len(matches)} matches")
            return matches
        logger.debug(
            f"[CompendiumEncounter] Environment '{env}': only {len(matches)} matches — "
            f"using full pool"
        )
        return candidates

    def _select_monsters_greedy(
        self, candidates: List[Monster], budget: float, party_size: int, max_creatures: int
    ) -> List[Monster]:
        """Greedily select monsters whose *adjusted* XP fits the budget.

        Because the encounter multiplier rises with monster count, affordability
        is re-checked against adjusted XP after each tentative add — not against
        a running raw sum. Pass 1 favors variety (one of each name); pass 2 fills
        remaining budget with repeats.
        """
        if not candidates:
            return []

        # Bigger threats first so a high-level party gets a real centerpiece
        # rather than a swarm of trivial monsters. At equal CR, prefer an
        # existing campaign NPC over a generic compendium monster so recurring
        # opponents are used "when appropriate".
        ordered = sorted(candidates, key=lambda m: (m.cr, m.is_world_actor), reverse=True)

        selected: List[Monster] = []
        seen = set()

        def fits(trial: List[Monster]) -> bool:
            return len(trial) <= max_creatures and adjusted_xp(trial, party_size) <= budget

        for m in ordered:                       # pass 1: variety
            if m.name in seen:
                continue
            if fits(selected + [m]):
                selected.append(m)
                seen.add(m.name)

        for m in ordered:                       # pass 2: fill with repeats
            if len(selected) >= max_creatures:
                break
            if fits(selected + [m]):
                selected.append(m)

        return sorted(selected, key=lambda m: m.cr, reverse=True)

    def _select_for_count(
        self, candidates: List[Monster], budget: float, party_size: int, target_count: int
    ) -> List[Monster]:
        """Select ~target_count combatants whose adjusted XP fits the budget.

        Aims at the requested encounter size: for a horde it favors many weak
        monsters, for a solo it favors one strong threat. Picks monsters near the
        ideal per-creature XP (budget / count / multiplier), preferring a
        campaign NPC at equal fit. Falls back to a single biggest-affordable
        threat if the target can't be met (e.g. budget too small for a horde).
        """
        if not candidates:
            return []

        mult = encounter_multiplier(target_count, party_size)
        ideal = budget / max(1, target_count) / max(0.1, mult)  # ideal raw XP per creature

        # Prefer closeness to ideal; tie-break toward campaign NPCs.
        pool = sorted(candidates, key=lambda m: (abs(m.xp - ideal), not m.is_world_actor))

        selected: List[Monster] = []
        seen = set()

        def fits(trial):
            return adjusted_xp(trial, party_size) <= budget

        for m in pool:                              # variety toward target
            if len(selected) >= target_count:
                break
            if m.name in seen:
                continue
            if fits(selected + [m]):
                selected.append(m)
                seen.add(m.name)

        for m in pool:                              # fill remaining slots (repeats)
            if len(selected) >= target_count:
                break
            if fits(selected + [m]):
                selected.append(m)

        if not selected:
            # Target unattainable — fall back to the biggest single affordable threat.
            selected = self._select_monsters_greedy(candidates, budget, party_size, 1)

        return sorted(selected, key=lambda m: (m.cr, m.is_world_actor), reverse=True)

    def _snap(self, value: float) -> int:
        """Snap a pixel coordinate to the grid."""
        return int(round(value / self.grid_size) * self.grid_size)

    def _position_cluster(self, monsters: List[Monster]) -> List[Dict[str, Any]]:
        """Place hostiles in a grid-snapped block within the real scene bounds.

        Role-agnostic (see module docstring). Clusters toward the right-center of
        the map so enemies read as a group the party approaches.
        """
        if not monsters:
            return []

        n = len(monsters)
        cols = min(3, n)
        rows = math.ceil(n / cols)
        gs = self.grid_size

        block_w = (cols - 1) * gs
        block_h = (rows - 1) * gs
        center_x = self.scene_width * 0.65
        center_y = self.scene_height * 0.5
        origin_x = center_x - block_w / 2
        origin_y = center_y - block_h / 2

        max_x = max(0, self.scene_width - gs)
        max_y = max(0, self.scene_height - gs)

        placements = []
        for i, m in enumerate(monsters):
            r, c = divmod(i, cols)
            x = max(0, min(max_x, self._snap(origin_x + c * gs)))
            y = max(0, min(max_y, self._snap(origin_y + r * gs)))
            placements.append({
                "uuid": m.uuid,
                "name": m.name,
                "cr": m.cr,
                "source": m.source,
                "x": x,
                "y": y,
                "hidden": False,
            })
        logger.debug(f"[CompendiumEncounter] Positioned {n} creatures in {rows}x{cols} cluster")
        return placements

    def _generate_notes(
        self, monsters: List[Monster], budget: float, used: float,
        difficulty: str, shape: str = ""
    ) -> str:
        """Human-readable encounter summary, honest about the balance math."""
        if not monsters:
            return "Empty encounter — no monsters fit the budget."
        names = ", ".join(m.name for m in monsters)
        shape_txt = f"{shape} — " if shape else ""
        return (
            f"{shape_txt}{len(monsters)} combatants: {names}. "
            f"Adjusted XP {used:.0f} vs {difficulty} budget {budget:.0f}."
        )
