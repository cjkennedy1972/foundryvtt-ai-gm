"""
Campaign Orchestrator — High-level pipeline for building FoundryVTT campaigns.

Orchestrates the full campaign creation pipeline:
1. Scan the connected FoundryVTT world (detect scenes, actors, modules, capabilities)
2. Generate a complete campaign structure via LLM
3. Save campaign data to Obsidian vault
4. Generate map/portrait images via oMLX or ComfyUI
5. Deploy campaign elements to FoundryVTT (scenes, journal entries, NPCs, loot tables, quest logs)
6. Report progress to caller

Usage:
    from campaign.orchestrator import CampaignOrchestrator

    orch = CampaignOrchestrator()
    result = await orch.build_campaign(
        name="My New Campaign",
        prompt="A dark fantasy campaign about...",
        on_progress=callback,
    )
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)


class CampaignOrchestrator:
    """Orchestrates the full campaign build pipeline."""

    def __init__(self, settings_obj=None):
        self.settings = settings_obj or settings

    # ─── Phase 1: Scan FoundryVTT world ─────────────────────────────────────

    async def scan_foundry_world(self, foundry_client) -> Dict[str, Any]:
        """Scan the currently connected FoundryVTT world.

        Detects:
        - Active scenes and rooms
        - Existing actors/NPCs
        - Users connected
        - Available modules/add-ons
        - Loot tables and journal entries
        - General world capabilities

        Returns a catalog of existing content and available capabilities.
        """
        scan_result = {
            "scenes": [],
            "actors": [],
            "users": [],
            "rooms": [],
            "modules": [],
            "capabilities": {},
        }

        # Scan scenes
        try:
            scenes = await foundry_client.get_scenes()
            scan_result["scenes"] = scenes
        except Exception as e:
            logger.warning(f"Failed to scan scenes: {e}")

        # Scan actors
        try:
            actors = await foundry_client.get_actors()
            scan_result["actors"] = actors
        except Exception as e:
            logger.warning(f"Failed to scan actors: {e}")

        # Scan users
        try:
            users = await foundry_client.get_users()
            scan_result["users"] = users
        except Exception as e:
            logger.warning(f"Failed to scan users: {e}")

        # Scan rooms
        try:
            rooms = await foundry_client.get_rooms()
            scan_result["rooms"] = rooms
        except Exception as e:
            logger.warning(f"Failed to scan rooms: {e}")

        # Determine capabilities
        scan_result["capabilities"] = {
            "has_scenes": len(scan_result["scenes"]) > 0 or len(scan_result["rooms"]) > 0,
            "has_actors": len(scan_result["actors"]) > 0,
            "has_users": len(scan_result["users"]) > 0,
            "has_rooms": len(scan_result["rooms"]) > 0,
            "scene_creation": True,  # Assume supported via relay
            "journal_entries": True,
            "quest_system": True,
            "combat_system": True,
            "loot_tables": True,
        }

        return scan_result

    # ─── Phase 2: Generate campaign via LLM ─────────────────────────────────

    async def generate_campaign_data(
        self,
        prompt: str,
        llm_client,
        scan_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate complete campaign data using the LLM."""
        from campaign.generator import (
            generate_campaign_prompt,
            parse_campaign_response,
            validate_campaign,
        )

        # Build enhanced prompt with scan results if available
        scan_info = ""
        if scan_result and (scan_result.get("scenes") or scan_result.get("actors")):
            existing_scenes = [s.get("name", "Unknown") for s in scan_result.get("scenes", [])]
            existing_actors = [a.get("name", "Unknown") for a in scan_result.get("actors", [])]
            scan_info = (
                "\n\n## Current FoundryVTT World Context\n"
                f"- Existing scenes: {', '.join(existing_scenes) if existing_scenes else '(none)'}\n"
                f"- Existing actors: {', '.join(existing_actors) if existing_actors else '(none)'}\n"
                f"- Users online: {len(scan_result.get('users', []))}\n"
                "Build the new campaign alongside or in addition to this existing content.\n"
            )

        prompt_text = generate_campaign_prompt(prompt) + scan_info

        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": prompt},
        ]

        # Add oMLX instruction to skip thinking
        if "Qwen" in (self.settings.model or ""):
            # Some models need explicit "skip thinking" instruction
            messages[0]["content"] += (
                "\n\nIMPORTANT: Do not output any thinking or reasoning. "
                "Start your response directly with `{` and end with `}`."
            )

        endpoint = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": 0.85,
            "max_tokens": 16384,
        }

        resp = await llm_client.post(endpoint, json=payload, timeout=600)
        if resp.status_code != 200:
            raise Exception(f"LLM request failed: {resp.status_code} {resp.text[:500]}")

        raw_text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        campaign_data = parse_campaign_response(raw_text)
        campaign_data["generated_prompt"] = prompt
        campaign_data["generated_at"] = time.strftime("%Y-%m-%d %H:%M")

        # Validate
        warnings = validate_campaign(campaign_data)
        if warnings:
            for w in warnings:
                logger.warning(f"Campaign validation warning: {w}")
        campaign_data["validation_warnings"] = warnings

        return campaign_data

    # ─── Phase 3: Save to Obsidian vault ────────────────────────────────────

    async def save_to_vault(
        self, campaign_data: Dict[str, Any], vault_path: str = None
    ) -> Dict[str, Any]:
        """Save campaign data to Obsidian vault."""
        if vault_path is None:
            vault_path = settings.campaign_vault_path

        from campaign.obsidian_sync import sync_campaign_to_vault

        manifest = sync_campaign_to_vault(campaign_data, vault_path)
        return manifest

    # ─── Phase 4: Generate maps and portraits ────────────────────────────────

    async def generate_assets(
        self,
        campaign_data: Dict[str, Any],
        map_generator,
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Generate all map and portrait images for the campaign."""
        output_dir.mkdir(parents=True, exist_ok=True)
        results = {
            "maps": [],
            "portraits": [],
            "status": "completed",
        }

        # Generate maps for scenes
        scenes = campaign_data.get("scenes", [])
        location_scenes = [s for s in scenes if s.get("map_needed")]

        if location_scenes:
            logger.info(f"Generating {len(location_scenes)} scene map(s)...")
            for scene in location_scenes:
                prompt = scene.get("map_style", f"{scene.get('type', 'fantasy')} scene map")
                map_file = output_dir / f"scene_{scene['name'].replace(' ', '_').lower()}.png"

                try:
                    map_result = await map_generator.generate_map(
                        prompt=prompt,
                        output_dir=output_dir,
                    )
                    if map_result["status"] == "success":
                        results["maps"].append({
                            "scene": scene["name"],
                            "type": "scene_map",
                            "file": map_result["output_file"],
                            "provider": map_result.get("provider", "unknown"),
                        })
                        scene["map_file"] = Path(map_result["output_file"]).name
                    else:
                        logger.warning(f"Map generation failed for {scene['name']}: {map_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Map generation error for {scene['name']}: {e}")

        # Generate maps for locations
        locations = campaign_data.get("locations", [])
        location_maps = [l for l in locations if l.get("map_needed")]

        if location_maps:
            logger.info(f"Generating {len(location_maps)} location map(s)...")
            for loc in location_maps:
                prompt = loc.get("map_style", f"{loc.get('type', 'fantasy')} location map")
                map_file = output_dir / f"location_{loc['name'].replace(' ', '_').lower()}.png"

                try:
                    map_result = await map_generator.generate_map(
                        prompt=prompt,
                        output_dir=output_dir,
                    )
                    if map_result["status"] == "success":
                        results["maps"].append({
                            "location": loc["name"],
                            "type": "location_map",
                            "file": map_result["output_file"],
                            "provider": map_result.get("provider", "unknown"),
                        })
                        loc["map_file"] = Path(map_result["output_file"]).name
                    else:
                        logger.warning(f"Map generation failed for {loc['name']}: {map_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Map generation error for {loc['name']}: {e}")

        # Generate NPC portraits
        npcs = campaign_data.get("npcs", [])
        portrait_npcs = [n for n in npcs if n.get("portrait_needed")]

        if portrait_npcs:
            logger.info(f"Generating {len(portrait_npcs)} NPC portrait(s)...")
            portraits_dir = output_dir / "portraits"
            portraits_dir.mkdir(exist_ok=True)

            for npc in portrait_npcs:
                prompt = npc.get("description", f"{npc.get('name', 'NPC')} portrait")
                portrait_file = portraits_dir / f"portrait_{npc['name'].replace(' ', '_').lower()}.png"

                try:
                    portrait_result = await map_generator.generate_portrait(
                        prompt=prompt,
                        output_dir=portraits_dir,
                    )
                    if portrait_result["status"] == "success":
                        results["portraits"].append({
                            "npc": npc["name"],
                            "file": portrait_result["output_file"],
                            "provider": portrait_result.get("provider", "unknown"),
                        })
                        npc["portrait_file"] = Path(portrait_result["output_file"]).name
                    else:
                        logger.warning(f"Portrait generation failed for {npc['name']}: {portrait_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Portrait generation error for {npc['name']}: {e}")

        results["total_maps"] = len(results["maps"])
        results["total_portraits"] = len(results["portraits"])
        return results

    # ─── Phase 5: Deploy to FoundryVTT ──────────────────────────────────────

    async def deploy_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Deploy campaign elements to the connected FoundryVTT world.

        Deploys:
        - Scenes (via relay)
        - NPCs/Actors (via relay)
        - Journal entries (via relay)
        - Quest logs (via relay)
        - Loot tables (via relay)
        """
        deployment = {
            "scenes": [],
            "npcs": [],
            "journal_entries": [],
            "quest_logs": [],
            "loot_tables": [],
            "status": "complete",
        }

        # Deploy NPCs as Actors
        npcs = campaign_data.get("npcs", [])
        if npcs:
            logger.info(f"Deploying {len(npcs)} NPCs to FoundryVTT...")
            for npc in npcs:
                try:
                    npc_data = {
                        "name": npc["name"],
                        "type": npc.get("role", "npc"),
                        "alignment": npc.get("alignment", "unknown"),
                        "description": npc.get("description", ""),
                        "stat_block": npc.get("stat_block", ""),
                        "faction": npc.get("faction", ""),
                    }
                    if npc.get("portrait_file"):
                        npc_data["portrait"] = npc["portrait_file"]
                    result = await foundry_client._send("create-actor", **npc_data)
                    deployment["npcs"].append({
                        "name": npc["name"],
                        "uuid": result.get("data", {}).get("uuid", result.get("uuid", "")),
                        "status": "created",
                    })
                except Exception as e:
                    logger.warning(f"Failed to create actor {npc['name']}: {e}")
                    deployment["npcs"].append({
                        "name": npc["name"],
                        "status": "failed",
                        "error": str(e),
                    })

        # Deploy Journal Entries
        journal_entries = campaign_data.get("journal_entries", [])
        if journal_entries:
            logger.info(f"Deploying {len(journal_entries)} journal entries to FoundryVTT...")
            for entry in journal_entries:
                try:
                    journal_data = {
                        "title": entry["title"],
                        "body": entry.get("body", ""),
                        "type": entry.get("type", "note"),
                        "act": entry.get("act", 1),
                        "visible_to_players": entry.get("visible_to_players", True),
                    }
                    result = await foundry_client._send("create-journal", **journal_data)
                    deployment["journal_entries"].append({
                        "title": entry["title"],
                        "uuid": result.get("data", {}).get("uuid", result.get("uuid", "")),
                        "status": "created",
                    })
                except Exception as e:
                    logger.warning(f"Failed to create journal entry {entry['title']}: {e}")
                    deployment["journal_entries"].append({
                        "title": entry["title"],
                        "status": "failed",
                        "error": str(e),
                    })

        # Deploy Quest Logs
        quest_logs = campaign_data.get("quest_logs", [])
        if quest_logs:
            logger.info(f"Deploying {len(quest_logs)} quest logs to FoundryVTT...")
            for quest in quest_logs:
                try:
                    quest_data = {
                        "id": quest.get("id", f"quest_{int(time.time())}"),
                        "title": quest["title"],
                        "type": quest.get("type", "main"),
                        "description": quest.get("description", ""),
                        "objectives": quest.get("objectives", []),
                        "rewards": quest.get("rewards", []),
                        "act": quest.get("act", 1),
                        "status": quest.get("status", "not-started"),
                    }
                    result = await foundry_client._send("create-quest", **quest_data)
                    deployment["quest_logs"].append({
                        "title": quest["title"],
                        "id": quest.get("id", ""),
                        "status": "created",
                    })
                except Exception as e:
                    logger.warning(f"Failed to create quest {quest['title']}: {e}")
                    deployment["quest_logs"].append({
                        "title": quest["title"],
                        "status": "failed",
                        "error": str(e),
                    })

        # Deploy Loot Tables
        loot_tables = campaign_data.get("loot_tables", [])
        if loot_tables:
            logger.info(f"Deploying {len(loot_tables)} loot tables to FoundryVTT...")
            for table in loot_tables:
                try:
                    table_data = {
                        "name": table["name"],
                        "description": table.get("description", ""),
                        "type": table.get("table_type", "treasure"),
                        "entries": table.get("entries", []),
                    }
                    result = await foundry_client._send("create-loot-table", **table_data)
                    deployment["loot_tables"].append({
                        "name": table["name"],
                        "uuid": result.get("data", {}).get("uuid", result.get("uuid", "")),
                        "status": "created",
                    })
                except Exception as e:
                    logger.warning(f"Failed to create loot table {table['name']}: {e}")
                    deployment["loot_tables"].append({
                        "name": table["name"],
                        "status": "failed",
                        "error": str(e),
                    })

        # Deploy Scenes (via relay)
        scenes = campaign_data.get("scenes", [])
        if scenes:
            logger.info(f"Deploying {len(scenes)} scenes to FoundryVTT...")
            for scene in scenes:
                try:
                    scene_map_file = scene.get("map_file", "")
                    scene_data = {
                        "name": scene["name"],
                        "type": scene.get("type", "scene"),
                        "description": scene.get("description", ""),
                        "act": scene.get("act", 1),
                        "map_file": scene_map_file if scene_map_file else None,
                        "map_scale": scene.get("map_scale", "room-scale"),
                        "token_count": scene.get("token_count", 0),
                        "lighting": scene.get("lighting", ""),
                        "atmosphere": scene.get("atmosphere", ""),
                    }
                    result = await foundry_client._send("create-scene", **scene_data)
                    deployment["scenes"].append({
                        "name": scene["name"],
                        "type": scene.get("type", "scene"),
                        "uuid": result.get("data", {}).get("uuid", result.get("uuid", "")),
                        "status": "created",
                    })
                except Exception as e:
                    logger.warning(f"Failed to create scene {scene['name']}: {e}")
                    deployment["scenes"].append({
                        "name": scene["name"],
                        "status": "failed",
                        "error": str(e),
                    })

        return deployment

    # ─── Master Pipeline ─────────────────────────────────────────────────────

    async def build_campaign(
        self,
        prompt: str,
        campaign_name: str = None,
        llm_client = None,
        foundry_client = None,
        vault_path: str = None,
        comfyui_url: str = None,
        omlx_url: str = None,
        omlx_model: str = "Z-Image-Turbo",
        omlx_api_key: str = None,
        on_progress: Callable = None,
    ) -> Dict[str, Any]:
        """Run the full campaign build pipeline.

        Phases:
        1. Scan FoundryVTT world
        2. Generate campaign structure via LLM
        3. Save to Obsidian vault
        4. Generate maps and portraits
        5. Deploy to FoundryVTT

        Args:
            prompt: User's campaign description
            campaign_name: Optional name for the campaign
            llm_client: httpx.AsyncClient for LLM calls
            foundry_client: Connected FoundryClient instance
            vault_path: Obsidian vault path
            comfyui_url: ComfyUI URL for map generation
            omlx_url: oMLX API URL for map generation
            omlx_model: oMLX model name
            omlx_api_key: oMLX API key (uses LLM_API_KEY if not set)
            on_progress: Optional callback(msg, step, detail)

        Returns:
            Result dict with full campaign info, asset info, and deployment status
        """
        result = {
            "status": "building",
            "campaign_name": campaign_name,
            "steps": [],
        }

        def progress(msg: str, step: str = "", detail: str = ""):
            result["steps"].append({"message": msg, "step": step, "detail": detail})
            if on_progress:
                try:
                    on_progress(msg, step, detail)
                except Exception as e:
                    logger.debug(f"Progress callback error: {e}")

        # Determine oMLX key
        api_key = omlx_api_key or self.settings.llm_api_key

        # ── Phase 1: Scan FoundryVTT world ──
        progress("🔍 Scanning connected FoundryVTT world...", step="scan")
        scan_result = None
        if foundry_client:
            try:
                scan_result = await self.scan_foundry_world(foundry_client)
                progress(
                    f"✅ Scan complete — {len(scan_result.get('scenes', []))} scenes, "
                    f"{len(scan_result.get('actors', []))} actors, "
                    f"{len(scan_result.get('users', []))} users found",
                    step="scan",
                    detail="world_scan",
                )
                result["scan"] = scan_result
            except Exception as e:
                progress(f"⚠️ Scan incomplete: {e}", step="scan")
                scan_result = {}
        else:
            progress("ℹ️ No FoundryVTT connection — running without scan", step="scan")
            scan_result = {}

        # ── Phase 2: Generate campaign via LLM ──
        progress("🏗️ Generating campaign structure via LLM...", step="generate")
        if llm_client is None:
            import httpx
            llm_client = httpx.AsyncClient(timeout=300)

        try:
            campaign_data = await self.generate_campaign_data(prompt, llm_client, scan_result)
            result["campaign_data"] = campaign_data
            progress(f"✅ Campaign '{campaign_data['campaign']['name']}' generated", step="generate", detail="complete")
        except Exception as e:
            result["status"] = "error"
            result["error"] = f"Campaign generation failed: {e}"
            logger.exception("Campaign generation failed")
            if llm_client:
                await llm_client.aclose()
            return result

        # ── Phase 3: Save to Obsidian vault ──
        progress("💾 Saving campaign to Obsidian vault...", step="vault")
        manifest = None
        try:
            manifest = await self.save_to_vault(campaign_data, vault_path)
            result["manifest"] = manifest
            progress(f"✅ Campaign saved to vault", step="vault", detail=manifest.get("campaign_folder", ""))
        except Exception as e:
            progress(f"⚠️ Vault save failed: {e}", step="vault")
            result["vault_error"] = str(e)

        # ── Phase 4: Generate assets (maps, portraits) ──
        progress("🎨 Generating maps and portraits...", step="assets")
        asset_output_dir = Path("./campaign_assets") / (campaign_data["campaign"]["name"].replace(" ", "_").lower() + "_maps")

        map_generator = None
        if comfyui_url or omlx_url:
            try:
                from campaign.map_generator import MapGenerator
                map_generator = MapGenerator(
                    comfyui_url=comfyui_url or settings.comfyui_url,
                    omlx_url=omlx_url or settings.omlx_url or "http://localhost:8800/v1/images/generations",
                    omlx_model=omlx_model,
                    omlx_api_key=api_key,
                    provider="auto",
                )
            except Exception as e:
                progress(f"⚠️ Map generator init failed: {e}", step="assets")

        asset_info = {"maps": [], "portraits": [], "status": "skipped"}
        if map_generator:
            try:
                asset_info = await self.generate_assets(campaign_data, map_generator, asset_output_dir)
                progress(
                    f"✅ Generated {asset_info['total_maps']} map(s) and {asset_info['total_portraits']} portrait(s)",
                    step="assets",
                    detail=f"maps={asset_info['total_maps']}, portraits={asset_info['total_portraits']}",
                )
            except Exception as e:
                progress(f"⚠️ Asset generation failed: {e}", step="assets")
                result["asset_error"] = str(e)
            await map_generator.close()

        result["assets"] = asset_info

        # ── Phase 5: Deploy to FoundryVTT ──
        progress("🚀 Deploying campaign to FoundryVTT...", step="deploy")
        deployment = None
        if foundry_client:
            try:
                deployment = await self.deploy_to_foundry(campaign_data, foundry_client, asset_info)
                total_deployed = (
                    len(deployment.get("scenes", []))
                    + len(deployment.get("npcs", []))
                    + len(deployment.get("journal_entries", []))
                    + len(deployment.get("quest_logs", []))
                    + len(deployment.get("loot_tables", []))
                )
                progress(
                    f"✅ Deployed {total_deployed} elements to FoundryVTT",
                    step="deploy",
                    detail=f"scenes={len(deployment.get('scenes', []))}, npcs={len(deployment.get('npcs', []))}, "
                           f"journal={len(deployment.get('journal_entries', []))}, quests={len(deployment.get('quest_logs', []))}, "
                           f"loot={len(deployment.get('loot_tables', []))}",
                )
            except Exception as e:
                progress(f"⚠️ Deployment failed: {e}", step="deploy")
                result["deploy_error"] = str(e)

        result["deployment"] = deployment

        # Clean up
        if llm_client and llm_client is not settings._default_httpx_client:
            await llm_client.aclose()

        result["status"] = "complete"
        result["campaign_ready"] = True
        result["ready_to_start"] = True

        return result

    # ─── Convenience wrapper ─────────────────────────────────────────────────

    async def build_campaign_convenience(
        self,
        prompt: str,
        campaign_name: str = None,
        foundry_client = None,
        on_progress: Callable = None,
    ) -> Dict[str, Any]:
        """Simplified builder using app-level settings."""
        import httpx

        llm_client = httpx.AsyncClient(timeout=300)

        return await self.build_campaign(
            prompt=prompt,
            campaign_name=campaign_name,
            llm_client=llm_client,
            foundry_client=foundry_client,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=getattr(settings, "omlx_url", None),
            omlx_model=getattr(settings, "omlx_model", "Z-Image-Turbo"),
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=on_progress,
        )
