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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from campaign.assets import resolve_uploaded_path, upload_image
from campaign.prologue import build_prologue_pages
from campaign.layout_generator import validate_scene_setup
import campaign.modules  # noqa: F401 — populates registry.MODULE_REGISTRY on import
from campaign.modules.registry import MODULE_REGISTRY, NpcContext, run_flag_hook, run_npc_hooks
from config import settings
from utils.path_safety import sanitize_filename

logger = logging.getLogger(__name__)


class CampaignOrchestrator:
    """Orchestrates the full campaign build pipeline."""

    def __init__(self, settings_obj=None):
        self.settings = settings_obj or settings

    # ─── LLM request helpers (thinking-suppression, endpoint) ───────────────

    def _chat_endpoint(self) -> str:
        """Chat-completions URL with the oMLX ?thinking=false query param.

        oMLX suppresses reasoning-token output server-side when this param is
        present (see llm/manager.py). Harmless on endpoints that ignore unknown
        query params (OpenAI, vLLM, etc.), so it is applied unconditionally.
        """
        base = self.settings.llm_base_url.rstrip("/")
        sep = "&" if "?" in base else "?"
        return f"{base}/chat/completions{sep}thinking=false"

    def _suppress_thinking(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Apply model-agnostic reasoning-token suppression to a chat payload.

        Sets `enable_thinking=False` (the API-level flag honored by Qwen3 and
        ignored by models that don't support it) and prepends the `/nothink`
        tokenizer directive to the LAST message. Previously this was gated on
        `"Qwen" in model`, which silently no-op'd for other local models (e.g.
        gemma-*), letting thinking/preamble tokens leak into and inflate the
        JSON output. Applying it for every model is safe: `/nothink` is inert
        text to a model that doesn't recognize it, and the JSON extractor strips
        any stray preamble regardless.
        """
        payload["enable_thinking"] = False
        msgs = payload.get("messages")
        if msgs:
            msgs[-1]["content"] = "/nothink\n" + msgs[-1]["content"]
        return payload

    # ─── Phase 1: Scan FoundryVTT world ─────────────────────────────────────

    async def scan_foundry_world(self, foundry_client) -> Dict[str, Any]:
        """Scan the currently connected FoundryVTT world.

        Detects scenes, actors, users, modules, and addon capabilities.
        Returns a catalog of existing content and available capabilities.
        """
        scan_result: Dict[str, Any] = {
            "scenes": [],
            "actors": [],
            "users": [],
            "rooms": [],
            "active_modules": {},   # {module_id: {title, version}}
            "capabilities": {},
        }

        # World info — active modules, system, users
        try:
            world_info = await foundry_client.get_world_info()
            for mod in world_info.get("modules", []):
                if mod.get("active"):
                    scan_result["active_modules"][mod["id"]] = {
                        "title": mod.get("title", mod["id"]),
                        "version": mod.get("version", ""),
                    }
            logger.info(f"Detected {len(scan_result['active_modules'])} active modules")
        except Exception as e:
            logger.warning(f"Failed to get world info: {e}")

        # Scan scenes
        try:
            scan_result["scenes"] = await foundry_client.get_scenes()
        except Exception as e:
            logger.warning(f"Failed to scan scenes: {e}")

        # Scan actors
        try:
            scan_result["actors"] = await foundry_client.get_actors()
        except Exception as e:
            logger.warning(f"Failed to scan actors: {e}")

        # Scan users
        try:
            scan_result["users"] = await foundry_client.get_users()
        except Exception as e:
            logger.warning(f"Failed to scan users: {e}")

        # Scan rooms
        try:
            scan_result["rooms"] = await foundry_client.get_rooms()
        except Exception as e:
            logger.warning(f"Failed to scan rooms: {e}")

        mods = scan_result["active_modules"]
        scan_result["capabilities"] = {
            "has_scenes": bool(scan_result["scenes"] or scan_result["rooms"]),
            "has_actors": bool(scan_result["actors"]),
            "has_users": bool(scan_result["users"]),
            # Animation
            "animated_tokens": "autoanimations" in mods and ("JB2A_DnD5e" in mods or "jb2a_patreon" in mods),
            # Combat
            "spell_automation": "midi-qol" in mods,
            "active_effects": "dae" in mods,
            # Items
            "item_piles": "item-piles" in mods,
            "loot_sheets": "lootsheet-simple" in mods,
            # Vision
            "vision_5e": "vision-5e" in mods,
            # Scenes
            "dynamic_soundscapes": "dynamic-soundscapes" in mods,
            "multi_floor_scenes": "levels" in mods,
            "fog_effects": "fog-weaver" in mods,
            "ingame_clock": "smalltime" in mods,
            "ingame_calendar": "foundryvtt-simple-calendar-reborn" in mods,
            # NPCs
            "npc_patrol": "patrol" in mods,
            "token_notes": "token-notes" in mods,
            # Quest / Narrative
            "progress_tracking": "progress-tracker" in mods,
            "quest_log": "rpgx-quest-log" in mods,
            # Language
            "polyglot": "polyglot" in mods,
            # Conditions / Traits
            "condition_tracking": "mmm" in mods,
        }

        return scan_result

    # ─── Phase 2: Generate campaign via LLM ─────────────────────────────────

    async def _post_and_parse_campaign_json(
        self,
        llm_client,
        endpoint: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """POST a campaign-generation request and parse the JSON response.

        Local/quantized models occasionally emit malformed JSON (a dropped
        colon, an unescaped quote) — a single bad turn used to be a hard
        failure with no recourse. Re-prompting is a genuine root-cause fix
        (not a guess-repair): the same request at temperature=0.85 has a real
        chance of coming back well-formed, with no risk of silently deploying
        a mis-repaired structure into the world (see json_repair evaluation
        in the 2026-07-05 investigation — it mangled this exact error class).
        """
        from campaign.generator import parse_campaign_response

        last_err: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=600)
            if resp.status_code != 200:
                raise Exception(f"LLM request failed: {resp.status_code} {resp.text[:500]}")

            raw_text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            try:
                return parse_campaign_response(raw_text)
            except json.JSONDecodeError as e:
                last_err = e
                if attempt < max_attempts:
                    logger.warning(
                        f"[LLM JSON] Attempt {attempt}/{max_attempts} failed to parse "
                        f"({e}) — retrying with a fresh generation..."
                    )
                else:
                    logger.error(
                        f"[LLM JSON] All {max_attempts} attempts failed to parse. Last error: {e}"
                    )
        raise last_err

    async def generate_campaign_data(
        self,
        prompt: str,
        llm_client,
        scan_result: Optional[Dict[str, Any]] = None,
        level_range: str = "1-5",
    ) -> Dict[str, Any]:
        """Generate complete campaign data using the LLM."""
        from campaign.generator import (
            generate_campaign_prompt,
            validate_campaign,
            campaign_count_checklist,
        )

        # Build enhanced prompt with scan results if available
        active_modules = scan_result.get("active_modules", {}) if scan_result else {}
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

        prompt_text = generate_campaign_prompt(
            prompt, active_modules=active_modules, level_range=level_range
        ) + scan_info

        # The count checklist goes in the USER turn (read last) so numeric
        # targets win on recency over the shape-template example in the system
        # prompt. See campaign_count_checklist docstring.
        user_content = f"{prompt}\n\n{campaign_count_checklist(level_range)}"
        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": user_content},
        ]

        endpoint = self._chat_endpoint()
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.campaign_gen_temperature,
            "max_tokens": 32768,
        }
        self._suppress_thinking(payload)

        campaign_data = await self._post_and_parse_campaign_json(llm_client, endpoint, headers, payload)
        campaign_data["generated_prompt"] = prompt
        campaign_data["generated_at"] = time.strftime("%Y-%m-%d %H:%M")

        # Count-compliance refill loop: a small quantized model often undershoots
        # array counts (anchoring on the shape-template example). Rather than
        # silently shipping a thin campaign, detect the shortfall and issue
        # targeted top-up calls to backfill only the short arrays.
        campaign_data = await self._refill_short_arrays(
            campaign_data, llm_client, endpoint, headers, level_range
        )

        # Validate
        warnings = validate_campaign(campaign_data, level_range=level_range)
        if warnings:
            for w in warnings:
                logger.warning(f"Campaign validation warning: {w}")
        campaign_data["validation_warnings"] = warnings

        return campaign_data

    async def _refill_short_arrays(
        self,
        campaign_data: Dict[str, Any],
        llm_client,
        endpoint: str,
        headers: Dict[str, str],
        level_range: str,
        max_rounds: int = 2,
    ) -> Dict[str, Any]:
        """Top up arrays that fell short of their level-scaled minimum counts.

        Issues up to `max_rounds` targeted generation calls, each asking only for
        the missing items and merging the returned arrays. Stops as soon as all
        minimums are met, or when a round makes no progress (guards against a
        model that simply can't produce more, avoiding an infinite/costly loop).
        """
        from campaign.generator import (
            campaign_count_shortfall,
            generate_refill_prompt,
            parse_campaign_response,
        )

        refill_keys = ("scenes", "npcs", "locations", "quest_logs", "quests", "encounters",
                       "loot_tables", "factions", "artifacts")

        for round_num in range(1, max_rounds + 1):
            shortfall = campaign_count_shortfall(campaign_data, level_range=level_range)
            if not shortfall:
                break
            logger.warning(
                f"[Refill] Round {round_num}/{max_rounds}: campaign short on "
                f"{ {k: v['got'] for k, v in shortfall.items()} } — requesting top-up."
            )
            refill_prompt = generate_refill_prompt(campaign_data, shortfall, level_range=level_range)
            messages = [
                {"role": "system", "content": "You output ONLY valid JSON. No prose, no code fences."},
                {"role": "user", "content": refill_prompt},
            ]
            payload = {
                "model": self.settings.model,
                "messages": messages,
                "temperature": self.settings.campaign_gen_temperature,
                "max_tokens": 32768,
            }
            self._suppress_thinking(payload)

            try:
                resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=600)
                if resp.status_code != 200:
                    logger.error(f"[Refill] LLM request failed: {resp.status_code} — aborting refill.")
                    break
                raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                extra = parse_campaign_response(raw)
            except Exception as e:
                logger.error(f"[Refill] Round {round_num} failed to parse top-up ({e}) — keeping what we have.")
                break

            # Merge: append new items to existing arrays. quest_logs/quests are
            # aliases — funnel both into whichever key the campaign already uses.
            made_progress = False
            for key in refill_keys:
                new_items = extra.get(key)
                if not isinstance(new_items, list) or not new_items:
                    continue
                target_key = key
                if key in ("quest_logs", "quests"):
                    target_key = "quest_logs" if "quest_logs" in campaign_data or "quests" not in campaign_data else "quests"
                campaign_data.setdefault(target_key, [])
                campaign_data[target_key].extend(new_items)
                made_progress = True
                logger.info(f"[Refill] Added {len(new_items)} item(s) to '{target_key}'.")

            if not made_progress:
                logger.warning(f"[Refill] Round {round_num} produced no usable items — stopping.")
                break

        final_shortfall = campaign_count_shortfall(campaign_data, level_range=level_range)
        if final_shortfall:
            logger.warning(
                f"[Refill] Still short after {max_rounds} round(s): "
                f"{ {k: v['got'] for k, v in final_shortfall.items()} }. Shipping best effort."
            )
        return campaign_data


    # ─── Phase 3: Save to Obsidian vault ────────────────────────────────────

    async def save_to_vault(
        self, campaign_data: Dict[str, Any], vault_path: str = None
    ) -> Dict[str, Any]:
        """Save campaign data to Obsidian vault."""
        if vault_path is None:
            vault_path = settings.campaign_vault_path

        from campaign.obsidian_sync import sync_campaign_to_vault

        manifest = await sync_campaign_to_vault(campaign_data, vault_path)
        return manifest

    # ─── Phase 4: Generate maps and portraits ────────────────────────────────

    def _build_scene_prompt(self, scene: Dict[str, Any]) -> str:
        """Build a rich map prompt from scene data when map_style is not provided.

        When the scene has scene_setup with walls/doors, extracts structural
        layout information (wall positions, door locations, room count)
        and includes it in the prompt so the generated map respects the
        physical layout even in text-only fallback mode.
        """
        scene_type = scene.get("type", "fantasy")
        scene_name = scene.get("name", "Scene")
        description = scene.get("description", "")
        atmosphere = scene.get("atmosphere", "")
        lighting = scene.get("lighting", "warm light")

        # Build a detailed prompt from available data
        type_perspectives = {
            "settlement": "top-down settlement interior",
            "tavern": "top-down tavern interior",
            "dungeon": "top-down dungeon",
            "cave": "top-down cavern",
            "forest": "top-down forest clearing",
            "temple": "top-down temple interior",
            "castle": "top-down castle room",
            "shop": "top-down shop interior",
            "crypt": "top-down crypt",
            "ruins": "top-down ancient ruins",
            "village": "isometric village scene",
            "city": "isometric city district",
            "wilderness": "aerial wilderness view",
        }
        perspective = type_perspectives.get(scene_type, f"top-down {scene_type}")

        # Extract key details from description
        prompt_parts = [perspective]

        # Add atmospheric details
        if atmosphere:
            prompt_parts.append(f"{atmosphere} atmosphere")
        if lighting:
            prompt_parts.append(f"{lighting} lighting")

        # Add description keywords (pick 2-3 strongest keywords)
        if description:
            # Extract nouns/adjectives from description
            key_words = [w for w in description.split() if len(w) > 4 and w[0].isupper()][:3]
            if key_words:
                prompt_parts.extend(key_words)

        # Add structural layout information from scene_setup (walls, doors)
        setup = scene.get("scene_setup", {})
        walls = setup.get("walls", [])
        doors = setup.get("doors", [])
        if walls or doors:
            wall_count = len(walls)
            door_count = len(doors)
            grid_w = setup.get("grid_width", 16)
            grid_h = setup.get("grid_height", 12)

            # Describe structural features
            layout_parts = []
            layout_parts.append(f"room layout with {wall_count} wall segments")

            # Classify wall types
            horizontal = [w for w in walls if w[1] == w[3]]  # horizontal segments
            vertical = [w for w in walls if w[0] == w[2]]  # vertical segments
            if horizontal:
                layout_parts.append(f"{len(horizontal)} horizontal walls")
            if vertical:
                layout_parts.append(f"{len(vertical)} vertical walls")

            # Classify doors
            for door in doors:
                door_type = door.get("door", 0)
                door_info = {0: "wall", 1: "open doorway", 2: "secret door"}.get(door_type, "door")
                layout_parts.append(door_info)

            layout_parts.append(f"grid {grid_w}x{grid_h}")
            layout = ", ".join(layout_parts)
            prompt_parts.insert(1, f"floorplan: {layout}")  # Insert after perspective

        # Add visual enhancement
        prompt_parts.append("detailed visual, fantasy illustration style")

        return ", ".join(prompt_parts)

    def _build_location_prompt(self, location: Dict[str, Any]) -> str:
        """Build a rich map prompt from location data when map_style is not provided."""
        location_type = location.get("type", "fantasy")
        location_name = location.get("name", "Location")
        description = location.get("description", "")
        key_features = location.get("key_features", [])

        # Build a detailed prompt from available data
        type_perspectives = {
            "settlement": "isometric village overview",
            "village": "isometric village overview",
            "town": "isometric town overview",
            "city": "isometric city overview",
            "dungeon": "top-down dungeon complex",
            "cave": "aerial cavern system",
            "cave_system": "aerial cavern system",
            "forest": "aerial forest with clearings",
            "wilderness": "aerial wilderness map",
            "castle": "isometric fortress overview",
            "fortress": "isometric fortress overview",
            "temple": "isometric temple grounds",
            "ruin": "top-down ancient ruin complex",
            "ruins": "top-down ancient ruin complex",
            "crypt": "top-down crypt complex",
            "tower": "isometric tower overview",
            "keep": "isometric keep overview",
        }
        perspective = type_perspectives.get(location_type, f"isometric {location_type} overview")

        # Build prompt
        prompt_parts = [perspective]

        # Add key features as visual elements
        if key_features:
            features = ", ".join(key_features[:4])  # Pick up to 4 features
            prompt_parts.append(features)

        # Add atmosphere from description
        if description:
            # Extract 2-3 key adjectives from description
            key_words = [w for w in description.split() if len(w) > 5 and w[0].isupper()][:2]
            if key_words:
                prompt_parts.extend(key_words)

        # Add visual style
        prompt_parts.append("detailed aerial view, fantasy cartography style, vibrant colors")

        return ", ".join(prompt_parts)

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
                prompt = scene.get("map_style", self._build_scene_prompt(scene))
                # Derive image dimensions from the scene's grid layout so that
                # wall coordinates (placed at GRID_PX per square) align with
                # what's visible in the generated image.
                setup = scene.get("scene_setup", {})
                gw = setup.get("grid_width", 16)
                gh = setup.get("grid_height", 12)
                gp = setup.get("grid_size_px", self.GRID_PX)
                img_w = gw * gp
                img_h = gh * gp
                # Store resolved image dimensions on the scene so deploy can use them
                scene["_map_width_px"] = img_w
                scene["_map_height_px"] = img_h
                scene["_grid_size_px"] = gp

                # ── Layout-guided generation (when scene has wall/door data) ──
                walls = setup.get("walls", [])
                doors = setup.get("doors", [])

                # Validate scene_setup geometry — fall back to procedural generation
                # if walls are disconnected, out-of-bounds, or otherwise invalid.
                scene_type = scene.get("type", "dungeon")
                scene["_scene_type"] = scene_type
                needs_fallback = False
                if walls or doors:
                    is_valid, val_warnings = validate_scene_setup(setup)
                    if not is_valid:
                        logger.warning(
                            f"[Layout] Scene '{scene['name']}' scene_setup failed validation "
                            f"({len(val_warnings)} issue(s)): {val_warnings} — activating procedural fallback"
                        )
                        needs_fallback = True

                if (walls or doors) and not needs_fallback:
                    logger.info(f"[Layout] Scene '{scene['name']}' has valid wall/door data — using ControlNet layout-guided generation")
                    # Set _output_dir so generate_layout_mask can save to the right place
                    scene["_output_dir"] = str(output_dir)
                    try:
                        if needs_fallback:
                            logger.info(f"[Layout] Using procedural fallback layout for '{scene['name']}'")
                            layout_mask = await map_generator.fallback_layout_for_scene(
                                scene=scene,
                                width=img_w,
                                height=img_h,
                                grid_size_px=gp,
                            )
                        else:
                            layout_mask = await map_generator.generate_layout_mask(
                                scene_setup=setup,
                                width=img_w,
                                height=img_h,
                                grid_size_px=gp,
                            )
                        if layout_mask and layout_mask.exists():
                            # Use layout-guided map generation (ControlNet)
                            # Derive map style from scene type for appropriate style prefix
                            scene_type = scene.get("type", "dungeon")
                            style_map = {
                                "dungeon": "dungeon",
                                "settlement": "fantasy_map",
                                "tavern": "dungeon",
                                "cave": "dungeon",
                                "temple": "dungeon",
                                "castle": "dungeon",
                                "crypt": "dungeon",
                                "ruins": "dungeon",
                                "village": "overworld",
                                "city": "overworld",
                                "wilderness": "overworld",
                            }
                            style = style_map.get(scene_type, "dungeon")
                            map_result = await map_generator.generate_map_controlnet(
                                prompt=prompt,
                                layout_image_path=str(layout_mask),
                                output_dir=output_dir,
                                width=img_w,
                                height=img_h,
                                style=style,
                            )
                        else:
                            # Fallback: no layout possible, use text-only generation
                            logger.info(f"[Layout] No layout data to mask — falling back to text-only generation (scene: '{scene['name']}')")
                            map_result = await map_generator.generate_map(
                                prompt=prompt,
                                output_dir=output_dir,
                                width=img_w,
                                height=img_h,
                            )
                    except Exception as e:
                        logger.warning(f"[Layout] Layout-guided generation failed for {scene['name']}: {e} — falling back to text-only")
                        try:
                            map_result = await map_generator.generate_map(
                                prompt=prompt,
                                output_dir=output_dir,
                                width=img_w,
                                height=img_h,
                            )
                        except Exception as fallback_e:
                            logger.warning(f"[Layout] Text-only fallback also failed for {scene['name']}: {fallback_e}")
                            map_result = {"status": "error", "error": str(fallback_e), "provider": "none"}
                else:
                    # No wall/door data — use text-only generation (existing behavior)
                    try:
                        map_result = await map_generator.generate_map(
                            prompt=prompt,
                            output_dir=output_dir,
                            width=img_w,
                            height=img_h,
                        )
                    except Exception as e:
                        logger.warning(f"Map generation error for {scene['name']}: {e}")
                        map_result = {"status": "error", "error": str(e), "provider": "none"}

                if map_result["status"] == "success":
                    results["maps"].append({
                        "scene": scene["name"],
                        "type": "scene_map",
                        "file": map_result["output_file"],
                        "provider": map_result.get("provider", "unknown"),
                    })
                    scene["map_file"] = Path(map_result["output_file"]).name
                    logger.info(f"[Layout] '{scene['name']}' map {'layout-guided' if (walls or doors) else 'text-only'} — {map_result.get('provider', 'unknown')}")
                else:
                    logger.warning(f"Map generation failed for {scene['name']}: {map_result.get('error', 'unknown')}")

        # Generate maps for locations
        locations = campaign_data.get("locations", [])
        location_maps = [l for l in locations if l.get("map_needed")]

        if location_maps:
            logger.info(f"Generating {len(location_maps)} location map(s)...")
            for loc in location_maps:
                prompt = loc.get("map_style", self._build_location_prompt(loc))
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
        # portrait_needed=None means "not explicitly set" — treat as True so campaigns
        # built with partial errors still get portraits on regenerate.
        npcs = campaign_data.get("npcs", [])
        portrait_npcs = [n for n in npcs if n.get("portrait_needed") is not False]

        if portrait_npcs:
            logger.info(f"Generating {len(portrait_npcs)} NPC portrait(s)...")
            portraits_dir = output_dir / "portraits"
            portraits_dir.mkdir(exist_ok=True)

            for npc in portrait_npcs:
                prompt = npc.get("description", f"{npc.get('name', 'NPC')} portrait")
                # Per-NPC filename: the raw ComfyUI output name is timestamp-based
                # and collided across NPCs (two actors ended up sharing one image).
                portrait_file = portraits_dir / f"portrait_{sanitize_filename(npc['name'].lower())}.png"

                try:
                    portrait_result = await map_generator.generate_portrait(
                        prompt=prompt,
                        output_dir=portraits_dir,
                    )
                    if portrait_result["status"] == "success":
                        src_file = Path(portrait_result["output_file"])
                        if src_file != portrait_file:
                            await asyncio.to_thread(src_file.replace, portrait_file)
                        results["portraits"].append({
                            "npc": npc["name"],
                            "file": str(portrait_file),
                            "provider": portrait_result.get("provider", "unknown"),
                        })
                        npc["portrait_file"] = portrait_file.name
                    else:
                        logger.warning(f"Portrait generation failed for {npc['name']}: {portrait_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Portrait generation error for {npc['name']}: {e}")

        # ── Generate prologue panel illustrations ──
        prologue = campaign_data.get("prologue")
        if prologue and isinstance(prologue, dict):
            panels = prologue.get("panels", [])
            vessel = prologue.get("vessel", "tome")
            if panels:
                logger.info(f"Generating {len(panels)} prologue panel illustration(s) (vessel: {vessel})...")
                prologue_dir = output_dir / "prologue"
                prologue_dir.mkdir(exist_ok=True)

                for i, panel in enumerate(panels):
                    if not isinstance(panel, dict):
                        continue
                    image_prompt = panel.get("image_prompt", "")
                    if not image_prompt:
                        logger.warning(f"Prologue panel {i+1} missing 'image_prompt', skipping")
                        continue

                    # Landscape aspect for journal image pages (~1344x768)
                    panel_filename = f"prologue_panel_{i+1:02d}.png"
                    panel_path = prologue_dir / panel_filename

                    try:
                        # Use dedicated prologue panel generation with vessel style
                        panel_result = await map_generator.generate_prologue_panel(
                            prompt=image_prompt,
                            vessel=vessel,
                            output_dir=prologue_dir,
                            width=1344,
                            height=768,
                        )
                        if panel_result.get("status") == "success":
                            src_file = Path(panel_result["output_file"])
                            if src_file != panel_path:
                                await asyncio.to_thread(src_file.replace, panel_path)
                            results.setdefault("prologue_panels", []).append({
                                "panel_index": i,
                                "title": panel.get("title", f"Panel {i+1}"),
                                "file": str(panel_path),
                                "provider": panel_result.get("provider", "unknown"),
                            })
                            # Stash the served path on the panel dict for later upload
                            panel["image_file"] = panel_path.name
                            logger.info(f"Prologue panel {i+1} generated: {panel_path.name}")
                        else:
                            logger.warning(f"Prologue panel {i+1} generation failed: {panel_result.get('error', 'unknown')}")
                    except Exception as e:
                        logger.warning(f"Prologue panel {i+1} generation error: {e}")

        results["total_maps"] = len(results["maps"])
        results["total_portraits"] = len(results["portraits"])
        results["total_prologue_panels"] = len(results.get("prologue_panels", []))
        return results

    # ─── Upload generated maps and set scene backgrounds ────────────────────

    async def upload_maps_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_output_dir: Path,
        safe_name: str,
    ) -> Dict[str, Any]:
        """Upload generated map PNGs to Foundry and set background_src on each scene dict.

        Must be called AFTER generate_assets() (which populates scene["map_file"]) and
        BEFORE deploy_to_foundry() (which reads scene["background_src"] when creating scenes).

        Returns summary: {uploaded: int, failed: int, errors: list}
        """
        summary: Dict[str, Any] = {"uploaded": 0, "failed": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — map upload skipped")
            return summary

        scenes = campaign_data.get("scenes", [])
        if not scenes:
            return summary

        # Sequential — concurrent uploads overwhelm the relay/Foundry WebSocket
        # and 408 out (same fix as regenerate_assets_for_campaign).
        for scene in scenes:
            map_file = scene.get("map_file")
            if not map_file:
                continue
            img_path = asset_output_dir / map_file
            if not img_path.exists():
                logger.warning(f"[Upload] Map file not found: {img_path}")
                summary["failed"] += 1
                summary["errors"].append(f"{scene.get('name', '?')}: file not found ({img_path.name})")
                continue
            result = await upload_image(
                foundry_client, img_path, f"ai-gm-maps/{safe_name}", map_file,
                f"ai-gm-maps/{safe_name}/{map_file}",
            )
            if result["ok"]:
                scene["background_src"] = result["src"]
                summary["uploaded"] += 1
                logger.info(f"[Upload] '{scene.get('name', '?')}' → {result['src']}")
            else:
                summary["failed"] += 1
                msg = f"{scene.get('name', '?')}: {result['error']}"
                summary["errors"].append(msg)
                logger.warning(f"[Upload] Map upload failed: {msg}")
        logger.info(
            f"[Upload] Map upload complete: {summary['uploaded']} uploaded, "
            f"{summary['failed']} failed"
        )
        return summary

    async def upload_portraits_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_output_dir: Path,
        safe_name: str,
    ) -> Dict[str, Any]:
        """Upload generated NPC portrait PNGs and set portrait_src on each NPC dict.

        Must run AFTER generate_assets() (which populates npc["portrait_file"]) and
        BEFORE deploy_to_foundry() (which reads npc["portrait_src"] when creating actors).
        """
        summary: Dict[str, Any] = {"uploaded": 0, "failed": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — portrait upload skipped")
            return summary

        # Sequential for the same 408 reason as maps.
        for npc in campaign_data.get("npcs", []):
            portrait_file = npc.get("portrait_file")
            if not portrait_file:
                continue
            img_path = asset_output_dir / "portraits" / portrait_file
            if not img_path.exists():
                continue
            result = await upload_image(
                foundry_client, img_path, f"ai-gm-portraits/{safe_name}", portrait_file,
                f"ai-gm-portraits/{safe_name}/{portrait_file}",
            )
            if result["ok"]:
                npc["portrait_src"] = result["src"]
                summary["uploaded"] += 1
                logger.info(f"[Upload] Portrait '{npc.get('name', '?')}' → {result['src']}")
            else:
                summary["failed"] += 1
                msg = f"{npc.get('name', '?')}: {result['error']}"
                summary["errors"].append(msg)
                logger.warning(f"[Upload] Portrait upload failed: {msg}")

        return summary

    async def upload_prologue_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_output_dir: Path,
        safe_name: str,
    ) -> Dict[str, Any]:
        """Upload generated prologue panel PNGs to Foundry.

        Must run AFTER generate_assets() (which populates panel["image_file"]) and
        BEFORE deploy_to_foundry() so the JournalEntry pages reference the correct src.
        """
        summary: Dict[str, Any] = {"uploaded": 0, "failed": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — prologue upload skipped")
            return summary

        prologue = campaign_data.get("prologue")
        if not prologue or not isinstance(prologue, dict):
            return summary

        panels = prologue.get("panels", [])
        if not panels:
            return summary

        # Sequential upload to avoid 408s
        for i, panel in enumerate(panels):
            image_file = panel.get("image_file")
            if not image_file:
                continue
            img_path = asset_output_dir / "prologue" / image_file
            if not img_path.exists():
                logger.warning(f"Prologue panel {i+1} file not found: {img_path}")
                continue
            result = await upload_image(
                foundry_client, img_path, f"ai-gm-prologue/{safe_name}", image_file,
                f"ai-gm-prologue/{safe_name}/{image_file}",
            )
            if result["ok"]:
                panel["image_src"] = result["src"]
                summary["uploaded"] += 1
                logger.info(f"[Upload] Prologue panel {i+1} → {result['src']}")
            else:
                summary["failed"] += 1
                msg = f"Prologue panel {i+1}: {result['error']}"
                summary["errors"].append(msg)
                logger.warning(f"[Upload] Prologue panel upload failed: {msg}")

        return summary

    async def _attach_map_to_scene(self, foundry_client, scene: dict, src: str, summary: dict) -> None:
        """Push a new background src into an ALREADY-DEPLOYED scene, by name.

        Only for regenerate — build-time scenes don't exist in Foundry yet,
        deploy_to_foundry creates them fresh reading scene["background_src"].
        """
        try:
            # FoundryVTT v14: Attach background via the Levels system
            logger.info(f"Fetching scene '{scene['name']}' to update levels...")
            current_scene = await foundry_client.get_scene_by_name(scene["name"])
            if not current_scene:
                msg = f"scene '{scene['name']}': scene not found in Foundry"
                logger.warning(msg)
                summary["errors"].append(msg)
                return

            logger.info(f"Current scene data keys: {list(current_scene.keys())}")

            # Preserve existing levels and only update the Base Level background.
            # This prevents loss of multi-level data from modules like Perfect Vision or Levels.
            existing_levels = current_scene.get("levels", [])
            bg_config = {
                "src": src,
                "offsetX": 0,
                "offsetY": 0,
                "scaleX": 1.0,
                "scaleY": 1.0,
            }
            if existing_levels:
                # Find and update the Base Level, or use the first level
                base_level_idx = next(
                    (i for i, l in enumerate(existing_levels) if l.get("name") == "Base Level"),
                    0
                )
                if base_level_idx < len(existing_levels):
                    existing_levels[base_level_idx]["background"] = bg_config
                levels_to_send = existing_levels
            else:
                # Fallback: create a single Base Level if none exists
                levels_to_send = [{"name": "Base Level", "background": bg_config}]

            logger.info(f"Updating scene with {len(levels_to_send)} level(s), Base Level background={src}")

            # Send the updated levels
            logger.info(f"Sending update-scene for '{scene['name']}'...")
            result = await foundry_client.update_scene(
                scene["name"],
                {"levels": levels_to_send}
            )
            logger.info(f"Update-scene result: {result}")
            if result and result.get("type") != "error":
                summary["scenes_attached"] += 1
            elif result and result.get("type") == "error":
                msg = f"scene '{scene['name']}': {result.get('error')}"
                logger.error(f"Scene attachment failed: {msg}")
                summary["errors"].append(msg)
            else:
                # Handle None or falsy result (network error, relay timeout)
                msg = f"scene '{scene['name']}': no response from Foundry (possible network timeout)"
                logger.error(f"Scene attachment failed: {msg}")
                summary["errors"].append(msg)
        except Exception as e:
            msg = f"scene '{scene['name']}': {type(e).__name__}: {e}"
            logger.exception(f"Scene attachment failed: {msg}")
            summary["errors"].append(msg)

    async def _attach_portrait_to_actor(
        self, foundry_client, npc: dict, src: str, npc_uuid_map: dict, summary: dict
    ) -> None:
        """Push a new portrait src into an ALREADY-DEPLOYED actor, by uuid or name.

        Only for regenerate — build-time NPCs don't exist in Foundry yet,
        deploy_to_foundry creates them fresh reading npc["portrait_src"].
        """
        try:
            npc_name = npc["name"]
            logger.info(f"Updating NPC '{npc_name}' with portrait {src}...")

            # Try using UUID from deployment state first (fastest path)
            result = None
            if npc_name in npc_uuid_map:
                npc_uuid = npc_uuid_map[npc_name]
                logger.info(f"Using cached UUID for '{npc_name}': {npc_uuid}")
                result = await foundry_client.update_entity(
                    uuid=npc_uuid,
                    data={"img": src}
                )
            else:
                # Fall back to searching by name
                result = await foundry_client.update_actor(
                    actor_name=npc_name,
                    actor_data={"img": src}
                )

            if result:
                logger.debug(f"Update response: {json.dumps(result, default=str)}")

            if result and result.get("type") != "error":
                logger.info(f"Updated NPC actor: {result}")
                summary["portraits_attached"] += 1
            elif result and result.get("type") == "error":
                logger.error(f"Failed to update portrait for NPC '{npc_name}': {result.get('error')}")
                summary["errors"].append(f"Portrait update failed for '{npc_name}': {result.get('error')}")
            else:
                logger.info(f"NPC '{npc_name}' not deployed in Foundry yet (not an error)")
        except KeyError as e:
            msg = f"NPC has missing field {e}"
            logger.exception(f"NPC update failed: {msg}")
            summary["errors"].append(msg)
        except Exception as e:
            msg = f"NPC '{npc.get('name', '?')}': {type(e).__name__}: {e}"
            logger.exception(f"NPC update failed: {msg}")
            summary["errors"].append(msg)

    # ─── Regenerate assets for an existing campaign ─────────────────────────

    async def regenerate_assets_for_campaign(
        self,
        campaign_name: str,
        foundry_client=None,
        comfyui_url: str = None,
        attach_to_foundry: bool = True,
        progress: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Regenerate maps/portraits for an already-built campaign.

        Loads the campaign from the vault, regenerates images with the current
        (improved) map generator, persists them, and — when Foundry is connected
        — uploads each map and attaches it as the background of the matching scene
        (updating existing scenes by name, so nothing is duplicated). Does NOT
        re-run the LLM; all existing NPCs/quests/story are preserved.
        """
        from campaign.map_generator import MapGenerator
        from campaign.vault import CampaignStore

        def _progress(msg: str, **kw):
            if progress:
                progress(msg, **kw)
            logger.info(msg)

        summary: Dict[str, Any] = {
            "campaign_name": campaign_name,
            "maps_generated": 0,
            "portraits_generated": 0,
            "scenes_attached": 0,
            "portraits_attached": 0,
            "errors": [],
            "status": "completed",
        }

        # ── Load campaign.json from the vault ──
        store = CampaignStore(campaign_name, self.settings.campaign_vault_path)
        if not store.exists:
            summary["status"] = "error"
            summary["errors"].append(f"Campaign '{campaign_name}' not found in vault")
            return summary
        campaign_data = await store.load(normalize=False)

        # ── Load deployment state (NPC UUIDs from last deployment) ──
        deployment_state = await store.load_deployment()
        npc_uuid_map = {  # name -> uuid
            npc_info["name"]: npc_info["uuid"]
            for npc_info in deployment_state.get("npcs", [])
            if npc_info.get("status") == "created" and npc_info.get("uuid")
        }
        if npc_uuid_map:
            logger.info(f"Loaded deployment state with {len(npc_uuid_map)} NPC UUIDs")

        # ── Generate images (improved SDXL workflow) ──
        safe_name = store.safe_name
        asset_output_dir = store.maps_dir

        map_generator = MapGenerator(
            comfyui_url=comfyui_url or getattr(self.settings, "comfyui_url", "http://127.0.0.1:18188"),
        )
        try:
            if not (await map_generator.health_check()).get("comfyui"):
                summary["status"] = "error"
                summary["errors"].append("ComfyUI is not reachable")
                return summary

            _progress("🎨 Regenerating maps and portraits...", step="assets")
            asset_info = await self.generate_assets(campaign_data, map_generator, asset_output_dir)
            summary["maps_generated"] = asset_info.get("total_maps", 0)
            summary["portraits_generated"] = asset_info.get("total_portraits", 0)

            # ── Upload maps + attach to existing Foundry scenes (by name) ──
            connected = bool(foundry_client and getattr(foundry_client, "is_connected", False))
            if attach_to_foundry and connected:
                async def _upload_and_attach_map(scene):
                    map_file = scene.get("map_file")
                    if not map_file:
                        return
                    img_path = asset_output_dir / map_file
                    if not img_path.exists():
                        return
                    result = await upload_image(
                        foundry_client, img_path, f"ai-gm-maps/{safe_name}", map_file,
                        f"ai-gm-maps/{safe_name}/{map_file}",
                    )
                    if not result["ok"]:
                        msg = f"scene '{scene.get('name', '?')}': {result['error']}"
                        logger.warning(f"File upload/processing failed: {msg}")
                        summary["errors"].append(msg)
                        return
                    scene["background_src"] = result["src"]
                    await self._attach_map_to_scene(foundry_client, scene, result["src"], summary)

                # Upload maps sequentially — parallel uploads overwhelm the relay/Foundry
                # WebSocket connection causing 408 timeouts on concurrent requests.
                scenes = campaign_data.get("scenes", [])
                for scene in scenes:
                    await _upload_and_attach_map(scene)

            # ── Upload portraits + attach to existing NPCs (by name) ──
            if attach_to_foundry and connected:
                npc_list = campaign_data.get("npcs", [])
                if npc_list:
                    _progress(f"Attaching {len(npc_list)} NPC portrait(s)...")

                    async def _upload_and_attach_portrait(npc):
                        portrait_file = npc.get("portrait_file")
                        if not portrait_file:
                            return
                        portrait_path = asset_output_dir / "portraits" / portrait_file
                        if not portrait_path.exists():
                            return
                        result = await upload_image(
                            foundry_client, portrait_path, f"ai-gm-portraits/{safe_name}", portrait_file,
                            f"ai-gm-portraits/{safe_name}/{portrait_file}",
                        )
                        if not result["ok"]:
                            msg = f"NPC '{npc.get('name', '?')}': {result['error']}"
                            logger.warning(f"Portrait upload/processing failed: {msg}")
                            summary["errors"].append(msg)
                            return
                        logger.info(f"Using portrait source: {result['src']}")
                        npc["portrait_src"] = result["src"]
                        await self._attach_portrait_to_actor(foundry_client, npc, result["src"], npc_uuid_map, summary)

                    # Upload portraits sequentially — same reason as the maps
                    # above: concurrent uploads overwhelm the relay/Foundry and
                    # 408 out (Elara's portrait was lost to exactly this).
                    for npc in npc_list:
                        await _upload_and_attach_portrait(npc)

                # ── Restore walls/lights/sounds on scenes missing them ──
                # Scenes deployed via the deploy endpoint historically never got
                # enriched; enrich_scenes skips categories that already exist.
                if deployment_state.get("scenes"):
                    try:
                        _progress("🏗️ Verifying scene walls, lights, and sounds...", step="enrich")
                        enrich_summary = await self.enrich_scenes(
                            campaign_data, foundry_client, deployment_state,
                        )
                        summary["scenes_enriched"] = enrich_summary.get("enriched", 0)
                        summary["errors"].extend(enrich_summary.get("errors", []))
                    except Exception as e:
                        logger.warning(f"Enrichment during regenerate failed: {e}")
                        summary["errors"].append(f"enrich: {e}")
            elif attach_to_foundry and not connected:
                summary["errors"].append(
                    "Foundry not connected — images regenerated and saved, but not attached to scenes/NPCs"
                )
        finally:
            await map_generator.close()

        # ── Persist updated references back to the vault ──
        await store.save(campaign_data)
        _progress(
            f"✅ Regenerated {summary['maps_generated']} map(s), "
            f"{summary['portraits_generated']} portrait(s), "
            f"attached {summary['scenes_attached']} scenes and {summary.get('portraits_attached', 0)} portraits to Foundry",
            step="assets",
        )
        return summary

    # ─── Phase 5: Deploy to FoundryVTT ──────────────────────────────────────

    async def deploy_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_info: Dict[str, Any],
        scan_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Deploy campaign elements to the connected FoundryVTT world.

        Uses active module information from scan_result to apply addon-specific
        flags and create enhanced entities (Item Piles, Playlists, animated NPCs, etc.)
        """
        mods: Dict[str, Any] = (scan_result or {}).get("active_modules", {})

        deployment: Dict[str, Any] = {
            "scenes": [],
            "npcs": [],
            "journal_entries": [],
            "quest_logs": [],
            "loot_tables": [],
            "loot_piles": [],
            "playlists": [],
            "calendar_events": [],
            "encounters": [],
            "encounter_actors": [],
            "status": "complete",
        }

        async def _create(entity_type: str, data: dict) -> dict:
            result = await foundry_client._send("create", entityType=entity_type, data=data)
            return result.get("data", result) if isinstance(result, dict) else {}

        def _uuid(result: dict) -> str:
            return result.get("uuid", result.get("_id", ""))

        # ── NPCs ──────────────────────────────────────────────────────────────
        npcs = campaign_data.get("npcs", [])
        if npcs:
            logger.info(f"Deploying {len(npcs)} NPCs...")
            for npc in npcs:
                if npc.get("existing_uuid"):
                    deployment["npcs"].append({
                        "name": npc["name"], "uuid": npc["existing_uuid"], "status": "linked",
                    })
                    continue
                try:
                    ctx = NpcContext(
                        npc=npc,
                        mods=mods,
                        flags={
                            "ai-gm": {
                                "faction": npc.get("faction", ""),
                                "stat_block": npc.get("stat_block", ""),
                                "npc_type": npc.get("npc_type", "combat"),
                            }
                        },
                        system={
                            "details": {
                                "alignment": npc.get("alignment", ""),
                                "biography": {"value": npc.get("description", "")},
                                "cr": npc.get("cr", 1),
                            },
                            "attributes": {
                                "hp": {
                                    "value": npc.get("hp", 10),
                                    "max": npc.get("hp", 10),
                                    "formula": npc.get("hp_formula", ""),
                                },
                                "ac": {
                                    "flat": npc.get("ac", 10),
                                    "calc": "natural",
                                },
                                "speed": {"value": npc.get("speed", 30), "units": "ft"},
                            },
                            "traits": {
                                "ci": {"value": npc.get("condition_immunities", [])},
                                "dv": {"value": npc.get("damage_vulnerabilities", [])},
                                "dr": {"value": npc.get("damage_resistances", [])},
                                "di": {"value": npc.get("damage_immunities", [])},
                                "languages": {
                                    "value": npc.get("languages", []),
                                    "custom": "",
                                },
                            },
                        },
                    )
                    run_npc_hooks(ctx)

                    data: Dict[str, Any] = {
                        "name": npc["name"],
                        "type": "npc",
                        "system": ctx.system,
                        "flags": ctx.flags,
                    }

                    # Attach the generated portrait (uploaded before deploy, or
                    # persisted in campaign.json from a previous build/regen) so
                    # redeployed NPCs keep their art instead of the mystery-man icon.
                    portrait_src = npc.get("portrait_src")
                    if portrait_src:
                        data["img"] = portrait_src
                        ctx.prototype_token.setdefault("texture", {})["src"] = portrait_src

                    if ctx.effects:
                        data["effects"] = ctx.effects
                    if ctx.items:
                        data["items"] = ctx.items
                    if ctx.prototype_token:
                        data["prototypeToken"] = ctx.prototype_token

                    result = await _create("Actor", data)
                    deployment["npcs"].append({"name": npc["name"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    npc_name = npc.get("name", "?")
                    logger.warning(f"Failed to create NPC {npc_name}: {e}")
                    deployment["npcs"].append({"name": npc_name, "status": "failed", "error": str(e)})

        # ── Journal Entries ───────────────────────────────────────────────────
        journal_entries = campaign_data.get("journal_entries", [])
        if journal_entries:
            logger.info(f"Deploying {len(journal_entries)} journal entries...")
            for entry in journal_entries:
                try:
                    entry_flags: Dict[str, Any] = {
                        "ai-gm": {"type": entry.get("type", "note"), "act": entry.get("act", 1)}
                    }
                    entry_flags.update(run_flag_hook("on_journal", entry, mods))
                    if entry.get("pdf_src"):
                        # Imported handout PDF — create a Foundry pdf-type page
                        data = {
                            "name": entry["title"],
                            "pages": [{
                                "name": entry["title"],
                                "type": "pdf",
                                "src": entry["pdf_src"],
                            }],
                            "flags": entry_flags,
                        }
                    else:
                        data = {
                            "name": entry["title"],
                            "pages": [{"name": entry["title"], "type": "text", "text": {"content": entry.get("body", ""), "format": 1}}],
                            "flags": entry_flags,
                        }
                    result = await _create("JournalEntry", data)
                    deployment["journal_entries"].append({"title": entry["title"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    entry_title = entry.get("title", "?")
                    logger.warning(f"Failed to create journal entry {entry_title}: {e}")
                    deployment["journal_entries"].append({"title": entry_title, "status": "failed", "error": str(e)})

        # ── Prologue JournalEntry (illustrated campaign introduction) ───────────
        prologue = campaign_data.get("prologue")
        if prologue and isinstance(prologue, dict):
            vessel = prologue.get("vessel", "tome")
            title = prologue.get("title", "Prologue")
            panels = prologue.get("panels", [])
            if panels:
                logger.info(f"Deploying prologue JournalEntry '{title}' ({len(panels)} panels, vessel: {vessel})...")
                try:
                    pages = build_prologue_pages(prologue)

                    prologue_flags: Dict[str, Any] = {
                        "ai-gm": {
                            "prologue": True,
                            "vessel": vessel,
                            "shown": False,
                        }
                    }
                    prologue_flags.update(run_flag_hook("on_prologue", prologue, mods))

                    data = {
                        "name": f"Prologue — {title}",
                        "pages": pages,
                        "flags": prologue_flags,
                    }
                    result = await _create("JournalEntry", data)
                    prologue_uuid = _uuid(result)
                    deployment.setdefault("prologue", {})["uuid"] = prologue_uuid
                    deployment.setdefault("prologue", {})["title"] = title
                    deployment.setdefault("prologue", {})["status"] = "created"
                    deployment["journal_entries"].append({"title": f"Prologue — {title}", "uuid": prologue_uuid, "status": "created"})
                except Exception as e:
                    logger.warning(f"Failed to create prologue JournalEntry: {e}")
                    deployment.setdefault("prologue", {})["status"] = "failed"
                    deployment.setdefault("prologue", {})["error"] = str(e)

        # ── Quest Logs ────────────────────────────────────────────────────────
        quest_logs = campaign_data.get("quest_logs", [])
        if quest_logs:
            logger.info(f"Deploying {len(quest_logs)} quest logs...")
            for quest in quest_logs:
                try:
                    objectives_html = "".join(
                        f"<li>{o.get('desc', o) if isinstance(o, dict) else o}"
                        + (f" <em>({o['check']})</em>" if isinstance(o, dict) and o.get("check") else "")
                        + "</li>"
                        for o in quest.get("objectives", [])
                    )
                    rewards_html = "".join(f"<li>{r}</li>" for r in quest.get("rewards", []))
                    body = (
                        f"<h2>{quest['title']}</h2>"
                        f"<p>{quest.get('description', '')}</p>"
                        f"<h3>Objectives</h3><ul>{objectives_html}</ul>"
                        f"<h3>Rewards</h3><ul>{rewards_html}</ul>"
                    )
                    quest_flags: Dict[str, Any] = {
                        "ai-gm": {
                            "quest_id": quest.get("id", ""),
                            "status": quest.get("status", "not-started"),
                            "act": quest.get("act", 1),
                        }
                    }
                    quest_flags.update(run_flag_hook("on_quest", quest, mods))
                    data = {
                        "name": f"[Quest] {quest['title']}",
                        "pages": [{"name": quest["title"], "type": "text", "text": {"content": body, "format": 1}}],
                        "flags": quest_flags,
                    }
                    result = await _create("JournalEntry", data)
                    deployment["quest_logs"].append({"title": quest["title"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    quest_title = quest.get("title", "?")
                    logger.warning(f"Failed to create quest {quest_title}: {e}")
                    deployment["quest_logs"].append({"title": quest_title, "status": "failed", "error": str(e)})

        # ── Loot Tables (RollTable) + Item Piles ─────────────────────────────
        loot_tables = campaign_data.get("loot_tables", [])
        if loot_tables:
            logger.info(f"Deploying {len(loot_tables)} loot tables...")
            for table in loot_tables:
                # Always create the RollTable
                try:
                    roll_results = []
                    cumulative = 0
                    for e in table.get("entries", []):
                        w = e.get("weight", 1)
                        roll_results.append({
                            "type": "text",
                            "text": e.get("name", ""),
                            "weight": w,
                            "range": [cumulative + 1, cumulative + w],
                            "drawn": False,
                        })
                        cumulative += w
                    data = {
                        "name": table["name"],
                        "description": table.get("description", ""),
                        "results": roll_results,
                        "formula": f"1d{max(cumulative, 1)}",
                    }
                    result = await _create("RollTable", data)
                    deployment["loot_tables"].append({"name": table["name"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    logger.warning(f"Failed to create loot table {table.get('name', '?')}: {e}")
                    deployment["loot_tables"].append({"name": table.get("name", "?"), "status": "failed", "error": str(e)})

                # Item Piles — also create a physical loot container actor
                item_piles_integration = MODULE_REGISTRY.get("item-piles")
                if "item-piles" in mods and item_piles_integration and item_piles_integration.on_loot_table:
                    try:
                        pile_actor = await item_piles_integration.on_loot_table(table, mods)
                        if pile_actor:
                            pile_result = await _create("Actor", pile_actor)
                            deployment["loot_piles"].append({"name": table["name"], "uuid": _uuid(pile_result), "status": "created"})
                    except Exception as e:
                        logger.warning(f"Failed to create Item Pile for {table.get('name', '?')}: {e}")
                        deployment["loot_piles"].append({"name": table.get("name", "?"), "status": "failed", "error": str(e)})

        # ── Scenes ────────────────────────────────────────────────────────────
        scenes = campaign_data.get("scenes", [])
        if scenes:
            logger.info(f"Deploying {len(scenes)} scenes...")
            for scene in scenes:
                if scene.get("existing_uuid"):
                    deployment["scenes"].append({
                        "name": scene["name"], "uuid": scene["existing_uuid"], "status": "linked",
                    })
                    continue
                try:
                    scene_flags: Dict[str, Any] = {
                        "ai-gm": {
                            "type": scene.get("type", "scene"),
                            "act": scene.get("act", 1),
                            "atmosphere": scene.get("atmosphere", ""),
                        }
                    }
                    scene_flags.update(run_flag_hook("on_scene", scene, mods))

                    data = {
                        "name": scene["name"],
                        "darkness": scene.get("darkness", 0.0),
                        "flags": scene_flags,
                    }
                    # Set scene canvas dimensions from the grid layout so that
                    # walls placed during enrichment (at grid_size_px per square)
                    # align with the generated background image.
                    gp = scene.get("_grid_size_px", self.GRID_PX)
                    setup = scene.get("scene_setup", {})
                    gw = setup.get("grid_width")
                    gh = setup.get("grid_height")
                    if gw and gh:
                        data["width"] = scene.get("_map_width_px", gw * gp)
                        data["height"] = scene.get("_map_height_px", gh * gp)
                        # ponytail: set padding=0 during creation so walls align correctly; caller can adjust after walls are placed
                        data["grid"] = {"size": gp, "padding": 0}
                        data["padding"] = 0  # Also set scene-level padding to 0 (separate from grid.padding)
                    # FoundryVTT v14: Scenes use a Levels system. Create with a default level.
                    # If we have a background image reference, attach it to the level.
                    background_src = scene.get("background_src")
                    bg_config = {}
                    if background_src:
                        bg_config = {
                            "src": background_src,
                            "offsetX": 0,
                            "offsetY": 0,
                            "scaleX": 1.0,
                            "scaleY": 1.0,
                        }
                    levels = [
                        {
                            "name": "Base Level",
                            "background": bg_config,
                        }
                    ]
                    data["levels"] = levels
                    result = await _create("Scene", data)
                    deployment["scenes"].append({"name": scene["name"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    logger.warning(f"Failed to create scene {scene.get('name', '?')}: {e}")
                    deployment["scenes"].append({"name": scene.get("name", "?"), "status": "failed", "error": str(e)})

        # ── Calendar Events (Simple Calendar Reborn) ─────────────────────────
        if "foundryvtt-simple-calendar-reborn" in mods:
            calendar_events = campaign_data.get("calendar_events", [])
            if calendar_events:
                logger.info(f"Deploying {len(calendar_events)} calendar events...")
                deployment["calendar_events"] = []
                for event in calendar_events:
                    try:
                        body = (
                            f"<p>{event.get('description', '')}</p>"
                            f"<p><em>Type: {event.get('type', 'event')}</em></p>"
                        )
                        cal_flags: Dict[str, Any] = {"ai-gm": {"type": "calendar_event"}}
                        cal_flags.update(run_flag_hook("on_calendar_event", event, mods))
                        data = {
                            "name": event["title"],
                            "pages": [{"name": event["title"], "type": "text", "text": {"content": body, "format": 1}}],
                            "flags": cal_flags,
                        }
                        result = await _create("JournalEntry", data)
                        deployment["calendar_events"].append({"title": event["title"], "uuid": _uuid(result), "status": "created"})
                    except Exception as e:
                        logger.warning(f"Failed to create calendar event {event.get('title', '?')}: {e}")
                        deployment["calendar_events"].append({"title": event.get("title", "?"), "status": "failed", "error": str(e)})

        # ── Playlists (Dynamic Soundscapes) ───────────────────────────────────
        if "dynamic-soundscapes" in mods or "moulinette-soundboards" in mods:
            playlists = campaign_data.get("playlists", [])
            if playlists:
                logger.info(f"Deploying {len(playlists)} playlists...")
                for pl in playlists:
                    try:
                        pl_flags: Dict[str, Any] = {
                            "ai-gm": {"scene": pl.get("scene", ""), "mood": pl.get("mood", "")},
                        }
                        pl_flags.update(run_flag_hook("on_playlist", pl, mods))
                        data = {
                            "name": pl["name"],
                            "mode": 1,       # sequential
                            "fade": 1000,
                            "description": pl.get("mood", ""),
                            "sounds": [],    # GM adds actual audio files via Foundry UI
                            "flags": pl_flags,
                        }
                        result = await _create("Playlist", data)
                        deployment["playlists"].append({"name": pl["name"], "uuid": _uuid(result), "status": "created"})
                    except Exception as e:
                        logger.warning(f"Failed to create playlist {pl.get('name', '?')}: {e}")
                        deployment["playlists"].append({"name": pl.get("name", "?"), "status": "failed", "error": str(e)})

        # ── Encounters ────────────────────────────────────────────────────────
        encounters = campaign_data.get("encounters", [])
        if encounters:
            logger.info(f"Deploying {len(encounters)} encounter(s)...")
            try:
                enc_results = await self.deploy_encounters(campaign_data, foundry_client, deployment, mods)
                deployment["encounters"] = enc_results
            except Exception as e:
                logger.warning(f"Encounter deployment failed: {e}")
                deployment["encounters"] = [{"status": "failed", "error": str(e)}]

        # ── Portraits for compendium-less placeholder monsters ─────────────────
        # Encounter monsters with no compendium match are flagged needs_portrait;
        # generate AI art for them (falls back to themed icons if ComfyUI is down).
        try:
            cname = campaign_data.get("campaign", {}).get("name") or "campaign"
            portrait_summary = await self._generate_placeholder_portraits(foundry_client, cname)
            deployment["placeholder_portraits"] = portrait_summary
        except Exception as e:
            logger.warning(f"Placeholder portrait pass failed: {e}")

        return deployment

    # ─── Phase 5c: Deploy pre-staged encounters ──────────────────────────────

    async def _ensure_monster_actor(
        self,
        foundry_client,
        name: str,
        cr: float = 1,
        hp: int = 10,
        ac: int = 10,
    ) -> Optional[str]:
        """Return the UUID of a world actor matching `name`, creating one if needed."""
        from campaign.monster_actor import ensure_monster_actor
        return await ensure_monster_actor(foundry_client, name, cr=cr, hp=hp, ac=ac)

    @staticmethod
    def _default_monster_icon(name: str) -> str:
        """Pick a guaranteed-present core Foundry icon for a portrait-less monster.

        Used only when ComfyUI is unreachable so a placeholder is never left
        with the blank mystery-man icon.
        """
        n = name.lower()
        undead = ("undead", "skeleton", "zombie", "wraith", "shadow", "ghost",
                  "ghoul", "lich", "specter", "spectre", "wight", "vampire")
        if any(k in n for k in undead):
            return "icons/svg/skull.svg"
        if any(k in n for k in ("fire", "flame", "demon", "devil", "fiend")):
            return "icons/svg/fire.svg"
        return "icons/svg/mystery-man.svg"

    async def _generate_placeholder_portraits(
        self,
        foundry_client,
        campaign_name: str,
        output_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Generate AI portraits for art-less placeholder monster actors.

        `ensure_monster_actor` flags placeholders created without compendium
        art (flags["ai-gm"].needs_portrait). This pass finds those actors,
        generates a ComfyUI portrait for each, uploads it, sets it as the
        actor img + token art, and clears the flag. If ComfyUI is unreachable,
        falls back to a themed core icon so the actor is never left blank.
        """
        summary: Dict[str, Any] = {"generated": 0, "fallback_icon": 0, "errors": []}
        if not foundry_client or not foundry_client.is_connected:
            return summary

        from foundry import scripts

        try:
            res = await foundry_client.execute_js(scripts.find_actors_needing_portraits())
            pending = res.get("result") if isinstance(res, dict) else None
            pending = pending if isinstance(pending, list) else []
        except Exception as e:
            summary["errors"].append(f"lookup: {e}")
            return summary

        if not pending:
            return summary

        logger.info(f"[Placeholder Portraits] {len(pending)} monster(s) need art")

        from campaign.map_generator import MapGenerator
        map_generator = MapGenerator(
            comfyui_url=getattr(self.settings, "comfyui_url", "http://127.0.0.1:18188"),
        )
        safe_name = sanitize_filename(campaign_name.lower())
        base_dir = output_dir or (Path("./campaign_assets") / safe_name)
        portraits_dir = base_dir / "portraits"
        portraits_dir.mkdir(parents=True, exist_ok=True)

        try:
            comfy_up = (await map_generator.health_check()).get("comfyui")
            if not comfy_up:
                logger.warning("[Placeholder Portraits] ComfyUI unreachable — using fallback icons")
            for actor in pending:
                name = actor.get("name", "Monster")
                actor_uuid = actor.get("uuid", "")
                if not actor_uuid:
                    continue
                src = None
                if comfy_up:
                    try:
                        prompt = (
                            f"fantasy TTRPG monster portrait of a {name}, "
                            f"head and shoulders, detailed, dramatic lighting, painterly"
                        )
                        pres = await map_generator.generate_portrait(prompt, portraits_dir)
                        if pres.get("status") == "success":
                            pfile = Path(pres["output_file"])
                            result = await upload_image(
                                foundry_client, pfile, f"ai-gm-portraits/{safe_name}", pfile.name,
                                f"ai-gm-portraits/{safe_name}/{pfile.name}",
                            )
                            if result["ok"]:
                                src = result["src"]
                            else:
                                summary["errors"].append(f"{name}: {result['error']}")
                    except Exception as e:
                        summary["errors"].append(f"{name}: {e}")

                if src:
                    summary["generated"] += 1
                else:
                    src = self._default_monster_icon(name)
                    summary["fallback_icon"] += 1

                try:
                    await foundry_client.update_entity(
                        uuid=actor_uuid,
                        data={
                            "img": src,
                            "prototypeToken": {"texture": {"src": src}},
                            "flags": {"ai-gm": {"needs_portrait": False}},
                        },
                    )
                except Exception as e:
                    summary["errors"].append(f"{name} update: {e}")
        finally:
            await map_generator.close()

        logger.info(
            f"[Placeholder Portraits] generated={summary['generated']} "
            f"icon_fallback={summary['fallback_icon']} errors={len(summary['errors'])}"
        )
        return summary

    def _wall_blocked_squares(self, scene_setup: dict) -> set:
        """Return a set of (grid_x, grid_y) squares that are fully interior to a wall segment.

        Wall segments are line segments — we mark both endpoint squares as
        "avoid" rather than computing full polygon intersection, which is
        sufficient to prevent tokens spawning directly inside thick walls.
        """
        blocked = set()
        for seg in scene_setup.get("walls", []):
            if len(seg) != 4:
                continue
            x0, y0, x1, y1 = seg
            # Mark endpoint squares
            blocked.add((int(x0), int(y0)))
            blocked.add((int(x1), int(y1)))
            # Mark squares along axis-aligned segments
            if x0 == x1:
                for y in range(int(min(y0, y1)), int(max(y0, y1)) + 1):
                    blocked.add((int(x0), y))
            elif y0 == y1:
                for x in range(int(min(x0, x1)), int(max(x0, x1)) + 1):
                    blocked.add((x, int(y0)))
        return blocked

    async def _real_wall_blocked_squares(self, foundry_client, grid_size: float) -> set:
        """Blocked-square set built from a scene's REAL Wall documents on the
        currently-active canvas, converting pixel wall endpoints to
        grid-square coordinates with the scene's real grid size.

        Unlike _wall_blocked_squares (which reads Pass 2's imagined
        scene_setup — meaningless geometry for a scene we didn't generate
        ourselves), this reflects the actual map a linked/reused scene
        already has, so fallback token placement doesn't spawn tokens
        inside real walls on someone else's pre-built map.
        """
        blocked: set = set()
        try:
            walls = await foundry_client.canvas_get("walls")
        except Exception as e:
            logger.warning(f"[Encounter] Could not fetch real walls: {e}")
            return blocked

        for wall in walls:
            c = wall.get("c") if isinstance(wall, dict) else None
            if not c or len(c) != 4 or not grid_size:
                continue
            x0, y0, x1, y1 = c
            gx0, gy0 = int(x0 // grid_size), int(y0 // grid_size)
            gx1, gy1 = int(x1 // grid_size), int(y1 // grid_size)
            blocked.add((gx0, gy0))
            blocked.add((gx1, gy1))
            if gx0 == gx1:
                for gy in range(min(gy0, gy1), max(gy0, gy1) + 1):
                    blocked.add((gx0, gy))
            elif gy0 == gy1:
                for gx in range(min(gx0, gx1), max(gx0, gx1) + 1):
                    blocked.add((gx, gy0))
        return blocked

    def _safe_fallback_positions(
        self,
        scene_setup: dict,
        blocked: set,
        count: int,
        start_offset: int = 0,
    ) -> list:
        """Return `count` open grid positions spread across the scene, skipping wall-blocked squares."""
        gw = scene_setup.get("grid_width", 16)
        gh = scene_setup.get("grid_height", 12)
        candidates = [
            (x, y)
            for x in range(1, gw - 1)
            for y in range(1, gh - 1)
            if (x, y) not in blocked
        ]
        # Evenly space picks across the candidate list
        step = max(1, len(candidates) // max(count, 1))
        return [candidates[(start_offset + i * step) % len(candidates)] for i in range(count)]

    async def deploy_encounters(
        self,
        campaign_data: dict,
        foundry_client,
        deployment: dict,
        mods: dict,
    ) -> list:
        """Phase 5c — place pre-staged encounter tokens on their linked scenes.

        For each encounter:
        - Switches to the linked scene (which has walls and map image from enrichment).
        - Finds or imports each monster actor from the compendium.
        - Places hidden tokens at the LLM-specified grid positions, falling back to
          open (non-wall-blocked) squares when placement coordinates are missing or unsafe.
        - Creates a GM-only JournalEntry "Encounter: <name>" with difficulty badge,
          trigger, environment notes, and tactical tips.

        Tokens are placed hidden=True so the GM reveals them when the encounter begins.
        Returns a list of per-encounter result dicts.
        """
        results: List[Dict[str, Any]] = []
        encounters = campaign_data.get("encounters", [])
        if not encounters:
            return results

        gs = self.GRID_PX  # pixels per grid square — valid for scenes WE created

        # Index scenes for fast wall/grid lookup
        scene_index: Dict[str, dict] = {s["name"]: s for s in campaign_data.get("scenes", [])}
        # "linked" scenes (reused from a pre-existing Foundry document, e.g. a
        # DDBImporter map) genuinely exist in the world just like "created"
        # ones — an encounter needs to be able to switch to and place tokens
        # on either. Excluding "linked" here silently dropped every encounter
        # whose linked_scene pointed at a reused scene.
        deployed_scene_names = {
            s["name"] for s in deployment.get("scenes", []) if s.get("status") in ("created", "linked")
        }
        linked_scene_names = {
            s["name"] for s in deployment.get("scenes", []) if s.get("status") == "linked"
        }

        for enc in encounters:
            enc_name = enc.get("name", "Unnamed Encounter")
            linked_scene = enc.get("linked_scene", "")

            # Fuzzy-match: if the LLM produced a scene name that doesn't exactly
            # match a deployed scene, try a case-insensitive substring match so
            # minor hallucinations (extra words, em-dash variants) still resolve.
            if linked_scene and linked_scene not in deployed_scene_names:
                linked_lower = linked_scene.lower()
                matched = None
                # Level 1: substring match (catches extra words / em-dash variants)
                for candidate in deployed_scene_names:
                    if linked_lower in candidate.lower() or candidate.lower() in linked_lower:
                        matched = candidate
                        break
                # Level 2: word-overlap score (catches total hallucinations)
                if not matched:
                    stop_words = {"the", "a", "an", "of", "in", "at", "on", "and", "or", "to"}
                    query_words = {w for w in linked_lower.split() if w not in stop_words and len(w) > 2}
                    best_score, best_candidate = 0, None
                    for candidate in deployed_scene_names:
                        cand_words = {w for w in candidate.lower().split() if w not in stop_words and len(w) > 2}
                        if not query_words or not cand_words:
                            continue
                        overlap = len(query_words & cand_words)
                        score = overlap / max(len(query_words), len(cand_words))
                        if score > best_score:
                            best_score, best_candidate = score, candidate
                    if best_score >= 0.25:
                        matched = best_candidate
                if matched:
                    logger.warning(
                        f"[Encounter] '{enc_name}': linked_scene '{linked_scene}' "
                        f"not found — fuzzy-matched to '{matched}'"
                    )
                    linked_scene = matched
                else:
                    logger.warning(
                        f"[Encounter] '{enc_name}': linked_scene '{linked_scene}' "
                        f"not found and no fuzzy match among {deployed_scene_names}"
                    )

            enc_result: Dict[str, Any] = {
                "name": enc_name,
                "scene": linked_scene,
                "tokens_placed": 0,
                "journal_created": False,
                "status": "ok",
                "errors": [],
            }

            # ── Token placement (only if scene was deployed) ──────────────────
            if linked_scene and linked_scene in deployed_scene_names:
                try:
                    await foundry_client.activate_scene_and_wait(linked_scene, timeout=7)
                except Exception as e:
                    enc_result["errors"].append(f"scene switch: {e}")
                    enc_result["status"] = "partial"

                scene_data = scene_index.get(linked_scene, {})
                scene_setup = scene_data.get("scene_setup", {})

                # A linked scene is a real pre-existing document (e.g. a
                # DDBImporter map) with its own real grid size, dimensions,
                # and walls — not what Pass 2 imagined. Using scene_setup's
                # hallucinated geometry here wouldn't just misplace tokens
                # (wrong pixel scale), it could spawn a "safe" fallback
                # token directly inside a real wall the campaign data never
                # knew existed. Fall back to the assumed values on any
                # lookup failure rather than raising — a slightly-off
                # placement beats an unhandled exception dropping the
                # encounter's tokens entirely.
                scene_gs = gs
                fallback_setup = scene_setup
                if linked_scene in linked_scene_names:
                    try:
                        real_scene = await foundry_client.get_scene_by_name(linked_scene)
                        real_grid_size = (real_scene or {}).get("grid", {}).get("size")
                        if real_grid_size:
                            scene_gs = real_grid_size
                            width = real_scene.get("width")
                            height = real_scene.get("height")
                            if width and height:
                                fallback_setup = {
                                    "grid_width": max(1, int(width // scene_gs)),
                                    "grid_height": max(1, int(height // scene_gs)),
                                }
                    except Exception as e:
                        logger.warning(
                            f"[Encounter] Could not fetch real scene data for linked "
                            f"scene '{linked_scene}', using defaults: {e}"
                        )
                    blocked = await self._real_wall_blocked_squares(foundry_client, scene_gs)
                else:
                    blocked = self._wall_blocked_squares(scene_setup)

                token_offset = 0  # stagger fallback positions across monster groups
                for monster_group in enc.get("monsters", []):
                    monster_name = monster_group.get("name", "Unknown")
                    compendium_search = monster_group.get("compendium_search", monster_name)
                    count = monster_group.get("count", 1)
                    disposition = monster_group.get("disposition", -1)
                    cr = monster_group.get("cr", 1)
                    hp = monster_group.get("hp", max(1, int(cr) * 7 + 3))
                    ac = monster_group.get("ac", 10 + min(int(cr), 5))
                    placements = monster_group.get("placement", [])

                    # Resolve fallback positions for tokens with no explicit placement
                    fallback_positions = self._safe_fallback_positions(
                        fallback_setup, blocked, count, start_offset=token_offset
                    )
                    token_offset += count

                    # Ensure actor exists in world
                    actor_uuid = await self._ensure_monster_actor(
                        foundry_client, compendium_search, cr=cr, hp=hp, ac=ac
                    )
                    actor_id = actor_uuid.split(".")[-1] if actor_uuid else None

                    # Track the actor UUID so teardown can delete it. Covers the
                    # cases the ai-gm flag misses: actors reused from a prior
                    # deploy and compendium imports created before flagging.
                    if actor_uuid:
                        enc_actors = deployment.setdefault("encounter_actors", [])
                        if not any(a.get("uuid") == actor_uuid for a in enc_actors):
                            enc_actors.append({"name": monster_name, "uuid": actor_uuid})

                    for i in range(count):
                        # Resolve grid position: explicit placement → fallback
                        if i < len(placements):
                            gx = placements[i].get("grid_x", fallback_positions[i][0])
                            gy = placements[i].get("grid_y", fallback_positions[i][1])
                            # Nudge off a wall-blocked square
                            if (gx, gy) in blocked and i < len(fallback_positions):
                                gx, gy = fallback_positions[i]
                        else:
                            gx, gy = fallback_positions[i]

                        # Convert grid square → pixel (top-left of square)
                        x_px = int(gx * scene_gs)
                        y_px = int(gy * scene_gs)

                        label = f"{monster_name} {i + 1}" if count > 1 else monster_name
                        token_data: Dict[str, Any] = {
                            "name": label,
                            "x": x_px,
                            "y": y_px,
                            "hidden": True,
                            "disposition": disposition,
                            "width": 1,
                            "height": 1,
                        }
                        if actor_id:
                            token_data["actorId"] = actor_id
                            token_data["actorLink"] = False

                        try:
                            await foundry_client.canvas_create("tokens", token_data)
                            enc_result["tokens_placed"] += 1
                            logger.info(
                                f"[Encounter] Placed '{label}' at grid ({gx},{gy}) "
                                f"= pixel ({x_px},{y_px}) on '{linked_scene}'"
                            )
                        except Exception as e:
                            enc_result["errors"].append(f"token '{label}': {e}")
                            enc_result["status"] = "partial"
            else:
                reason = "not deployed" if linked_scene else "no linked_scene"
                enc_result["errors"].append(f"token placement skipped ({reason})")
                enc_result["status"] = "partial"

            # ── GM-only encounter brief journal entry ─────────────────────────
            try:
                difficulty_color = {
                    "easy": "#2ecc71", "medium": "#f39c12",
                    "hard": "#e74c3c", "deadly": "#8e44ad",
                }.get(enc.get("difficulty", "medium"), "#e67e22")

                monster_rows = "".join(
                    f"<tr><td><strong>{m['name']}</strong></td>"
                    f"<td>×{m.get('count', 1)}</td>"
                    f"<td>CR {m.get('cr', '?')}</td>"
                    f"<td>HP {m.get('hp', '?')} / AC {m.get('ac', '?')}</td></tr>"
                    for m in enc.get("monsters", [])
                )
                reward_items = "".join(
                    f"<li>{r}</li>" for r in enc.get("rewards", [])
                )
                body = (
                    f'<h2 style="border-left:4px solid {difficulty_color};padding-left:8px">'
                    f'Encounter — {enc_name}</h2>'
                    f'<p><strong>Scene:</strong> {linked_scene}<br>'
                    f'<strong>Act:</strong> {enc.get("act", "?")}<br>'
                    f'<strong>Difficulty:</strong> '
                    f'<span style="color:{difficulty_color};font-weight:bold">'
                    f'{enc.get("difficulty", "medium").upper()}</span><br>'
                    f'<strong>XP Award:</strong> {enc.get("xp_award", 0)} XP</p>'
                    f'<p><em><strong>Trigger:</strong> {enc.get("trigger", "")}</em></p>'
                    f'<h3>Description</h3><p>{enc.get("description", "")}</p>'
                    f'<h3>Monsters</h3>'
                    f'<table><thead><tr><th>Name</th><th>Count</th><th>CR</th><th>Stats</th></tr></thead>'
                    f'<tbody>{monster_rows}</tbody></table>'
                    f'<h3>Environment &amp; Cover</h3><p>{enc.get("environment_notes", "")}</p>'
                    f'<h3>Tactical Notes (GM Only)</h3><p>{enc.get("tactical_notes", "")}</p>'
                    f'<h3>Rewards</h3><ul>{reward_items}</ul>'
                    f'<p><em>Tokens are pre-staged hidden on the scene. '
                    f'Reveal them when the encounter triggers.</em></p>'
                )
                journal_flags: Dict[str, Any] = {
                    "ai-gm": {
                        "type": "encounter_brief",
                        "act": enc.get("act", 1),
                        "linked_scene": linked_scene,
                        "difficulty": enc.get("difficulty", "medium"),
                    }
                }

                journal_flags.update(run_flag_hook("on_encounter_journal", enc, mods))
                journal_data = {
                    "name": f"[Encounter] {enc_name}",
                    "pages": [
                        {
                            "name": enc_name,
                            "type": "text",
                            "text": {"content": body, "format": 1},
                        }
                    ],
                    "flags": journal_flags,
                }
                je_result = await foundry_client._send(
                    "create", entityType="JournalEntry", data=journal_data
                )
                je_uuid = (je_result.get("data", {}) or {}).get("uuid", "")
                enc_result["journal_uuid"] = je_uuid
                enc_result["journal_created"] = True
            except Exception as e:
                enc_result["errors"].append(f"journal: {e}")
                enc_result["status"] = "partial"

            results.append(enc_result)
            logger.info(
                f"[Encounter] '{enc_name}': {enc_result['tokens_placed']} tokens placed, "
                f"journal={enc_result['journal_created']}, status={enc_result['status']}"
            )

        return results

    # ─── Phase 5b: Enrich deployed scenes with walls/lights/sounds ──────────

    # Pixels per grid square for all generated scenes.
    # 64px/sq means: 16×12 grid → 1024×768px, 20×15 → 1280×960px, 24×18 → 1536×1152px.
    # All clean multiples — image dimensions, scene canvas, and wall coords stay in sync.
    GRID_PX: int = 64

    def _scene_setup_to_canvas(
        self,
        setup: dict,
        grid_size: int = None,
    ) -> dict:
        """Convert a scene_setup block (grid-square coordinates) to Foundry canvas data.

        Returns a dict with keys: walls, lights, sounds, scene_config.
        All coordinates are converted from grid squares to pixels using grid_size
        (defaults to GRID_PX = 64).
        """
        gs = grid_size if grid_size is not None else self.GRID_PX

        # --- Walls ---
        foundry_walls = []
        for seg in setup.get("walls", []):
            if len(seg) == 4:
                x0, y0, x1, y1 = [v * gs for v in seg]
                foundry_walls.append({"c": [x0, y0, x1, y1], "move": 20, "sense": 20, "sound": 20, "door": 0, "ds": 0})

        # --- Doors (override or supplement wall segments) ---
        for door in setup.get("doors", []):
            c_raw = door.get("c", [])
            if len(c_raw) == 4:
                x0, y0, x1, y1 = [v * gs for v in c_raw]
                foundry_walls.append({
                    "c": [x0, y0, x1, y1],
                    "move": 20,
                    "sense": 20,
                    "sound": 20,
                    "door": door.get("door", 1),
                    "ds": door.get("ds", 0),
                })

        # --- Lights ---
        foundry_lights = []
        for light in setup.get("lights", []):
            x_px = light.get("x", 0) * gs
            y_px = light.get("y", 0) * gs
            foundry_lights.append({
                "x": x_px,
                "y": y_px,
                "config": {
                    "bright": light.get("bright", 20),
                    "dim": light.get("dim", 40),
                    "color": light.get("color", "#ff6600"),
                    "alpha": light.get("alpha", 0.5),
                    "angle": 360,
                },
            })

        # --- Sounds ---
        # Foundry AmbientSound.radius is in scene distance units (feet in D&D 5e),
        # NOT pixels. 1 grid square = 5 feet, so convert grid-square radius → feet.
        foundry_sounds = []
        for sound in setup.get("sounds", []):
            x_px = sound.get("x", 0) * gs
            y_px = sound.get("y", 0) * gs
            radius_sq = sound.get("radius", 15)
            radius_ft = radius_sq * 5  # grid squares → feet
            foundry_sounds.append({
                "x": x_px,
                "y": y_px,
                "path": sound.get("path", ""),
                "radius": radius_ft,
                "volume": sound.get("volume", 0.5),
                "repeat": True,
            })

        # --- Scene config (lighting/fog only — dimensions set at creation time) ---
        scene_config = {}
        if "darkness" in setup:
            scene_config["darkness"] = setup["darkness"]
        if "global_illumination" in setup:
            scene_config["globalLight"] = setup["global_illumination"]
        if "fog_exploration" in setup:
            scene_config["fogExploration"] = setup["fog_exploration"]
        if "token_vision" in setup:
            scene_config["tokenVision"] = setup["token_vision"]

        return {
            "walls": foundry_walls,
            "lights": foundry_lights,
            "sounds": foundry_sounds,
            "scene_config": scene_config,
        }

    async def enrich_scenes(
        self,
        campaign_data: dict,
        foundry_client,
        deployment: dict,
        on_progress: Optional[Callable] = None,
    ) -> dict:
        """Phase 5b — populate deployed scenes with walls, lights, sounds, and scene config.

        For each deployed scene that has a `scene_setup` block in campaign_data,
        switches to that scene and places all canvas elements. Falls back gracefully
        if a scene isn't found or the relay times out.

        Returns a summary dict: {enriched: int, skipped: int, errors: list}
        """
        summary = {"enriched": 0, "skipped": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — scene enrichment skipped")
            return summary

        # Build a fast name→uuid lookup from the deployment result
        deployed_scene_names = {
            s["name"] for s in deployment.get("scenes", []) if s.get("status") == "created"
        }

        for scene in campaign_data.get("scenes", []):
            scene_name = scene.get("name", "")
            setup = scene.get("scene_setup")
            if not setup:
                summary["skipped"] += 1
                logger.info(f"[Enrich] '{scene_name}' has no scene_setup — skipping")
                continue

            if scene_name not in deployed_scene_names:
                summary["skipped"] += 1
                logger.info(f"[Enrich] '{scene_name}' was not deployed — skipping")
                continue

            if on_progress:
                try:
                    on_progress(f"🏗️ Enriching scene: {scene_name}", step="enrich")
                except Exception:
                    pass

            # Use per-scene grid_size_px if the LLM specified one, else global GRID_PX
            grid_size = setup.get("grid_size_px", self.GRID_PX)
            canvas_data = self._scene_setup_to_canvas(setup, grid_size=grid_size)
            walls = canvas_data["walls"]
            lights = canvas_data["lights"]
            sounds = canvas_data["sounds"]
            scene_config = canvas_data["scene_config"]

            errors_this_scene = []

            # Switch to the scene
            try:
                await foundry_client.activate_scene_and_wait(scene_name, timeout=7)
            except Exception as e:
                logger.warning(f"[Enrich] Could not switch to '{scene_name}': {e}")
                errors_this_scene.append(f"scene switch: {e}")

            # Skip categories the scene already has — enrichment runs at build,
            # redeploy, and regenerate, and blindly re-creating walls/lights/
            # sounds would duplicate them.
            try:
                from foundry import scripts
                count_res = await foundry_client.execute_js(scripts.count_scene_placeables(scene_name))
                counts = count_res.get("result") if isinstance(count_res, dict) else None
                if isinstance(counts, dict):
                    if counts.get("walls", 0) > 0:
                        logger.info(f"[Enrich] '{scene_name}' already has {counts['walls']} walls — skipping walls")
                        walls = []
                    if counts.get("lights", 0) > 0:
                        lights = []
                    if counts.get("sounds", 0) > 0:
                        sounds = []
            except Exception as e:
                logger.debug(f"[Enrich] Could not read existing counts for '{scene_name}': {e}")

            # Apply scene config (darkness, fog, vision)
            if scene_config:
                try:
                    await foundry_client.configure_scene(scene_config)
                    logger.info(f"[Enrich] '{scene_name}': configured {list(scene_config.keys())}")
                except Exception as e:
                    logger.warning(f"[Enrich] Scene config failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"scene config: {e}")

            # Place walls
            if walls:
                try:
                    wall_res = await foundry_client.canvas_create("walls", walls)
                    # canvas_create doesn't always raise on failure — the relay
                    # can reply success:False without type:"error" (same class
                    # of bug as the token-move silent failure) — so check it.
                    if isinstance(wall_res, dict) and wall_res.get("success") is False:
                        raise RuntimeError(wall_res.get("error", "canvas_create returned success=False"))
                    logger.info(f"[Enrich] '{scene_name}': placed {len(walls)} walls")
                    # Padding stays 0 (set at scene creation): walls store
                    # absolute scene coordinates, so re-adding padding here
                    # shifted the background relative to the walls — the exact
                    # misalignment the padding=0 fix solved.
                except Exception as e:
                    logger.warning(f"[Enrich] Wall placement failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"walls: {e}")

            # Place lights
            if lights:
                try:
                    light_res = await foundry_client.canvas_create("lights", lights)
                    if isinstance(light_res, dict) and light_res.get("success") is False:
                        raise RuntimeError(light_res.get("error", "canvas_create returned success=False"))
                    logger.info(f"[Enrich] '{scene_name}': placed {len(lights)} lights")
                except Exception as e:
                    logger.warning(f"[Enrich] Light placement failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"lights: {e}")

            # Place sounds
            if sounds:
                try:
                    sound_res = await foundry_client.canvas_create("sounds", sounds)
                    if isinstance(sound_res, dict) and sound_res.get("success") is False:
                        raise RuntimeError(sound_res.get("error", "canvas_create returned success=False"))
                    logger.info(f"[Enrich] '{scene_name}': placed {len(sounds)} sounds")
                except Exception as e:
                    logger.warning(f"[Enrich] Sound placement failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"sounds: {e}")

            # Place trap tiles (Monk's Active Tiles enter-triggers). Consumes the
            # scene's `trap_tiles`; the AI GM resolves the save/damage when the
            # whispered trigger fires at play time.
            from campaign.trap_tiles import build_trap_tile_docs
            trap_docs = build_trap_tile_docs(setup.get("trap_tiles"), grid_px=grid_size)
            if trap_docs:
                try:
                    # Replace prior AI-GM trap tiles so redeploy doesn't duplicate.
                    clear_js = (
                        "const s=game.scenes.getName(" + json.dumps(scene_name) + ");"
                        "if(s){const ids=s.tiles.filter(t=>t.getFlag('aigm-trap','version'))"
                        ".map(t=>t.id);if(ids.length)await s.deleteEmbeddedDocuments('Tile',ids);}"
                        "return true;"
                    )
                    await foundry_client.execute_js(clear_js)
                    tile_res = await foundry_client.canvas_create("tiles", trap_docs)
                    if isinstance(tile_res, dict) and tile_res.get("success") is False:
                        raise RuntimeError(tile_res.get("error", "canvas_create returned success=False"))
                    logger.info(f"[Enrich] '{scene_name}': placed {len(trap_docs)} trap tile(s)")
                except Exception as e:
                    logger.warning(f"[Enrich] Trap tile placement failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"trap tiles: {e}")

            if errors_this_scene:
                summary["errors"].extend([f"'{scene_name}': {e}" for e in errors_this_scene])
                # Partial enrichment still counts
                summary["enriched"] += 1
            else:
                summary["enriched"] += 1
                logger.info(f"[Enrich] '{scene_name}' fully enriched")

        return summary

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
        omlx_model: str = "",
        omlx_api_key: str = None,
        on_progress: Callable = None,
        level_range: str = "1-5",
        campaign_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the full campaign build pipeline.

        Phases:
        1. Scan FoundryVTT world
        2. Generate campaign structure via LLM
        3. Save to Obsidian vault
        4. Generate maps and portraits
        5. Deploy to FoundryVTT
        5b. Enrich scenes — place walls, lights, sounds, configure fog/darkness

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

        # ── Phase 2: Generate campaign via LLM (or use pre-built data) ──
        if llm_client is None:
            import httpx
            llm_client = httpx.AsyncClient(timeout=300)

        if campaign_data is not None:
            progress("📦 Using pre-built campaign data (import mode)...", step="generate")
            if not isinstance(campaign_data, dict) or "campaign" not in campaign_data:
                raise Exception(
                    f"Pre-built campaign data is incomplete (missing 'campaign' key). "
                    f"Keys present: {list(campaign_data.keys()) if isinstance(campaign_data, dict) else type(campaign_data).__name__}"
                )
            result["campaign_data"] = campaign_data
            campaign_name = campaign_data.get("campaign", {}).get("name", campaign_name or "Unnamed")
            progress(f"✅ Campaign '{campaign_name}' loaded from import", step="generate", detail="import")
        else:
            progress("🏗️ Generating campaign structure via LLM...", step="generate")

        try:
            if campaign_data is None:
                campaign_data = await self.generate_campaign_data(prompt, llm_client, scan_result, level_range=level_range)
                if not isinstance(campaign_data, dict) or "campaign" not in campaign_data:
                    raise Exception(
                        f"LLM returned incomplete campaign structure (missing 'campaign' key). "
                        f"Keys present: {list(campaign_data.keys()) if isinstance(campaign_data, dict) else type(campaign_data).__name__}"
                    )
                result["campaign_data"] = campaign_data
                campaign_name = campaign_data.get("campaign", {}).get("name", "Unnamed")
                progress(f"✅ Campaign '{campaign_name}' generated", step="generate", detail="complete")

            # ── Phase 3: Save to Obsidian vault ──
            progress("💾 Saving campaign to Obsidian vault...", step="vault")
            manifest = await self.save_to_vault(campaign_data, vault_path)
            result["manifest"] = manifest
            progress(f"✅ Campaign saved to vault", step="vault", detail=manifest.get("campaign_folder", ""))

            # ── Phase 4: Generate assets (maps, portraits) ──
            progress("🎨 Generating maps and portraits...", step="assets")
            # Sanitize campaign name to prevent path traversal attacks
            safe_campaign_name = sanitize_filename(campaign_name.lower())
            asset_output_dir = Path("./campaign_assets") / (safe_campaign_name + "_maps")
            campaign_assets_dir = Path("./campaign_assets") / safe_campaign_name
            # Ensure campaign assets directory exists for storing deployment state
            await asyncio.to_thread(campaign_assets_dir.mkdir, parents=True, exist_ok=True)

            map_generator = None
            try:
                from campaign.map_generator import MapGenerator
                map_generator = MapGenerator(
                    comfyui_url=comfyui_url or getattr(settings, "comfyui_url", "http://127.0.0.1:18188"),
                    omlx_base_url=getattr(settings, "omlx_base_url", "http://localhost:8800"),
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

            # ── Phase 4b: Upload maps to Foundry and set scene backgrounds ──
            # Pre-placed map_files (import mode) must upload even when nothing
            # was AI-generated this run (total_maps == 0 with full matches).
            has_premade_maps = any(
                s.get("map_file") for s in campaign_data.get("scenes", [])
            )
            if foundry_client and (asset_info.get("total_maps", 0) > 0 or has_premade_maps):
                progress("📤 Uploading maps to FoundryVTT...", step="upload")
                try:
                    upload_summary = await self.upload_maps_to_foundry(
                        campaign_data,
                        foundry_client,
                        asset_output_dir,
                        safe_campaign_name,
                    )
                    progress(
                        f"✅ Uploaded {upload_summary['uploaded']} map(s) to Foundry",
                        step="upload",
                        detail=f"uploaded={upload_summary['uploaded']}, failed={upload_summary['failed']}",
                    )
                    if upload_summary["errors"]:
                        logger.warning(f"Map upload errors: {upload_summary['errors']}")
                    result["upload_summary"] = upload_summary
                except Exception as e:
                    progress(f"⚠️ Map upload failed: {e}", step="upload")
                    logger.exception("Map upload to Foundry failed")

            # ── Phase 4c: Upload portraits so deploy can attach them to actors ──
            has_premade_portraits = any(
                n.get("portrait_file") for n in campaign_data.get("npcs", [])
            )
            if foundry_client and (asset_info.get("total_portraits", 0) > 0 or has_premade_portraits):
                progress("📤 Uploading NPC portraits to FoundryVTT...", step="upload")
                try:
                    portrait_summary = await self.upload_portraits_to_foundry(
                        campaign_data,
                        foundry_client,
                        asset_output_dir,
                        safe_campaign_name,
                    )
                    progress(
                        f"✅ Uploaded {portrait_summary['uploaded']} portrait(s) to Foundry",
                        step="upload",
                        detail=f"uploaded={portrait_summary['uploaded']}, failed={portrait_summary['failed']}",
                    )
                    if portrait_summary["errors"]:
                        logger.warning(f"Portrait upload errors: {portrait_summary['errors']}")
                    result["portrait_upload_summary"] = portrait_summary
                except Exception as e:
                    progress(f"⚠️ Portrait upload failed: {e}", step="upload")
                    logger.exception("Portrait upload to Foundry failed")

            # ── Phase 4d: Upload prologue panel illustrations ──
            if foundry_client and asset_info.get("total_prologue_panels", 0) > 0:
                progress("📤 Uploading prologue panels to FoundryVTT...", step="upload")
                try:
                    prologue_summary = await self.upload_prologue_to_foundry(
                        campaign_data,
                        foundry_client,
                        asset_output_dir,
                        safe_campaign_name,
                    )
                    progress(
                        f"✅ Uploaded {prologue_summary['uploaded']} prologue panel(s) to Foundry",
                        step="upload",
                        detail=f"uploaded={prologue_summary['uploaded']}, failed={prologue_summary['failed']}",
                    )
                    if prologue_summary["errors"]:
                        logger.warning(f"Prologue upload errors: {prologue_summary['errors']}")
                    result["prologue_upload_summary"] = prologue_summary
                except Exception as e:
                    progress(f"⚠️ Prologue upload failed: {e}", step="upload")
                    logger.exception("Prologue upload to Foundry failed")

            # ── Phase 5: Deploy to FoundryVTT ──
            progress("🚀 Deploying campaign to FoundryVTT...", step="deploy")
            deployment = None
            if foundry_client:
                try:
                    deployment = await self.deploy_to_foundry(campaign_data, foundry_client, asset_info, scan_result=scan_result)
                    total_deployed = sum(
                        len(deployment.get(k, []))
                        for k in ("scenes", "npcs", "journal_entries", "quest_logs", "loot_tables", "loot_piles", "playlists", "calendar_events", "encounters")
                    )
                    progress(
                        f"✅ Deployed {total_deployed} elements to FoundryVTT",
                        step="deploy",
                        detail=(
                            f"scenes={len(deployment.get('scenes', []))}, "
                            f"npcs={len(deployment.get('npcs', []))}, "
                            f"journal={len(deployment.get('journal_entries', []))}, "
                            f"quests={len(deployment.get('quest_logs', []))}, "
                            f"loot_tables={len(deployment.get('loot_tables', []))}, "
                            f"loot_piles={len(deployment.get('loot_piles', []))}, "
                            f"playlists={len(deployment.get('playlists', []))}, "
                            f"calendar_events={len(deployment.get('calendar_events', []))}, "
                            f"encounters={len(deployment.get('encounters', []))}"
                        ),
                    )
                except Exception as e:
                    progress(f"⚠️ Deployment failed: {e}", step="deploy")
                    result["deploy_error"] = str(e)

            # ── Phase 5b: Enrich scenes with walls/lights/sounds ──
            if foundry_client and deployment:
                progress("🏗️ Enriching scenes with walls, lights, and sounds...", step="enrich")
                try:
                    enrich_summary = await self.enrich_scenes(
                        campaign_data,
                        foundry_client,
                        deployment,
                        on_progress=on_progress,
                    )
                    enriched = enrich_summary.get("enriched", 0)
                    skipped = enrich_summary.get("skipped", 0)
                    progress(
                        f"✅ Scene enrichment complete — {enriched} scene(s) enriched, {skipped} skipped",
                        step="enrich",
                        detail=f"enriched={enriched}, skipped={skipped}, errors={len(enrich_summary.get('errors', []))}",
                    )
                    result["scene_enrichment"] = enrich_summary
                except Exception as e:
                    progress(f"⚠️ Scene enrichment failed: {e}", step="enrich")
                    logger.exception("Scene enrichment failed")

            # Persist deployment state and re-persist campaign.json now that
            # asset references exist. The vault save in Phase 3 ran BEFORE
            # asset generation, so map_file / background_src / portrait_src
            # were lost — which meant redeploying this campaign later produced
            # scenes with no backgrounds and NPCs with no portraits.
            try:
                from campaign.vault import CampaignStore
                store = CampaignStore(campaign_name, vault_path)
                if deployment:
                    await store.save_deployment(deployment)
                    logger.info(f"Saved deployment state to {store.deployment_file}")
                await store.save(campaign_data)
                logger.info(f"Persisted asset references to {store.campaign_file}")
            except Exception as e:
                logger.warning(f"Could not persist campaign state: {e}")

            result["deployment"] = deployment
            result["status"] = "complete"
            result["campaign_ready"] = True
            result["ready_to_start"] = True

        except Exception as e:
            if campaign_data is None:
                result["status"] = "error"
                result["error"] = f"Campaign generation failed: {e}"
                logger.exception("Campaign generation failed")
            else:
                logger.exception("Pipeline error after campaign generation")
                if "error" not in result:
                    result["error"] = f"Pipeline error: {e}"
            return result

        finally:
            if llm_client:
                await llm_client.aclose()

        return result

    # ─── Campaign import ────────────────────────────────────────────────────

    async def import_campaign(
        self,
        source_path: str,
        campaign_name: str,
        llm_client=None,
        foundry_client=None,
        vault_path: str = None,
        comfyui_url: str = None,
        omlx_url: str = None,
        omlx_api_key: str = None,
        on_progress: Callable = None,
        level_range: str = "1-5",
        journal_pack: str = None,
    ) -> Dict[str, Any]:
        """Import a published campaign folder into the AI GM pipeline.

        Scans the folder, extracts lore from adventure PDFs (or, if
        journal_pack is given, from an already-imported Foundry JournalEntry
        compendium pack) via LLM, matches pre-made maps/tokens to
        scenes/NPCs, writes lore .md files into the vault, then delegates to
        build_campaign with pre-built campaign_data.

        Args:
            source_path: Path to the product folder (adventure PDFs + Maps/ + Tokens/).
            campaign_name: Name for the imported campaign.
            llm_client: httpx.AsyncClient for LLM calls.
            foundry_client: Connected FoundryClient instance.
            vault_path: Obsidian vault path.
            comfyui_url: ComfyUI URL.
            omlx_url: oMLX API URL.
            omlx_api_key: oMLX API key.
            on_progress: Optional callback(msg, step, detail).
            level_range: Target level range for the campaign.
            journal_pack: Name/collection id of a Foundry JournalEntry
                compendium pack (e.g. one created by DDBImporter) to read
                adventure text from instead of an adventure PDF. Requires a
                connected foundry_client with the execute-js scope enabled.
        """
        from campaign.importer import (
            scan_product_folder,
            extract_pdf_text,
            journal_entries_to_pages,
            chunk_pages,
            build_pass1_prompt,
            build_pass1_user,
            build_pass2_user,
            build_pass2_chapter_user,
            _PASS2_SYSTEM,
            build_pass3_user,
            _PASS3_SYSTEM,
            parse_pass3_response,
            match_maps_to_scenes,
            match_tokens_to_npcs,
            match_names_to_existing,
            filter_candidates_by_campaign_folder,
            prepare_handouts,
        )
        from campaign.generator import CAMPAIGN_GENERATOR_PROMPT, validate_campaign
        import httpx

        result: Dict[str, Any] = {
            "status": "importing",
            "campaign_name": campaign_name,
            "steps": [],
            "import_summary": {},
        }

        def progress(msg: str, step: str = "", detail: str = ""):
            result["steps"].append({"message": msg, "step": step, "detail": detail})
            logger.info(f"[Import] {msg}")
            if on_progress:
                try:
                    on_progress(msg, step, detail)
                except Exception:
                    pass

        if llm_client is None:
            llm_client = httpx.AsyncClient(timeout=300)

        try:
            # ── Step 1: Scan product folder ──
            progress("📂 Scanning product folder...", step="scan")
            scan = scan_product_folder(source_path)
            if scan.get("errors"):
                for err in scan["errors"]:
                    progress(f"  ⚠️ {err}", step="scan")
                result["status"] = "error"
                result["error"] = "; ".join(scan["errors"])
                return result
            progress(
                f"✅ Found {len(scan['adventure_pdfs'])} PDF(s), "
                f"{len(scan['maps'])} map(s), {len(scan['tokens'])} token(s), "
                f"{len(scan['handouts'])} handout(s)",
                step="scan",
            )

            # ── Step 2: Extract adventure text, grouped by chapter ──
            # Each group is (chapter_label, pages). journal_pack gives one
            # group per real book chapter/appendix (each is its own
            # JournalEntry); the PDF path has no chapter boundaries, so it's
            # a single group covering everything (unchanged behavior there).
            chapter_groups: List[Tuple[str, List[Tuple[int, str]]]] = []
            if journal_pack:
                progress(f"📖 Reading Foundry journal pack '{journal_pack}'...", step="extract")
                if foundry_client is None:
                    result["status"] = "error"
                    result["error"] = "A connected Foundry client is required to read journal_pack."
                    return result
                await self._wait_for_foundry_ready(foundry_client)
                entries = await self._fetch_journal_pack(foundry_client, journal_pack)
                raw_page_count = sum(len(e.get("pages", [])) for e in entries)
                for entry in entries:
                    pages = journal_entries_to_pages([entry])
                    if pages:
                        chapter_groups.append((entry.get("name", "Untitled"), pages))
                total_kept = sum(len(pages) for _, pages in chapter_groups)
                progress(
                    f"  📖 {len(entries)} journal entrie(s), {raw_page_count} raw page(s), "
                    f"{total_kept} page(s) kept after filtering, across {len(chapter_groups)} chapter(s)",
                    step="extract",
                )
                total_chars = sum(len(t) for _, pages in chapter_groups for _, t in pages)
                preview = (chapter_groups[0][1][0][1][:300] if chapter_groups and chapter_groups[0][1] else "")
                logger.info(
                    f"[Import] Journal pack '{journal_pack}': chapters={[name for name, _ in chapter_groups]}, "
                    f"total extracted chars={total_chars}, first page preview={preview!r}"
                )
            else:
                progress("📄 Extracting text from adventure PDFs...", step="extract")
                pdf_pages: List[Tuple[int, str]] = []
                for pdf in scan["adventure_pdfs"]:
                    pages = await asyncio.to_thread(extract_pdf_text, pdf)
                    pdf_pages.extend(pages)
                    progress(f"  📄 {Path(pdf).name}: {len(pages)} pages extracted", step="extract")
                if pdf_pages:
                    chapter_groups.append((campaign_name, pdf_pages))

            if not chapter_groups:
                result["status"] = "error"
                result["error"] = (
                    f"No text could be extracted from journal pack '{journal_pack}'."
                    if journal_pack
                    else "No text could be extracted from the adventure PDFs."
                )
                return result

            # ── Step 3-5: Per-chapter Pass 1 (extract) + Pass 2 (generate/merge) ──
            # One full generate-and-merge cycle per chapter instead of a
            # single whole-book call — a single Pass 2 call was capping
            # output at ~3-5 scenes regardless of how many real chapters/
            # pages were fed in: the verbose schema this system uses doesn't
            # fit a whole 7-chapter campaign in one response, and the model
            # defaults to a short-arc-sized result rather than exhaustively
            # enumerating everything. Each chapter gets its own full token
            # budget and is merged in (tagged with source_chapter), staying
            # strictly extract-only throughout — unlike extend_campaign_arc,
            # which deliberately invents/escalates for a NEW arc, chapters
            # here must never contradict or invent beyond their own notes.
            endpoint = self._chat_endpoint()
            headers = {
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            }

            campaign_data: Dict[str, Any] = {}
            all_notes: List[Tuple[str, str]] = []  # (chapter_label, notes)
            total_pages_extracted = 0
            total_chunks_processed = 0
            MERGE_SECTIONS = (
                "scenes", "npcs", "locations", "quest_logs", "encounters",
                "loot_tables", "loot_piles", "factions", "artifacts", "journal_entries",
            )

            for chapter_idx, (chapter_label, pages) in enumerate(chapter_groups, 1):
                progress(
                    f"📖 Chapter {chapter_idx}/{len(chapter_groups)}: {chapter_label}",
                    step="pass1",
                )
                chunks = chunk_pages(pages)
                total_pages_extracted += len(pages)
                total_chunks_processed += len(chunks)
                chapter_notes: List[str] = []
                for i, chunk_text in enumerate(chunks, 1):
                    payload: Dict[str, Any] = {
                        "model": self.settings.model,
                        "messages": [
                            {"role": "system", "content": build_pass1_prompt(chunk_text)},
                            {"role": "user", "content": build_pass1_user(chunk_text)},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 8192,
                    }
                    self._suppress_thinking(payload)
                    resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=600)
                    resp.raise_for_status()
                    notes = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    chapter_notes.append(notes)
                    progress(f"  📝 {chapter_label}: chunk {i}/{len(chunks)} notes extracted", step="pass1")

                chapter_combined_notes = "\n\n---\n\n".join(chapter_notes)
                all_notes.append((chapter_label, chapter_combined_notes))

                progress(f"🏗️ {chapter_label}: generating campaign content...", step="pass2")
                if not campaign_data:
                    pass2_payload: Dict[str, Any] = {
                        "model": self.settings.model,
                        "messages": [
                            {"role": "system", "content": _PASS2_SYSTEM + "\n\n" + CAMPAIGN_GENERATOR_PROMPT},
                            {"role": "user", "content": build_pass2_user(
                                chapter_combined_notes, campaign_name, level_range,
                            )},
                        ],
                        "temperature": self.settings.campaign_gen_temperature,
                        "max_tokens": 32768,
                    }
                    self._suppress_thinking(pass2_payload)
                    campaign_data = await self._post_and_parse_campaign_json(
                        llm_client, endpoint, headers, pass2_payload,
                    )
                    for section in MERGE_SECTIONS:
                        for item in campaign_data.get(section, []):
                            item["source_chapter"] = chapter_label
                else:
                    existing_summary = {
                        "scenes": [s.get("name", "") for s in campaign_data.get("scenes", [])],
                        "NPCs": [n.get("name", "") for n in campaign_data.get("npcs", [])],
                        "locations": [l.get("name", "") for l in campaign_data.get("locations", [])],
                    }
                    chapter_payload: Dict[str, Any] = {
                        "model": self.settings.model,
                        "messages": [
                            {"role": "system", "content": _PASS2_SYSTEM + "\n\n" + CAMPAIGN_GENERATOR_PROMPT},
                            {"role": "user", "content": build_pass2_chapter_user(
                                chapter_combined_notes, campaign_name, level_range,
                                chapter_label, existing_summary,
                            )},
                        ],
                        "temperature": self.settings.campaign_gen_temperature,
                        "max_tokens": 32768,
                    }
                    self._suppress_thinking(chapter_payload)
                    chapter_data = await self._post_and_parse_campaign_json(
                        llm_client, endpoint, headers, chapter_payload,
                    )
                    for section in MERGE_SECTIONS:
                        campaign_data.setdefault(section, [])
                        for item in chapter_data.get(section, []):
                            item["source_chapter"] = chapter_label
                            campaign_data[section].append(item)

                progress(
                    f"  ✅ {chapter_label}: {len(campaign_data.get('scenes', []))} total scene(s) so far",
                    step="pass2",
                )

            combined_notes = "\n\n---\n\n".join(notes for _, notes in all_notes)

            # Validate but skip count-refill (counts come from the source)
            warnings = validate_campaign(campaign_data, level_range=level_range)
            for w in warnings:
                logger.warning(f"[Import] Validation: {w}")
            campaign_data["validation_warnings"] = warnings
            campaign_data["imported_from"] = source_path
            progress(
                f"✅ Campaign structure generated across {len(chapter_groups)} chapter(s) "
                f"({len(campaign_data.get('scenes', []))} scenes total)",
                step="pass2",
            )

            # ── Step 6: Pass 3 — Generate Worldbuilding + History ──
            progress("📚 Pass 3: Generating worldbuilding documents...", step="pass3")
            pass3_payload: Dict[str, Any] = {
                "model": self.settings.model,
                "messages": [
                    {"role": "system", "content": _PASS3_SYSTEM},
                    {"role": "user", "content": build_pass3_user(combined_notes)},
                ],
                "temperature": 0.5,
                "max_tokens": 16384,
            }
            self._suppress_thinking(pass3_payload)
            resp3 = await llm_client.post(endpoint, headers=headers, json=pass3_payload, timeout=600)
            resp3.raise_for_status()
            pass3_text = resp3.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            wb_md, hist_md = parse_pass3_response(pass3_text)
            progress("✅ Worldbuilding documents generated", step="pass3")

            # ── Step 6.5: Link to pre-existing Foundry documents ──
            # A DDBImporter sync pre-creates the whole book as world Actors
            # and Scenes (folders/subfolders) — reuse those instead of
            # generating duplicate NPCs/maps when names match.
            if foundry_client is not None:
                existing_scenes = filter_candidates_by_campaign_folder(
                    await self._fetch_world_document_index(foundry_client, "Scene"), campaign_name
                )
                existing_actors = filter_candidates_by_campaign_folder(
                    await self._fetch_world_document_index(foundry_client, "Actor"), campaign_name
                )

                scenes_all = campaign_data.get("scenes", [])
                scene_link = match_names_to_existing(
                    [s.get("name", "") for s in scenes_all], existing_scenes
                )
                # Semantic fallback for whatever fuzzy name matching missed —
                # content/context judgment catches cases like a generated
                # "Vogler — The Brass Crab" that should still link to an
                # existing "Map 3.1: Vogler" despite barely sharing any text.
                remaining_scenes = [
                    c for c in existing_scenes if c.get("uuid") not in scene_link["matched"].values()
                ]
                unmatched_scenes = [s for s in scenes_all if s.get("name") in scene_link["unmatched"]]
                semantic_scenes = await self._semantic_match_names(
                    llm_client, "scene", unmatched_scenes, remaining_scenes
                )
                for scene in scenes_all:
                    name = scene.get("name", "")
                    uuid = scene_link["matched"].get(name) or semantic_scenes.get(name)
                    if uuid:
                        scene["existing_uuid"] = uuid
                        scene["map_needed"] = False

                npcs_all = campaign_data.get("npcs", [])
                npc_link = match_names_to_existing(
                    [n.get("name", "") for n in npcs_all], existing_actors
                )
                remaining_actors = [
                    c for c in existing_actors if c.get("uuid") not in npc_link["matched"].values()
                ]
                unmatched_npcs = [n for n in npcs_all if n.get("name") in npc_link["unmatched"]]
                semantic_npcs = await self._semantic_match_names(
                    llm_client, "NPC", unmatched_npcs, remaining_actors
                )
                for npc in npcs_all:
                    name = npc.get("name", "")
                    uuid = npc_link["matched"].get(name) or semantic_npcs.get(name)
                    if uuid:
                        npc["existing_uuid"] = uuid

                progress(
                    f"🔗 Linked {len(scene_link['matched']) + len(semantic_scenes)} scene(s) "
                    f"({len(semantic_scenes)} via semantic match) and "
                    f"{len(npc_link['matched']) + len(semantic_npcs)} NPC(s) "
                    f"({len(semantic_npcs)} via semantic match) to pre-existing Foundry documents",
                    step="assets",
                )

            # ── Step 7: Match assets ──
            progress("🗺️ Matching maps to scenes...", step="assets")
            from campaign.vault import CampaignStore
            store = CampaignStore(campaign_name, vault_path)
            store.maps_dir.mkdir(parents=True, exist_ok=True)

            scenes = campaign_data.get("scenes", [])
            scene_names = [s.get("name", "") for s in scenes]
            # Published maps are often named after regions/locations rather than
            # individual scenes. Let each scene also match its containing
            # location's name so regional maps get picked up as a fallback.
            scene_aliases: Dict[str, List[str]] = {}
            for loc in campaign_data.get("locations", []):
                loc_name = loc.get("name", "")
                if not loc_name:
                    continue
                for sn in loc.get("scenes", []):
                    scene_aliases.setdefault(sn, []).append(loc_name)
            map_match = match_maps_to_scenes(
                scene_names,
                scan["maps"],
                store.maps_dir,
                scene_aliases=scene_aliases,
            )
            # Apply matches onto the scene dicts: pre-placed file + flags mean
            # generate_assets skips the scene and upload picks the file up.
            for scene in scenes:
                match = map_match["matched_scenes"].get(scene.get("name", ""))
                if not match:
                    scene.setdefault("map_needed", True)
                    continue
                scene["map_file"] = Path(match["map_file"]).name
                scene["map_needed"] = False
                # Grid from the real image dimensions; empty walls/lights/sounds —
                # hallucinated walls won't align with professional maps, and
                # enrich_scenes no-ops on empty lists.
                setup = scene.setdefault("scene_setup", {})
                setup["grid_width"] = match["grid_width"]
                setup["grid_height"] = match["grid_height"]
                setup["grid_size_px"] = match["grid_size_px"]
                setup["walls"] = []
                setup["doors"] = []
                setup["lights"] = []
                setup["sounds"] = []
                # Exact pixel dims so deploy sizes the canvas to the image.
                scene["_map_width_px"] = match["width_px"]
                scene["_map_height_px"] = match["height_px"]
                scene["_grid_size_px"] = match["grid_size_px"]
            progress(
                f"  🗺️ {len(map_match['matched_scenes'])} matched, "
                f"{len(map_match['unmatched_scenes'])} unmatched",
                step="assets",
            )

            npcs = campaign_data.get("npcs", [])
            npc_names = [n.get("name", "") for n in npcs]
            portraits_dir = store.maps_dir / "portraits"
            portraits_dir.mkdir(parents=True, exist_ok=True)
            token_match = match_tokens_to_npcs(
                npc_names,
                scan["tokens"],
                portraits_dir,
            )
            for npc in npcs:
                match = token_match["matched_npcs"].get(npc.get("name", ""))
                if not match:
                    continue
                # upload_portraits_to_foundry resolves <maps_dir>/portraits/<file>
                npc["portrait_file"] = Path(match["portrait_file"]).name
                npc["portrait_needed"] = False
            progress(
                f"  👤 {len(token_match['matched_npcs'])} token(s) matched, "
                f"{len(token_match['unmatched_npcs'])} unmatched",
                step="assets",
            )

            # Prepare handout journal entries
            handout_entries = prepare_handouts(scan["handouts"], campaign_data)
            if handout_entries:
                campaign_data.setdefault("journal_entries", []).extend(handout_entries)
                progress(f"  📜 {len(handout_entries)} handout(s) prepared", step="assets")

            # ── Step 8: Write lore .md files into vault ──
            progress("📝 Writing lore files to vault...", step="lore")
            store.folder.mkdir(parents=True, exist_ok=True)

            if wb_md:
                wb_path = store.folder / "Worldbuilding.md"
                await asyncio.to_thread(wb_path.write_text, wb_md, encoding="utf-8")
            if hist_md:
                hist_path = store.folder / "History.md"
                await asyncio.to_thread(hist_path.write_text, hist_md, encoding="utf-8")

            # Raw extraction notes as Lore/<NN> <chapter label>.md — one file
            # per chapter (rather than per arbitrary token-boundary chunk) so
            # they're actually browsable in Obsidian for a multi-chapter import.
            lore_dir = store.folder / "Lore"
            lore_dir.mkdir(exist_ok=True)
            for i, (chapter_label, notes) in enumerate(all_notes, 1):
                part_path = lore_dir / f"{i:02d} {sanitize_filename(chapter_label)}.md"
                await asyncio.to_thread(part_path.write_text, notes, encoding="utf-8")

            # Handout markdown files
            if handout_entries:
                handout_dir = store.folder / "Handouts"
                handout_dir.mkdir(exist_ok=True)
                for entry in handout_entries:
                    md_path = handout_dir / f"{sanitize_filename(entry['title'])}.md"
                    await asyncio.to_thread(
                        md_path.write_text,
                        f"# {entry['title']}\n\nSee attached PDF: {entry['pdf_file']}\n",
                        encoding="utf-8",
                    )

            progress(f"✅ Lore files written to vault", step="lore")

            # ── Step 9: Upload handout PDFs to Foundry ──
            if foundry_client and scan["handouts"]:
                progress("📤 Uploading handout PDFs to Foundry...", step="upload_handouts")
                for entry in handout_entries:
                    try:
                        pdf_path = Path(entry["pdf_src"])
                        pdf_bytes = await asyncio.to_thread(pdf_path.read_bytes)
                        upload_resp = await foundry_client.upload_file(
                            file_bytes=pdf_bytes,
                            path=f"campaigns/{store.safe_name}/handouts",
                            filename=entry["pdf_file"],
                            mime_type="application/pdf",
                        )
                        # Update pdf_src to the Foundry-relative path
                        saved_path = upload_resp.get("path", entry["pdf_src"])
                        entry["pdf_src"] = saved_path
                        progress(f"  📜 Uploaded {entry['pdf_file']}", step="upload_handouts")
                    except Exception as e:
                        progress(f"  ⚠️ Failed to upload {entry['pdf_file']}: {e}", step="upload_handouts")

            # ── Step 10: Delegate to build_campaign ──
            progress("🚀 Running build pipeline with imported data...", step="build")
            build_result = await self.build_campaign(
                prompt=f"Imported campaign: {campaign_name}",
                campaign_name=campaign_name,
                llm_client=llm_client,
                foundry_client=foundry_client,
                vault_path=vault_path,
                comfyui_url=comfyui_url,
                omlx_url=omlx_url,
                omlx_api_key=omlx_api_key,
                on_progress=on_progress,
                level_range=level_range,
                campaign_data=campaign_data,
            )

            # Merge import summary into result
            build_result["import_summary"] = {
                "source_path": source_path,
                "pdfs_processed": len(scan["adventure_pdfs"]),
                "pages_extracted": total_pages_extracted,
                "chunks_processed": total_chunks_processed,
                "chapters_processed": len(chapter_groups),
                "maps_matched": sorted(map_match["matched_scenes"].keys()),
                "maps_unmatched": map_match["unmatched_scenes"],
                "tokens_matched": sorted(token_match["matched_npcs"].keys()),
                "tokens_unmatched": token_match["unmatched_npcs"],
                "handouts": [e["title"] for e in handout_entries],
                "warnings": map_match["warnings"] + token_match["warnings"],
            }
            build_result["steps"] = result["steps"] + build_result.get("steps", [])
            return build_result

        except Exception as e:
            logger.exception("Campaign import failed")
            result["status"] = "error"
            result["error"] = str(e)
            return result

    async def _wait_for_foundry_ready(self, foundry_client, timeout: float = 45.0) -> None:
        """Poll until Foundry's `game` object has finished loading the world.

        The relay reports "Foundry connected" as soon as the WebSocket
        handshake completes, not once `game.ready` is true — a headless
        session firing a heavy compendium query (like the journal-pack
        fetch) in that window got garbled/oversized replies that the
        browser's own WS layer killed with close code 1009 ("message too
        big"). Every failure observed fired within ~0.5s of "connected";
        the one success happened ~80s in. Waiting here is cheap insurance.
        """
        elapsed = 0.0
        while elapsed < timeout:
            try:
                res = await foundry_client.execute_js("return { ready: !!(game && game.ready) };")
                payload = res.get("result") if isinstance(res, dict) else res
                if isinstance(payload, dict) and payload.get("ready"):
                    return
            except Exception as e:
                logger.warning(f"[Import] game.ready poll failed, retrying: {e}")
            await asyncio.sleep(1.0)
            elapsed += 1.0
        logger.warning(f"[Import] Foundry did not report game.ready within {timeout}s; proceeding anyway")

    async def _fetch_world_document_index(self, foundry_client, doc_type: str) -> List[Dict[str, str]]:
        """List every Actor or Scene document already in the world (name,
        uuid, and containing folder name).

        A DDBImporter sync pre-creates the whole book as world Actors and
        Scenes (organized into folders/subfolders), so a campaign import
        shouldn't blindly generate a brand-new NPC/map for something that
        already exists. This is metadata only — no HTML/portrait/background
        data — so unlike the journal-pack fetch, a single call is safe
        regardless of how many documents the world has. The folder name is
        included because it's often the single strongest semantic signal
        available (e.g. a scene filed under "Chapter 3: When Home Burns" is
        very likely that chapter's content) without the cost/size risk of
        fetching each document's own description text.
        """
        collection = {"Actor": "game.actors", "Scene": "game.scenes"}[doc_type]
        js_query = f"""
        return {{ entries: {collection}.contents.map(d => ({{
            name: d.name, uuid: d.uuid, folder: d.folder?.name || ''
        }})) }};
        """
        res = await foundry_client.execute_js(js_query)
        payload = res.get("result") if isinstance(res, dict) else res
        if not isinstance(payload, dict) or payload.get("error"):
            logger.warning(
                f"[Import] Failed to list existing {doc_type} documents: "
                f"{payload.get('error') if isinstance(payload, dict) else 'query failed'}"
            )
            return []
        return payload.get("entries", [])

    async def _semantic_match_names(
        self,
        llm_client,
        kind: str,
        items: List[Dict[str, Any]],
        candidates: List[Dict[str, str]],
    ) -> Dict[str, str]:
        """LLM-driven fallback for items fuzzy name-matching (match_names_to_existing)
        didn't catch.

        Runs ONE batched LLM call (not one per item) asking it to judge
        content/context rather than text similarity — e.g. a generated
        'Vogler — The Brass Crab' scene should still link to an existing
        'Map 3.1: Vogler' despite barely sharing any text, because it's the
        same in-world location the adventure describes.

        Best-effort by design: any failure (LLM error, malformed JSON, a
        hallucinated candidate name) just yields no matches for this pass
        rather than raising — import_campaign already falls back to full
        generation for anything left unmatched, so this must never be able
        to break the import.
        """
        if not items or not candidates:
            return {}

        from campaign.importer import build_semantic_match_prompt, parse_semantic_match_response

        system, user = build_semantic_match_prompt(kind, items, candidates)
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        self._suppress_thinking(payload)
        try:
            endpoint = self._chat_endpoint()
            headers = {
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            }
            resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.warning(f"[Import] Semantic {kind} matching call failed: {e}")
            return {}

        name_to_existing = parse_semantic_match_response(text)
        candidate_by_name = {c.get("name", ""): c.get("uuid", "") for c in candidates}

        matched: Dict[str, str] = {}
        claimed: Set[str] = set()
        for generated_name, existing_name in name_to_existing.items():
            if not existing_name:
                continue
            uuid = candidate_by_name.get(existing_name)
            if not uuid:
                logger.warning(
                    f"[Import] Semantic match named a non-existent {kind} "
                    f"'{existing_name}' for '{generated_name}' — ignoring"
                )
                continue
            if uuid in claimed:
                logger.warning(
                    f"[Import] Semantic match for '{generated_name}' claimed "
                    f"already-used '{existing_name}' — skipping to avoid a double-link"
                )
                continue
            matched[generated_name] = uuid
            claimed.add(uuid)

        if matched:
            logger.info(f"[Import] Semantic matching linked {len(matched)} {kind}(s): {list(matched.keys())}")
        return matched

    @staticmethod
    def _pack_finder_js(pack_name: str) -> str:
        """JS snippet binding `pack` to a JournalEntry compendium, or erroring."""
        return f"""
        const pack = game.packs.find(p => p.documentName === 'JournalEntry'
            && (p.collection === {pack_name!r} || p.metadata.name === {pack_name!r}));
        if (!pack) return {{ error: 'Journal pack not found: ' + {pack_name!r} }};
        """

    async def _fetch_journal_pack(self, foundry_client, pack_name: str) -> List[Dict[str, Any]]:
        """Read every JournalEntry document (with its pages) out of a Foundry
        compendium pack, one document per execute-js call.

        Fetching the whole pack in a single `pack.getDocuments()` call
        repeatedly got the relay's Foundry-module WebSocket connection
        killed with close code 1009 ("message too big") partway through
        this campaign's 15-entry pack — stripping embedded images and
        waiting for game.ready didn't stop it, so whatever the real byte
        threshold is, the fix is to never build one big reply in the first
        place. A lightweight index call gets just the document ids, then
        each document is fetched (and image-stripped) in its own small
        reply, so no single WS message can ever be large regardless of the
        pack's total size.

        The index is also filtered down to entries named like 'Chapter N'
        or 'Appendix X' before any full document is fetched — a DDBImporter
        journals pack is often shared across every sourcebook synced into
        the world, not just the adventure being imported (this campaign's
        pack had Player's Handbook, Xanathar's Guide, Tasha's Cauldron,
        etc. mixed in with its actual chapters), which both wastes fetch
        round-trips and dilutes Pass 1/2 with unrelated rules-reference
        text.

        The relay wraps execute-js as an async function body, so each
        script awaits promises directly and returns the resolved value
        (not an async IIFE, whose value the relay drops); results are
        unwrapped from the relay envelope via `.get("result")`.
        """
        from campaign.importer import is_adventure_journal_entry

        index_query = self._pack_finder_js(pack_name) + """
        const index = await pack.getIndex();
        return { entries: index.contents.map(e => ({ id: e._id, name: e.name })) };
        """
        res = await foundry_client.execute_js(index_query)
        payload = res.get("result") if isinstance(res, dict) else res
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError(
                payload.get("error") if isinstance(payload, dict) else "Journal pack index query failed"
            )
        indexed = payload.get("entries", [])
        doc_ids = [e["id"] for e in indexed if is_adventure_journal_entry(e.get("name"))]
        skipped = [e["name"] for e in indexed if not is_adventure_journal_entry(e.get("name"))]
        if skipped:
            logger.info(f"[Import] Journal pack '{pack_name}': skipping non-adventure entries {skipped}")

        entries: List[Dict[str, Any]] = []
        for doc_id in doc_ids:
            doc_query = self._pack_finder_js(pack_name) + f"""
            const doc = await pack.getDocument({doc_id!r});
            if (!doc) return {{ error: 'Document not found: ' + {doc_id!r} }};
            const stripImages = (html) => (html || '')
                .replace(/<img\\b[^>]*>/gi, '')
                .replace(/data:[^"'\\s)]+/gi, '');
            return {{ name: doc.name, pages: (doc.pages?.contents ?? []).slice()
                .sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0))
                .map(p => ({{ name: p.name, html: stripImages(p.text && p.text.content) }})) }};
            """
            res = await foundry_client.execute_js(doc_query)
            payload = res.get("result") if isinstance(res, dict) else res
            if not isinstance(payload, dict) or payload.get("error"):
                logger.warning(
                    f"[Import] Skipping journal document {doc_id!r}: "
                    f"{payload.get('error') if isinstance(payload, dict) else 'query failed'}"
                )
                continue
            entries.append(payload)
        return entries

    # ─── Arc extension ───────────────────────────────────────────────────────

    async def extend_campaign_arc(
        self,
        campaign_name: str,
        current_level: int,
        llm_client=None,
        foundry_client=None,
        vault_path: str = None,
        comfyui_url: str = None,
        omlx_url: str = None,
        omlx_api_key: str = None,
        on_progress: Callable = None,
    ) -> Dict[str, Any]:
        """Generate and deploy the next arc for an existing campaign.

        Loads the existing campaign from the vault, prompts the LLM to extend it
        with new scenes/encounters/NPCs for the next level tier, and deploys
        everything into FoundryVTT alongside the existing content.

        Args:
            campaign_name: Name of the existing campaign to extend.
            current_level: The party's current level (arc starts here).
            llm_client: httpx.AsyncClient for LLM calls.
            foundry_client: Connected FoundryClient instance.
            vault_path: Obsidian vault path.
            comfyui_url: ComfyUI URL for map generation.
            omlx_url: oMLX API URL for map generation.
            omlx_api_key: oMLX API key.
            on_progress: Optional callback(msg, step, detail).
        """
        from campaign.generator import generate_arc_extension_prompt, validate_campaign
        import httpx

        result: Dict[str, Any] = {
            "status": "extending",
            "campaign_name": campaign_name,
            "steps": [],
        }

        def progress(msg: str, step: str = "", detail: str = ""):
            result["steps"].append({"message": msg, "step": step, "detail": detail})
            logger.info(f"[ArcExtend] {msg}")
            if on_progress:
                try:
                    on_progress(msg, step, detail)
                except Exception:
                    pass

        if llm_client is None:
            llm_client = httpx.AsyncClient(timeout=300)

        api_key = omlx_api_key or self.settings.llm_api_key

        # ── Step 1: Load existing campaign data ──
        progress("📖 Loading existing campaign data...", step="load")
        safe_name = sanitize_filename(campaign_name.lower())
        state_path = Path("./campaign_assets") / safe_name / "deployment_state.json"
        vault_json_path = None
        if vault_path:
            try:
                from campaign.vault import CampaignStore
                _store = CampaignStore(campaign_name, vault_path)
                vault_json_path = _store.campaign_file
            except Exception:
                # Fallback: manual path construction (legacy)
                vault_json_path = (
                    Path(vault_path).expanduser() / "Campaigns" / campaign_name / "campaign.json"
                )

        existing_data: Dict[str, Any] = {}
        for candidate in [state_path, vault_json_path]:
            if candidate and candidate.exists():
                try:
                    existing_data = json.loads(candidate.read_text(encoding="utf-8"))
                    # deployment_state wraps campaign_data under a "campaign_data" key
                    if "campaign_data" in existing_data and isinstance(existing_data["campaign_data"], dict):
                        existing_data = existing_data["campaign_data"]
                    progress(f"✅ Loaded campaign from {candidate}", step="load")
                    break
                except Exception as e:
                    progress(f"⚠️ Could not read {candidate}: {e}", step="load")

        if not existing_data:
            result["status"] = "error"
            result["error"] = (
                f"Campaign '{campaign_name}' not found. "
                "Build the campaign first before extending it."
            )
            return result

        # Determine arc number from existing deployment state
        existing_arcs = existing_data.get("story_arcs", [])
        arc_number = sum(1 for a in existing_arcs if a.get("arc_number", 0) > 0) + 2
        if arc_number < 2:
            arc_number = 2

        progress(
            f"📐 Generating Arc {arc_number} for levels {current_level}+...",
            step="generate",
        )

        # ── Step 2: Scan Foundry for current module list ──
        active_modules: Dict[str, Any] = {}
        if foundry_client:
            try:
                scan = await self.scan_foundry_world(foundry_client)
                active_modules = scan.get("active_modules", {})
            except Exception as e:
                progress(f"⚠️ Scan skipped: {e}", step="generate")

        # ── Step 2b: Build lore context from vault for consistency ──
        lore_context: str = ""
        if vault_path:
            try:
                from context.loader import CampaignLoader
                loader = CampaignLoader()
                await loader.load(vault_path=vault_path, campaign_name=campaign_name)
                camp_info = existing_data.get("campaign", {})
                query_parts = [
                    camp_info.get("description", ""),
                    camp_info.get("theme", ""),
                ]
                recent_arcs = [a.get("name", "") for a in existing_data.get("story_arcs", [])[-3:]]
                query_parts.extend(recent_arcs)
                lore_query = " ".join(p for p in query_parts if p).strip()
                if lore_query:
                    chunks = loader.search_vault(lore_query, max_results=12)
                    if chunks:
                        lore_context = "\n\n".join(chunks)
                        if len(lore_context) > 8000:
                            lore_context = lore_context[:8000] + "\n...(truncated)"
                        try:
                            progress(f"📚 Injected {len(chunks)} lore chunk(s) for consistency", step="generate")
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[ArcExtend] Lore injection skipped: {e}")
                lore_context = ""
        # ── Step 3: Generate arc via LLM ──
        arc_prompt = generate_arc_extension_prompt(
            existing_data, current_level=current_level,
            arc_number=arc_number, active_modules=active_modules,
            lore_context=lore_context,
        )

        endpoint = self._chat_endpoint()
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": arc_prompt},
                {"role": "user", "content": (
                    f"Generate Arc {arc_number} for '{campaign_name}', "
                    f"covering levels {current_level}+."
                )},
            ],
            "temperature": self.settings.campaign_gen_temperature,
            "max_tokens": 32768,
        }
        self._suppress_thinking(payload)

        arc_data = await self._post_and_parse_campaign_json(llm_client, endpoint, headers, payload)

        # Tag every new scene/NPC/encounter with the arc they belong to
        for section in ("scenes", "npcs", "encounters", "quest_logs", "locations", "story_arcs"):
            for item in arc_data.get(section, []):
                item["arc_number"] = arc_number

        warnings = validate_campaign(arc_data)
        for w in warnings:
            logger.warning(f"[ArcExtend] Validation: {w}")

        result["arc_data"] = arc_data
        arc_meta = arc_data.get("campaign", {})
        progress(
            f"✅ Arc {arc_number} generated — '{arc_meta.get('arc_title', 'New Arc')}' "
            f"(levels {arc_meta.get('arc_level_range', current_level)}+)",
            step="generate",
        )

        # ── Step 4: Merge arc data into existing campaign ──
        progress("🔀 Merging arc into campaign data...", step="merge")
        for section in ("scenes", "npcs", "encounters", "quest_logs", "locations",
                        "story_arcs", "journal_entries", "loot_tables", "loot_piles", "playlists"):
            existing_data.setdefault(section, [])
            existing_data[section].extend(arc_data.get(section, []))

        # ── Step 5: Save updated campaign to vault ──
        if vault_path:
            progress("💾 Saving updated campaign to vault...", step="vault")
            try:
                await self.save_to_vault(existing_data, vault_path)
                progress("✅ Vault updated", step="vault")
            except Exception as e:
                progress(f"⚠️ Vault save failed: {e}", step="vault")

        # ── Step 6: Generate maps for new scenes only ──
        progress("🎨 Generating maps for new scenes...", step="assets")
        asset_output_dir = Path("./campaign_assets") / (safe_name + "_maps")
        campaign_assets_dir = Path("./campaign_assets") / safe_name
        await asyncio.to_thread(campaign_assets_dir.mkdir, parents=True, exist_ok=True)

        map_generator = None
        try:
            from campaign.map_generator import MapGenerator
            map_generator = MapGenerator(
                comfyui_url=comfyui_url or getattr(settings, "comfyui_url", "http://127.0.0.1:18188"),
                omlx_base_url=getattr(settings, "omlx_base_url", "http://localhost:8800"),
                omlx_api_key=api_key,
                provider="auto",
            )
        except Exception as e:
            progress(f"⚠️ Map generator init failed: {e}", step="assets")

        asset_info: Dict[str, Any] = {"maps": [], "portraits": [], "status": "skipped"}
        if map_generator:
            try:
                # Only generate assets for the new arc's scenes/NPCs
                arc_only = dict(existing_data)
                arc_only["scenes"] = arc_data.get("scenes", [])
                arc_only["npcs"] = arc_data.get("npcs", [])
                arc_only["locations"] = arc_data.get("locations", [])
                asset_info = await self.generate_assets(arc_only, map_generator, asset_output_dir)
                progress(
                    f"✅ Generated {asset_info['total_maps']} map(s), {asset_info['total_portraits']} portrait(s)",
                    step="assets",
                )
            except Exception as e:
                progress(f"⚠️ Asset generation failed: {e}", step="assets")
            await map_generator.close()

        result["assets"] = asset_info

        # ── Step 7: Upload maps and deploy to Foundry ──
        if foundry_client:
            if asset_info.get("total_maps", 0) > 0:
                progress("📤 Uploading new maps to FoundryVTT...", step="upload")
                try:
                    upload_summary = await self.upload_maps_to_foundry(
                        arc_data, foundry_client, asset_output_dir, safe_name,
                    )
                    progress(
                        f"✅ Uploaded {upload_summary['uploaded']} map(s)",
                        step="upload",
                    )
                    result["upload_summary"] = upload_summary
                except Exception as e:
                    progress(f"⚠️ Map upload failed: {e}", step="upload")

            progress("🚀 Deploying new content to FoundryVTT...", step="deploy")
            try:
                deployment: Dict[str, Any] = {"scenes": [], "npcs": [], "encounters": []}
                await self.deploy_to_foundry(arc_data, foundry_client, deployment)
                progress(
                    f"✅ Deployed {len(deployment.get('scenes', []))} scenes, "
                    f"{len(deployment.get('npcs', []))} NPCs",
                    step="deploy",
                )
                # Deploy encounters for the new arc only
                enc_results = await self.deploy_encounters(arc_data, foundry_client, deployment, active_modules)
                deployment["encounters"] = enc_results
                result["deployment"] = deployment
            except Exception as e:
                progress(f"⚠️ Foundry deployment failed: {e}", step="deploy")
                result["deploy_error"] = str(e)

            # Enrich new scenes with walls/lights/sounds
            progress("🏗️ Enriching new scenes...", step="enrich")
            try:
                enrich_result = await self.enrich_scenes(
                    arc_data, foundry_client, on_progress=on_progress,
                )
                progress(
                    f"✅ Enriched {enrich_result.get('scenes_enriched', 0)} scenes",
                    step="enrich",
                )
                result["enrich_result"] = enrich_result
            except Exception as e:
                progress(f"⚠️ Scene enrichment failed: {e}", step="enrich")

        # ── Step 8: Save updated deployment state ──
        try:
            state_data = {
                "campaign_data": existing_data,
                "last_arc": arc_number,
                "last_arc_title": arc_meta.get("arc_title", ""),
                "last_extended_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            state_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            progress("💾 Deployment state saved", step="complete")
        except Exception as e:
            progress(f"⚠️ State save failed: {e}", step="complete")

        result["status"] = "complete"
        result["arc_number"] = arc_number
        result["arc_title"] = arc_meta.get("arc_title", f"Arc {arc_number}")
        return result

    # ─── Teardown ─────────────────────────────────────────────────────────────

    async def teardown_campaign(
        self,
        campaign_name: str,
        foundry_client,
    ) -> Dict[str, Any]:
        """Remove all AI-GM-created content for a campaign from FoundryVTT.

        Two deletion passes:
        1. Flag-based: deletes every world document that has flags["ai-gm"] set
           (Actors, JournalEntries, RollTables, Playlists, and Scenes).
        2. UUID-based fallback: reads the deployment state and deletes anything
           whose UUID was recorded but wasn't caught by the flag filter (e.g.
           entities created before the flag convention was stable).

        Does NOT touch the Obsidian vault or local campaign_assets files.
        """
        result: Dict[str, Any] = {
            "campaign_name": campaign_name,
            "deleted": {},
            "errors": [],
            "status": "ok",
        }

        if not foundry_client or not foundry_client.is_connected:
            result["status"] = "error"
            result["errors"].append("Not connected to FoundryVTT")
            return result

        # ── Pass 1: delete everything flagged with flags["ai-gm"] ──────────
        # Runs in a single execute-js call so it's one round-trip regardless
        # of how many entities exist.
        from foundry import scripts

        try:
            js_result = await foundry_client.execute_js(scripts.teardown_by_flag())
            # execute-js-result wraps the JS return value in {"result": ...}
            counts = js_result.get("result") if isinstance(js_result, dict) else None
            if not isinstance(counts, dict):
                counts = {}
            result["deleted"]["flag_pass"] = counts
            total = sum(v for v in counts.values() if isinstance(v, int))
            logger.info(f"[Teardown] Flag pass deleted {total} documents: {counts}")
        except Exception as e:
            logger.warning(f"[Teardown] Flag pass failed: {e}")
            result["errors"].append(f"flag_pass: {e}")

        # ── Pass 2: UUID fallback from deployment state ─────────────────────
        safe_name = sanitize_filename(campaign_name.lower())
        state_path = Path("./campaign_assets") / safe_name / "deployment_state.json"

        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                # Collect all UUIDs from every tracked section
                uuids: Dict[str, list] = {}
                section_type_map = {
                    "scenes":          "Scene",
                    "npcs":            "Actor",
                    "journal_entries": "JournalEntry",
                    "quest_logs":      "JournalEntry",
                    "loot_tables":     "RollTable",
                    "loot_piles":      "Actor",
                    "encounter_actors": "Actor",
                    "playlists":       "Playlist",
                }
                for section, doc_type in section_type_map.items():
                    for item in state.get(section, []):
                        uuid = item.get("uuid", "")
                        if uuid:
                            uuids.setdefault(doc_type, []).append(uuid)

                if uuids:
                    try:
                        fb_result = await foundry_client.execute_js(scripts.teardown_by_uuid_map(uuids))
                        fb_counts = fb_result.get("result") if isinstance(fb_result, dict) else None
                        if not isinstance(fb_counts, dict):
                            fb_counts = {}
                        result["deleted"]["uuid_pass"] = fb_counts
                        fb_total = sum(v for v in fb_counts.values() if isinstance(v, int))
                        logger.info(f"[Teardown] UUID fallback deleted {fb_total} more documents: {fb_counts}")
                    except Exception as e:
                        logger.warning(f"[Teardown] UUID pass failed: {e}")
                        result["errors"].append(f"uuid_pass: {e}")
            except Exception as e:
                logger.warning(f"[Teardown] Could not read deployment state: {e}")
                result["errors"].append(f"state_read: {e}")

        if result["errors"]:
            result["status"] = "partial"
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
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=on_progress,
        )
