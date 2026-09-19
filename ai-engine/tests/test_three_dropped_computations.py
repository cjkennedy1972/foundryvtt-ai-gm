#!/usr/bin/env python3
"""Three places that computed something and threw it away.

ruff flags all three as F841 (assigned but never used) and I had dismissed
that whole class as cosmetic. It was not: the encounter-difficulty bug fixed
in #179 was an F841 too — `level` read from the party and never used.

  combat/loop.py   reads `_rec.personality_traits` and `_rec.combat_style`
                   off an NPCRecord that has neither, via getattr(..., None),
                   so the combat prompt only ever emitted "Background:".
                   Same shape as the get_context defect fixed in #177, in the
                   combat path instead of the exploration one.

  combat/loop.py   spent a get_scene_tokens() round trip per NPC turn to
                   build `scene_info`, then discarded it. The prompt already
                   carries every combatant's position and computed
                   distance/cover/flanking, so the answer is to stop paying
                   for it, not to paste raw JSON into the context.

  procedural/      built a PartyComposition and dropped it, sizing monsters
  encounters.py    from an ad-hoc `party_level * party_size * 0.5/1.0/1.5`
                   instead. So the DMG budgets never reached generated
                   encounters, and asking for "hard" got you whatever that
                   formula produced.

Run:
    cd ai-engine && python -m pytest tests/test_three_dropped_computations.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.difficulty import (
    DynamicDifficulty,
    EncounterDifficulty,
    EncounterProfile,
)
from npc.registry import NPCRegistry
from procedural.encounters import EncounterGenerator


# ── 1. combat reads NPC fields that do not exist ──────────────────────────

def test_the_combat_prompt_reads_a_field_npcrecord_actually_has():
    """personality_traits and combat_style are not NPCRecord fields, so
    getattr(..., None) silently yielded nothing every turn."""
    import dataclasses
    from npc.registry import NPCRecord
    import combat.loop as loop_src
    import inspect

    fields = {f.name for f in dataclasses.fields(NPCRecord)}
    src = inspect.getsource(loop_src)
    block = src.split("Inject NPC personality from registry", 1)[1].split("Live geometry", 1)[0]

    read = {
        line.split('"')[1]
        for line in block.splitlines()
        if 'getattr(_rec, "' in line
    }
    missing = sorted(read - fields)
    assert missing == [], f"combat reads NPCRecord fields that do not exist: {missing}"


def test_registered_traits_are_visible_to_the_combat_prompt():
    """The registry stores traits under `personality`; if combat cannot read
    that, an NPC's personality never influences how it fights."""
    registry = NPCRegistry()
    registry.register_npc(npc_id="ogre", npc_name="Grukk", description="A brute.")
    registry.set_npc_personality("ogre", {"combat": ["reckless", "cruel"]})

    record = registry.get_npc_by_name("Grukk")
    assert record.personality, "the registry is where traits live"
    assert not hasattr(record, "personality_traits")
    assert not hasattr(record, "combat_style")


# ── 2. the discarded scene-token round trip ───────────────────────────────

def test_the_npc_turn_does_not_fetch_scene_tokens_it_never_reads():
    """One relay round trip per NPC turn, for a string nothing consumed."""
    import inspect
    import combat.loop as loop_src

    body = inspect.getsource(loop_src.CombatLoop._process_npc_turn)
    assert "scene_info" not in body, "scene_info is built and never used"


def test_the_prompt_still_tells_the_npc_where_everyone_is():
    """Deleting the unused fetch must not cost the positions that the
    combatant list and the tactical snapshot really do provide."""
    import inspect
    import combat.loop as loop_src

    body = inspect.getsource(loop_src.CombatLoop._process_npc_turn)
    assert "_build_combatant_list()" in body
    assert "tactical_block" in body


# ── 3. procedural encounters ignore the difficulty engine ─────────────────

ORDER = [
    EncounterDifficulty.TRIVIAL,
    EncounterDifficulty.EASY,
    EncounterDifficulty.MEDIUM,
    EncounterDifficulty.HARD,
    EncounterDifficulty.DEADLY,
]


