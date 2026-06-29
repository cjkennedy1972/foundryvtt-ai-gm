#!/usr/bin/env python3
"""Manually run scene enrichment (place walls/lights/sounds) for deployed scenes."""

import asyncio
import json
import sys
from pathlib import Path

# Add ai-engine to path
sys.path.insert(0, str(Path(__file__).parent / "ai-engine"))

from campaign.orchestrator import CampaignOrchestrator
from foundry.client import FoundryClient
from relay_proc.manager import RelayManager


async def main():
    """Run enrichment for The Ashen Crown campaign."""

    campaign_path = Path.home() / "Vaults/MyStuff/Dungeons_and_Dragons/Campaigns/The Ashen Crown_ Descent Beneath Gravewatch"
    campaign_file = campaign_path / "campaign.json"
    deployment_file = Path(__file__).parent / "ai-engine/campaign_assets/the ashen crown_ descent beneath gravewatch/deployment_state.json"

    if not campaign_file.exists():
        print(f"❌ Campaign file not found: {campaign_file}")
        return

    if not deployment_file.exists():
        print(f"❌ Deployment file not found: {deployment_file}")
        return

    # Load campaign data
    print(f"📖 Loading campaign from: {campaign_file}")
    with open(campaign_file) as f:
        campaign_data = json.load(f)

    # Load deployment state
    print(f"📋 Loading deployment state from: {deployment_file}")
    with open(deployment_file) as f:
        deployment = json.load(f)

    # Initialize components
    print("🔌 Initializing Foundry client and relay...")
    relay_mgr = RelayManager()
    relay_mgr.start()
    await asyncio.sleep(2)  # Wait for relay to start

    foundry_client = FoundryClient(relay_manager=relay_mgr)

    # Connect to Foundry
    print("📡 Connecting to Foundry...")
    try:
        await foundry_client.connect_with_retry(max_retries=5)
    except Exception as e:
        print(f"❌ Failed to connect to Foundry: {e}")
        return

    print("✅ Connected to Foundry")

    # Run enrichment
    orchestrator = CampaignOrchestrator()

    print("\n🏗️  Running enrichment (placing walls/lights/sounds)...\n")

    def progress_callback(msg, step=None, detail=None):
        print(f"  {msg}")

    enrichment_result = await orchestrator.enrich_scenes(
        campaign_data=campaign_data,
        foundry_client=foundry_client,
        deployment=deployment,
        on_progress=progress_callback
    )

    # Report results
    print("\n" + "="*60)
    print("✅ ENRICHMENT COMPLETE")
    print("="*60)
    print(f"Enriched: {enrichment_result['enriched']} scenes")
    print(f"Skipped: {enrichment_result['skipped']} scenes")

    if enrichment_result['errors']:
        print(f"\n⚠️  Errors ({len(enrichment_result['errors'])}):")
        for error in enrichment_result['errors']:
            print(f"  - {error}")
    else:
        print("\n✅ No errors!")

    # Cleanup
    await foundry_client.disconnect()
    relay_mgr.stop()
    print("\n🛑 Disconnected and stopped relay")


if __name__ == "__main__":
    asyncio.run(main())
