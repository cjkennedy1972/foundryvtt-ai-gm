#!/usr/bin/env python3
"""Scanning vault notes registered a character called "Stat Block".

register_vault_npcs prefers campaign.json and falls back to scanning the
Markdown "for hand-made and legacy vaults that only ever had prose". That
scan treats every heading from # to ### as a character name, with a skip-list
covering a few index headings (overview, npcs, allies, summary).

The section headings this codebase's own build_npc_markdown emits are not on
it. Feeding one generated NPC note straight back into the scanner:

    one NPC note -> 7 NPCs registered:
        Elder Morwenna
        Description
        Personality
        Motivations
        Relationships
        Stat Block
        First Appearance

Six phantoms per real NPC, each with a 400-character slab of the note as its
description, each eligible for an autonomous NPC turn.

Two layouts have to keep working, which is what the single regex was trying
to straddle: one note per NPC, where the title is the character; and one note
listing many NPCs under ## headings, where the title is a section label.

Run:
    cd ai-engine && python -m pytest tests/test_vault_npc_scan.py -v
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.generator import build_npc_markdown
from context.loader import CampaignLoader


def _scan(files):
    loader = CampaignLoader.__new__(CampaignLoader)
    loader._data = files

    registered = []
    registry = MagicMock()
    registry.get_npc_by_name = MagicMock(return_value=None)
    registry.register_npc = MagicMock(
        side_effect=lambda **kw: registered.append(kw["npc_name"])
    )
    loader._register_npcs_from_notes(registry)
    return registered


# ── one note per NPC, the layout this codebase writes ─────────────────────

def test_a_generated_npc_note_registers_exactly_one_character():
    note = build_npc_markdown("Valenthal", {
        "name": "Elder Morwenna", "role": "sage", "faction": "The Circle",
        "description": "A stooped woman with river-stone eyes.",
    })

    assert _scan({"NPCs/Elder Morwenna": note}) == ["Elder Morwenna"]


def test_a_hand_written_npc_note_registers_its_title_not_its_sections():
    note = (
        "# Halda Ironvein\n\n"
        "## Appearance\n\nBroad-shouldered, soot-stained.\n\n"
        "## Background\n\nRan the forge before the flood.\n\n"
        "## Secrets\n\nShe kept the bone key.\n"
    )

    assert _scan({"NPCs/Halda": note}) == ["Halda Ironvein"]


def test_a_note_with_unusual_sections_still_registers_only_its_title():
    """The skip-list cannot enumerate what people call their sections. A note
    holding one NPC does not need it to: the title is the character."""
    note = (
        "# Captain Veyra\n\n"
        "## Bribe Schedule\n\nTwo silver a week.\n\n"
        "## Who Owes Her\n\nHalf the harbour.\n\n"
        "## Favourite Tavern\n\nThe Drowned Bell.\n"
    )

    assert _scan({"NPCs/Veyra": note}) == ["Captain Veyra"]


def test_two_notes_register_two_characters():
    a = build_npc_markdown("Valenthal", {"name": "Elder Morwenna"})
    b = build_npc_markdown("Valenthal", {"name": "Halda Ironvein"})

    assert sorted(_scan({"NPCs/A": a, "NPCs/B": b})) == ["Elder Morwenna", "Halda Ironvein"]


# ── one note listing many NPCs, the legacy layout ─────────────────────────

def test_a_list_note_under_a_section_title_still_finds_every_npc():
    """The title is "NPCs", a section label, so the ## headings are the
    characters. Taking the title alone here would register nobody."""
    note = (
        "# NPCs\n\n"
        "## Elder Morwenna\n\nA sage of the Circle.\n\n"
        "## Halda Ironvein\n\nThe smith.\n\n"
        "## Captain Veyra\n\nHarbour watch.\n"
    )

    assert sorted(_scan({"NPCs": note})) == [
        "Captain Veyra", "Elder Morwenna", "Halda Ironvein",
    ]


def test_a_list_note_with_no_title_at_all_still_finds_every_npc():
    note = "## Elder Morwenna\n\nA sage.\n\n## Halda Ironvein\n\nThe smith.\n"

    assert sorted(_scan({"Act I NPCs": note})) == ["Elder Morwenna", "Halda Ironvein"]


def test_section_headings_under_a_listed_npc_are_not_characters():
    note = (
        "# Key NPCs\n\n"
        "## Elder Morwenna\n\n### Appearance\n\nStooped.\n\n### Secrets\n\nKnows the key.\n\n"
        "## Halda Ironvein\n\n### Appearance\n\nSoot-stained.\n"
    )

    assert sorted(_scan({"NPCs": note})) == ["Elder Morwenna", "Halda Ironvein"]


# ── unchanged behaviour ───────────────────────────────────────────────────

def test_a_file_that_is_not_about_npcs_is_skipped():
    assert _scan({"Locations/The Sunken Chapel": "# The Sunken Chapel\n\nFlooded.\n"}) == []


def test_the_bold_name_fallback_still_works():
    note = "**Elder Morwenna:** a sage of the Circle.\n\n**Halda Ironvein:** the smith.\n"

    assert sorted(_scan({"NPCs/roster": note})) == ["Elder Morwenna", "Halda Ironvein"]
