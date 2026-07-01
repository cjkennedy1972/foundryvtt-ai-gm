#!/usr/bin/env python3
"""Redeploy The Forbidden Library campaign with fixed wall alignment.

This script:
1. Deletes the old deployment (scenes, NPCs, etc.)
2. Generates fresh campaign data
3. Deploys with fixed code (1.5s wall placement delay before offset)
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from foundry.client import FoundryClient
from campaign.orchestrator import CampaignOrchestrator
from llm.client import LLMClient

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

CAMPAIGN_NAME = "The Forbidden Library"

CAMPAIGN_PROMPT = """
An ancient, corrupted library hidden beneath civilization. Filled with forbidden tomes,
cultist guardians, and dangerous magical anomalies. The party must navigate through
shifting halls of knowledge, confront the dark rituals bound to the archive's core,
and decide whether to preserve or destroy the forbidden texts. Each room holds secrets
that rewrite history itself.

Features:
- Multiple interconnected chambers with distinct challenges
- Cultist faction working to open an ancient seal
- Magical anomalies that twist space and perception
- Rare tomes granting forbidden knowledge (balanced for player agency)
- A final confrontation with the Archive's guardian entity
"""

async def delete_old_deployment(foundry_client):
    """Delete all traces of old deployment from Foundry."""
    from ai_engine.campaign.orchestrator import CampaignOrchestrator
    import json

    deployment_file = Path(__file__).parent / "campaign_assets" / "the forbidden library" / "deployment_state.json"

    if not deployment_file.exists():
        logger.warning("No deployment state found; skipping deletion")
        return

    logger.info("📋 Loading old deployment state...")
    with open(deployment_file) as f:
        state = json.load(f)

    logger.info("🗑️  Deleting old deployment...")

    # Delete scenes
    for scene in state.get("scenes", []):
        try:
            await foundry_client.delete_scene(scene["name"])
            logger.info(f"  ✓ Deleted scene: {scene['name']}")
        except Exception as e:
            logger.warning(f"  ✗ Failed to delete scene {scene['name']}: {e}")

    # Delete NPCs (actors)
    for npc in state.get("npcs", []):
        try:
            await foundry_client.delete_actor(npc["name"])
            logger.info(f"  ✓ Deleted NPC: {npc['name']}")
        except Exception as e:
            logger.warning(f"  ✗ Failed to delete NPC {npc['name']}: {e}")

    logger.info("✅ Old deployment cleared")

async def redeploy():
    """Full redeploy workflow."""

    # Initialize clients
    logger.info("🚀 Starting redeploy of The Forbidden Library...")
    logger.info(f"   Campaign: {CAMPAIGN_NAME}")
    logger.info(f"   Prompt: {CAMPAIGN_PROMPT[:80]}...")

    foundry = FoundryClient(
        relay_url=settings.relay_url,
        relay_ws_url=settings.relay_ws_url,
        relay_api_key=settings.relay_api_key,
    )

    llm_client = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.model,
    )

    orchestrator = CampaignOrchestrator(settings)

    await foundry.connect()

    try:
        # Delete old deployment
        logger.info("\n[Phase 1/6] Deleting old deployment...")
        await delete_old_deployment(foundry)

        # Build with fixed code
        logger.info("\n[Phase 2-6] Running full campaign build with fixed wall alignment...")
        logger.info("   (Walls will have 1.5s delay after placement before offset is applied)")

        result = await orchestrator.build_campaign(
            prompt=CAMPAIGN_PROMPT,
            campaign_name=CAMPAIGN_NAME,
            llm_client=llm_client,
            foundry_client=foundry,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=settings.omlx_url,
            omlx_api_key=settings.llm_api_key,
            on_progress=lambda msg: logger.info(f"   {msg}"),
            level_range="1-5",
        )

        logger.info("\n✅ REDEPLOY COMPLETE!")
        logger.info(f"\n📊 Deployment Summary:")
        logger.info(f"   Scenes: {len(result.get('scenes', []))} created")
        logger.info(f"   NPCs: {len(result.get('npcs', []))} created")
        logger.info(f"   Journal Entries: {len(result.get('journal_entries', []))} created")
        logger.info(f"   Encounters: {len(result.get('encounters', []))} set up")

        if result.get("errors"):
            logger.warning(f"\n⚠️  Deployment completed with {len(result['errors'])} errors:")
            for err in result["errors"][:5]:
                logger.warning(f"   - {err}")
            if len(result["errors"]) > 5:
                logger.warning(f"   ... and {len(result['errors']) - 5} more")

        logger.info("\n🎉 The Forbidden Library is ready to play!")
        logger.info("   Walls are now properly aligned (offset applied AFTER rendering).")

    except Exception as e:
        logger.error(f"\n❌ Redeploy failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await foundry.close()

async def main():
    await redeploy()

if __name__ == "__main__":
    asyncio.run(main())