def _rate(generated, level, size):
    """Rate a generated encounter with the same engine callers use."""
    engine = DynamicDifficulty()
    crs = [m["cr"] for m in generated.monsters for _ in range(m.get("count", 1))]
    profile = EncounterProfile([m["name"] for m in generated.monsters], crs)
    return engine.calculate_difficulty(profile, engine.get_party_composition(size, float(level)))


@pytest.mark.parametrize("band", ["easy", "medium", "hard", "deadly"])
@pytest.mark.parametrize("level,size", [(3, 4), (5, 4), (8, 5), (12, 4)])
def test_a_generated_encounter_rates_as_the_band_it_was_asked_for(band, level, size):
    """The generator and the rater must agree — that is the point of asking
    for a difficulty. Held for ordinary parties; the extremes are covered
    below, where MONSTERS_BY_CR genuinely cannot reach the band."""
    generated = EncounterGenerator().generate(band, level, size)

    assert _rate(generated, level, size) == EncounterDifficulty[band.upper()]


def test_the_encounter_reports_the_band_it_actually_is():
    """MONSTERS_BY_CR stops at CR 5 and starts at CR 1/4, so some bands are
    out of reach at the extremes. Echoing the request back would misreport
    it; the caller has to be able to see what it really got."""
    for band in ("trivial", "easy", "medium", "hard", "deadly"):
        for level, size in ((1, 1), (2, 1), (5, 4), (20, 8)):
            generated = EncounterGenerator().generate(band, level, size)
            assert generated.difficulty == _rate(generated, level, size).value


def test_an_unreachable_band_falls_to_the_end_it_missed():
    """Asking for deadly at level 20 fell back to a single CR 1/4 — the
    softest possible roster for the hardest possible request.

    MONSTERS_BY_CR stops at CR 5, so eight level-20 characters cannot be
    given a deadly fight from it at all (twelve of the largest rate medium).
    The fallback must at least reach for the top."""
    generated = EncounterGenerator().generate("deadly", 20, 8)

    assert sum(m["count"] for m in generated.monsters) == EncounterGenerator._MAX_MONSTERS
    assert max(m["cr"] for m in generated.monsters) == max(EncounterGenerator.MONSTERS_BY_CR)
    assert ORDER.index(_rate(generated, 20, 8)) > ORDER.index(EncounterDifficulty.EASY)


def test_a_band_beneath_the_smallest_monster_falls_the_other_way():
    """One CR 1/4 already outweighs every band for a solo level-1 character,
    so the fallback must go small, not reach for twelve of them."""
    generated = EncounterGenerator().generate("trivial", 1, 1)

    assert sum(m["count"] for m in generated.monsters) == 1
    assert generated.monsters[0]["cr"] == min(EncounterGenerator.MONSTERS_BY_CR)


def test_the_requested_band_still_drives_the_roster():
    """The old code compared an EncounterDifficulty member against the string
    "easy" — never equal, so every encounter took the same branch and the
    requested band changed nothing."""
    from combat.difficulty import XP_BY_CR

    def xp(band):
        enc = EncounterGenerator().generate(band, 5, 4)
        return sum(XP_BY_CR.get(m["cr"], 0) * m.get("count", 1) for m in enc.monsters)

    assert xp("deadly") > xp("easy")


def test_every_generated_monster_has_a_name_and_a_real_cr():
    from combat.difficulty import XP_BY_CR

    enc = EncounterGenerator().generate("medium", 7, 4)

    assert enc.monsters
    for m in enc.monsters:
        assert m["name"] and not m["name"].startswith("Unknown")
        assert m["cr"] in XP_BY_CR, f"CR {m['cr']} has no XP value"
        assert m.get("count", 1) >= 1


def test_treasure_scales_with_how_many_monsters_there_are():
    """treasure_cr summed one CR per entry, ignoring the count beside it."""
    enc = EncounterGenerator().generate("deadly", 10, 4)

    per_entry = sum(m["cr"] for m in enc.monsters)
    assert enc.treasure_cr >= per_entry
    if any(m["count"] > 1 for m in enc.monsters):
        assert enc.treasure_cr > per_entry
