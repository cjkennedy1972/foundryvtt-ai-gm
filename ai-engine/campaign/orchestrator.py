"""
Campaign Orchestrator — High-level pipeline that orchestrates:
  1. LLM generates campaign structure from prompt
  2. Obsidian vault sync (markdown notes + structured data)
  3. ComfyUI map generation for locations
  4. Report progress to caller

Usage:
    from campaign.orchestrator import CampaignOrchestrator

    orch = CampaignOrchestrator()
    result = await orch.generate_campaign(
        prompt="A dark fantasy campaign about a kingdom ruled by a dead god...",
        on_progress=callback,
    )
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class CampaignOrchestrator:
    """Orchestrates the full campaign generation pipeline."""

    def __init__(self, settings=None):
        self.settings = settings
        self.campaign_dir = None  # Set when generate_campaign is called

    async def generate_campaign(
        self,
        prompt: str,
        llm_client,           # httpx.AsyncClient instance
        llm_base_url: str,
        llm_api_key: str,
        model: str = "",
        vault_path: str = None,
        comfyui_url: str = None,
        comfyui_checkpoint: str = None,
        on_progress: Callable = None,
    ) -> Dict[str, Any]:
        """Run the full campaign generation pipeline.

        Args:
            prompt: User's campaign description
            llm_client: httpx.AsyncClient for LLM calls
            llm_base_url: LLM base URL
            llm_api_key: LLM API key
            model: Model name (auto-detect if empty)
            vault_path: Obsidian vault path (from config)
            comfyui_url: ComfyUI URL for map generation
            on_progress: Optional callback(msg, step, detail)

        Returns:
            Result dict with campaign data, manifest, map info
        """
        result = {
            "status": "pending",
            "prompt_id": f"campaign-{__import__('uuid').uuid4().hex[:8]}",
            "steps": [],
        }

        def progress(msg: str, step: str = "", detail: str = ""):
            result["steps"].append({"message": msg, "step": step, "detail": detail})
            if on_progress:
                try:
                    on_progress(msg, step, detail)
                except Exception as e:
                    logger.debug(f"Progress callback error: {e}")

        # Step 1: Generate campaign structure from prompt
        progress("🏗️ Building campaign from prompt...", step="generate")
        try:
            from campaign.generator import (
                generate_campaign_prompt,
                parse_campaign_response,
                validate_campaign,
            )

            prompt_text = generate_campaign_prompt(prompt)
            messages = [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": prompt},
            ]

            endpoint = llm_base_url.rstrip("/") + "/chat/completions?thinking=false"
            headers = {
                "Authorization": f"Bearer {llm_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model or "",
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 8192,
            }

            resp = await llm_client.post(endpoint, json=payload, timeout=300)
            if resp.status_code != 200:
                result["status"] = "error"
                result["error"] = f"LLM request failed: {resp.status_code} {resp.text[:200]}"
                return result

            raw_text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            progress(f"✅ LLM generated campaign structure", step="generate", detail="parsing JSON")

            campaign_data = parse_campaign_response(raw_text)
            campaign_data["generated_prompt"] = prompt

            # Validate
            warnings = validate_campaign(campaign_data)
            if warnings:
                for w in warnings:
                    logger.warning(f"Campaign validation warning: {w}")
                result["validation_warnings"] = warnings

        except Exception as e:
            result["status"] = "error"
            result["error"] = f"Failed to generate campaign: {e}"
            logger.exception("Campaign generation failed")
            return result

        # Step 2: Sync to Obsidian vault
        progress("📝 Saving to Obsidian vault...", step="obsidian")
        manifest = None
        try:
            from campaign.obsidian_sync import sync_campaign_to_vault

            manifest = sync_campaign_to_vault(campaign_data, vault_path)
            result["manifest"] = manifest
            progress(f"✅ Campaign saved to {manifest['campaign_folder']}", step="obsidian",
                     detail=f"NPCs: {len(campaign_data.get('npcs', []))}, "
                            f"Locations: {len(campaign_data.get('locations', []))}, "
                            f"Quests: {len(campaign_data.get('quests', []))}")
        except Exception as e:
            result["status"] = "partial"
            result["obsidian_error"] = str(e)
            logger.warning(f"Obsidian sync failed (campaign still generated): {e}")

        # Step 3: Generate maps via ComfyUI (optional — only if URL provided)
        progress("🗺️ Generating maps via ComfyUI...", step="maps")
        maps_result = {"status": "skipped", "generated": []}

        if comfyui_url:
            try:
                from campaign.map_generator import MapGenerator

                progress("Checking ComfyUI availability...", step="maps")
                mg = MapGenerator(comfyui_url, checkpoint_name=comfyui_checkpoint or "juggernautXL_v11.safetensors")
                healthy = await mg.health_check()

                if not healthy:
                    maps_result["status"] = "comfyui_unavailable"
                    maps_result["warning"] = "ComfyUI not reachable — maps will not be generated"
                    progress("⚠️ ComfyUI not available — skipping maps", step="maps")
                else:
                    # Get location prompts for maps
                    locations = campaign_data.get("locations", [])
                    map_prompts = [loc.get("map_style", "") for loc in locations if loc.get("map_style")]

                    if map_prompts and manifest is None:
                        maps_result["status"] = "skipped"
                        maps_result["warning"] = "Vault sync failed — no folder to save maps into"
                        progress("⚠️ Skipping maps: vault sync failed", step="maps")
                    elif map_prompts:
                        # Generate maps for all locations
                        maps_output = Path(manifest["campaign_folder"]) / "Maps"
                        maps_output.mkdir(parents=True, exist_ok=True)

                        maps_result["status"] = "generating"
                        progress(f"Generating {len(map_prompts)} map(s)...", step="maps")

                        map_results = await mg.generate_batch(map_prompts, maps_output, steps=28, cfg=8.0)

                        maps_result["generated"] = []
                        for loc, map_res in zip(locations, map_results):
                            if map_res["status"] == "success":
                                maps_result["generated"].append({
                                    "location": loc["name"],
                                    "file": map_res["output_file"],
                                })
                                progress(f"  ✅ Map: {loc['name']}", step="maps", detail="done")
                            else:
                                progress(f"  ⚠️ Map failed: {loc['name']}", step="maps")

                        maps_result["status"] = "success" if maps_result["generated"] else "failed"
                    else:
                        maps_result["status"] = "no_maps_needed"
                        progress("ℹ️ No locations need maps", step="maps")

                await mg.close()
            except Exception as e:
                maps_result["status"] = "error"
                maps_result["error"] = str(e)
                progress(f"⚠️ Map generation error: {e}", step="maps")

        result["campaign_data"] = campaign_data
        result["maps"] = maps_result
        result["status"] = "complete"

        return result


async def build_campaign(
    prompt: str,
    llm_client,
    settings,
    vault_path: str = None,
    comfyui_url: str = None,
    on_progress: Callable = None,
) -> Dict[str, Any]:
    """Convenience function to generate a campaign."""
    orchestrator = CampaignOrchestrator(settings)

    if vault_path is None:
        vault_path = settings.campaign_vault_path

    model = settings.model if hasattr(settings, 'model') else ""
    api_key = settings.llm_api_key if hasattr(settings, 'llm_api_key') else ""
    base_url = settings.llm_base_url if hasattr(settings, 'llm_base_url') else "http://localhost:18800/v1"
    checkpoint = settings.comfyui_checkpoint if hasattr(settings, 'comfyui_checkpoint') else None

    return await orchestrator.generate_campaign(
        prompt=prompt,
        llm_client=llm_client,
        llm_base_url=base_url,
        llm_api_key=api_key,
        model=model,
        vault_path=vault_path,
        comfyui_url=comfyui_url,
        comfyui_checkpoint=checkpoint,
        on_progress=on_progress,
    )
