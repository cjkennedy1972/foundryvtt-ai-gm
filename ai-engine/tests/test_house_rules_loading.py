"""Tests for House Rules loading from campaign vault.

House Rules are campaign-specific rule modifications (critical hits, skill
successes, spell modifications, etc.) that should be injected into the system
prompt so the AI always honors them.

Implementation:
- CampaignLoader.get_house_rules_context_sync() loads HOUSE_RULES.md from vault
- LLMManager injects it via build_system_prompt(house_rules_context=...)
- Naming: any vault file with "HouseRules" in the path will be loaded

Run:
    cd ai-engine && python -m pytest tests/test_house_rules_loading.py -v
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from context.loader import CampaignLoader
from llm.system_prompts import build_system_prompt


@pytest.mark.asyncio
async def test_house_rules_loads_from_vault():
    """CampaignLoader finds and loads house rules file from vault."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)

        # Create a minimal vault structure
        campaign_dir = vault_path / "Test_Campaign"
        campaign_dir.mkdir()

        # Create a HOUSE_RULES.md file
        house_rules_file = campaign_dir / "HOUSE_RULES.md"
        house_rules_content = """# House Rules for The Shattered Coast

## Critical Hits & Misses
- Natural 20 always hits and deals max damage on the die (not the base die, but the damage die rolled)
- Natural 1 always misses and triggers a mishap table

## Saving Throws
- Dexterity saves against AoE spells can be made with half damage on success (not negation)

## Healing Spell Changes
- Healing Word heals at range but requires line of effect (not just line of sight)
"""
        house_rules_file.write_text(house_rules_content)

        # Load vault
        loader = CampaignLoader(vault_path=str(vault_path))
        data = await loader.load("Test_Campaign")

        # Verify house rules were loaded
        assert "HOUSE_RULES" in data, "HOUSE_RULES file not found in loaded data"
        assert "Critical Hits" in data["HOUSE_RULES"]
        assert "Natural 20" in data["HOUSE_RULES"]

        # Verify get_house_rules_context_sync returns formatted content
        context = loader.get_house_rules_context_sync()
        assert "House Rules" in context
        assert "Critical Hits" in context
        assert "Natural 20" in context
        assert "Critical Hits" in context
        assert "Natural 20" in context


@pytest.mark.asyncio
async def test_house_rules_gracefully_missing():
    """If no HOUSE_RULES file exists, loader returns empty string (no crash)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)

        campaign_dir = vault_path / "Test_Campaign"
        campaign_dir.mkdir()

        # Create minimal campaign files, but no HOUSE_RULES
        (campaign_dir / "NPCs.md").write_text("## Mara\nA wizard.")

        loader = CampaignLoader(vault_path=str(vault_path))
        await loader.load("Test_Campaign")

        # Should return empty string, not crash
        context = loader.get_house_rules_context_sync()
        assert context == "", f"Expected empty string, got {context!r}"


def test_house_rules_injected_into_system_prompt():
    """House rules context is included in the system prompt."""
    house_rules_context = """## House Rules

### Damage Resistances
- Resistance reduces damage to 1/2 (rounded down)
"""

    prompt = build_system_prompt(
        game_state="Players in tavern",
        npc_context="## NPCs\nMara the Wise",
        world_context="## World\nThe Shattered Coast",
        house_rules_context=house_rules_context,
    )

    assert "House Rules" in prompt
    assert "Damage Resistances" in prompt
    assert "Resistance reduces damage" in prompt


def test_house_rules_context_ordering():
    """House rules appear in the campaign context section (between world and canon)."""
    npc_ctx = "## NPCs\nOne"
    world_ctx = "## World\nTwo"
    house_ctx = "## House Rules\nThree"
    canon_ctx = "## Canon\nFour"

    prompt = build_system_prompt(
        npc_context=npc_ctx,
        world_context=world_ctx,
        house_rules_context=house_ctx,
        canon_context=canon_ctx,
    )

    # Verify all are present
    assert "One" in prompt and "Two" in prompt and "Three" in prompt and "Four" in prompt

    # Verify ordering: NPC → World → House Rules → Canon
    idx_npc = prompt.find("One")
    idx_world = prompt.find("Two")
    idx_house = prompt.find("Three")
    idx_canon = prompt.find("Four")

    assert idx_npc < idx_world < idx_house < idx_canon, (
        f"Wrong order: NPC={idx_npc}, World={idx_world}, "
        f"House Rules={idx_house}, Canon={idx_canon}"
    )


@pytest.mark.asyncio
async def test_house_rules_file_naming_variations():
    """Loader finds house rules file with 'HouseRules' in path (case-insensitive match)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)
        campaign_dir = vault_path / "Test_Campaign"
        campaign_dir.mkdir()

        # Case 1: Exact "HOUSE_RULES.md"
        (campaign_dir / "HOUSE_RULES.md").write_text("## Rules\nRoll d20")
        loader = CampaignLoader(vault_path=str(vault_path))
        await loader.load("Test_Campaign")
        context = loader.get_house_rules_context_sync()
        assert "Rules" in context

        # Case 2: Nested in subdirectory
        subdir = campaign_dir / "Config"
        subdir.mkdir()
        (subdir / "HouseRules.md").write_text("## Variant Rules\nNewRule")
        loader2 = CampaignLoader(vault_path=str(vault_path))
        await loader2.load("Test_Campaign")
        context2 = loader2.get_house_rules_context_sync()
        assert "Variant Rules" in context2
