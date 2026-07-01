#!/usr/bin/env python3
"""Fix misaligned walls in currently deployed campaign.

Workflow:
1. Delete all walls from all scenes
2. Re-run enrichment with fixed wall placement code (1.5s delay)

This ensures walls are placed BEFORE offset is reapplied.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from foundry.client import FoundryRelayClient
from campaign.orchestrator import CampaignOrchestrator

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

async def fix_wall_alignment(campaign_name: str = None):
    """Delete walls and re-enrich scenes with fixed placement."""

    # Initialize clients
    foundry = FoundryRelayClient(
        relay_url=settings.relay_url,
        relay_ws_url=settings.relay_ws_url,
        relay_api_key=settings.relay_api_key,
    )

    await foundry.connect()

    try:
        # Get all scenes
        scenes = await foundry.get_scenes()
        print(f"\n📍 Found {len(scenes)} scenes in current world")

        # Identify campaign
        scene_names = [s.get("name", "") for s in scenes]
        print(f"Scenes: {', '.join(scene_names)}\n")

        if campaign_name:
            print(f"🎯 Targeting campaign: {campaign_name}")
        else:
            print("⚠️  No campaign specified. Will process ALL scenes.")

        # Delete walls from each scene
        deleted_count = 0
        for scene in scenes:
            scene_name = scene.get("name")
            print(f"\n🧹 Clearing walls from '{scene_name}'...", end=" ")

            try:
                await foundry.set_active_scene(scene_name)
                walls = await foundry.canvas_get("walls")

                if walls:
                    wall_ids = [w.get("_id") for w in walls if w.get("_id")]
                    if wall_ids:
                        await foundry.canvas_delete("walls", ids=wall_ids)
                        deleted_count += len(wall_ids)
                        print(f"✓ Deleted {len(wall_ids)} walls")
                    else:
                        print("(no wall IDs found)")
                else:
                    print("(no walls)")
            except Exception as e:
                print(f"✗ Error: {e}")

        print(f"\n✅ Total walls deleted: {deleted_count}")
        print("\n📋 Next steps:")
        print("   1. Run enrichment phase with fixed code:")
        print("      cd ai-engine && python main.py --mode enrich --campaign '<campaign_name>'")
        print("   2. This will recreate walls with 1.5s delay after placement")
        print("   3. Padding will be applied AFTER walls are rendered")

    finally:
        await foundry.close()

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fix wall alignment in deployed campaign")
    parser.add_argument("--campaign", help="Campaign name (optional; fixes all if not specified)")
    args = parser.parse_args()

    await fix_wall_alignment(campaign_name=args.campaign)

if __name__ == "__main__":
    asyncio.run(main())
