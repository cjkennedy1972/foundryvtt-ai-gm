#!/usr/bin/env python3
"""Manually run scene enrichment via the running AI GM app."""

import asyncio
import json
import httpx
from pathlib import Path


async def main():
    """Trigger enrichment via the admin API."""
    
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

    # Call admin API to run enrichment
    print("\n🏗️  Calling admin API to run enrichment...\n")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "http://localhost:5173/api/admin/enrich-scenes",
                json={
                    "campaign_data": campaign_data,
                    "deployment": deployment,
                },
                timeout=120.0
            )
            
            if response.status_code == 200:
                result = response.json()
                print("=" * 60)
                print("✅ ENRICHMENT COMPLETE")
                print("=" * 60)
                print(f"Enriched: {result.get('enriched', 0)} scenes")
                print(f"Skipped: {result.get('skipped', 0)} scenes")
                
                if result.get('errors'):
                    print(f"\n⚠️  Errors ({len(result['errors'])}):")
                    for error in result['errors']:
                        print(f"  - {error}")
                else:
                    print("\n✅ No errors!")
            else:
                print(f"❌ API error: {response.status_code}")
                print(response.text)
                
        except Exception as e:
            print(f"❌ Connection error: {e}")
            print("Make sure the AI GM app is running on localhost:5173")


if __name__ == "__main__":
    asyncio.run(main())
