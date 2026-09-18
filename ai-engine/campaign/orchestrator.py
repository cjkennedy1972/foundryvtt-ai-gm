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
from typing import Any, Callable, Dict, Optional

from campaign.checkpoints import BuildCheckpoint
from campaign.orchestrator_assets import AssetPipelineMixin
from campaign.orchestrator_deploy import DeploymentMixin
from campaign.orchestrator_import import WorldImportMixin
import campaign.modules  # noqa: F401 — populates registry.MODULE_REGISTRY on import
from config import settings
from utils.path_safety import sanitize_filename

logger = logging.getLogger(__name__)

# Completion budget for campaign-JSON generation calls (Pass 2, refill,
# arc extension). Verified directly against the configured LLM host: its
# real context window is 131072 tokens (a 156540-token prompt got a hard
# "exceeds the available context size (131072 tokens)" error, a 130450-token
# prompt did not) — nowhere near the old hardcoded 32768, which was ONLY our
# own request cap, not a model limit. A dense chapter (Dragonlance Ch.5: The
# Northern Wastes) hit that old 32768 cap on all 3 retries, truncating valid
# JSON right at the boundary each time. The largest real chapter prompt seen
# in production is ~20k tokens, so 65536 leaves large headroom on both ends.
CAMPAIGN_GEN_MAX_TOKENS = 65536


class CampaignOrchestrator(AssetPipelineMixin, DeploymentMixin, WorldImportMixin):
    """Orchestrates the full campaign build pipeline.

    Asset generation, Foundry deployment and world import live in the three
    mixins above; what stays here is world scanning, LLM campaign generation,
    vault persistence and the build/extend entry points.
    """

    # Exposed on the class so the mixins reach it through self instead of
    # importing back into this module (which would be circular — this module
    # imports them). The right-hand side resolves to the module global above;
    # class bodies fall back to module scope rather than creating a closure.
    CAMPAIGN_GEN_MAX_TOKENS = CAMPAIGN_GEN_MAX_TOKENS

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
                # A non-200 (e.g. "exceeds the available context size") is
                # exactly the overflow failure this function's token-budget
                # handling targets — it must get the same retry + diagnostics
                # as a parse failure, not an immediate unretried raise.
                last_err = Exception(f"LLM request failed: {resp.status_code} {resp.text[:500]}")
                logger.warning(
                    f"[LLM JSON] Attempt {attempt}/{max_attempts}: HTTP {resp.status_code}: "
                    f"{resp.text[:300]!r}"
                )
                if attempt < max_attempts:
                    continue
                raise last_err

            body = resp.json()
            choice = body.get("choices", [{}])[0]
            # .get(..., "") only covers a MISSING key — some servers return
            # content: null (vs "") on an empty/filtered completion, which
            # `or ""` also normalizes so it hits the same retry/diagnostic
            # path below instead of raising an uncaught AttributeError.
            raw_text = choice.get("message", {}).get("content") or ""
            try:
                return parse_campaign_response(raw_text)
            except json.JSONDecodeError as e:
                last_err = e
                # A parse failure with no visibility into WHAT came back is
                # undiagnosable after the fact — this exact gap meant a real
                # failure (3 straight empty completions, all HTTP 200) could
                # only be investigated by trying to reproduce it live rather
                # than reading the log. usage/finish_reason distinguish "the
                # model ran out of budget mid-answer" (finish_reason=length)
                # from "produced literally nothing" (completion_tokens≈0,
                # finish_reason=stop) — different root causes, same
                # JSONDecodeError.
                usage = body.get("usage", {})
                logger.warning(
                    f"[LLM JSON] Attempt {attempt}/{max_attempts}: finish_reason="
                    f"{choice.get('finish_reason')!r}, usage={usage}, "
                    f"content_len={len(raw_text)}, "
                    f"content_preview={raw_text[:300]!r}"
                )
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
            "max_tokens": CAMPAIGN_GEN_MAX_TOKENS,
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
                "max_tokens": CAMPAIGN_GEN_MAX_TOKENS,
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




    # ─── Upload generated maps and set scene backgrounds ────────────────────






    # ─── Regenerate assets for an existing campaign ─────────────────────────


    # ─── Phase 5: Deploy to FoundryVTT ──────────────────────────────────────


    # ─── Phase 5c: Deploy pre-staged encounters ──────────────────────────────








    # ─── Phase 5b: Enrich deployed scenes with walls/lights/sounds ──────────

    # Pixels per grid square for all generated scenes.
    # 64px/sq means: 16×12 grid → 1024×768px, 20×15 → 1280×960px, 24×18 → 1536×1152px.
    # All clean multiples — image dimensions, scene canvas, and wall coords stay in sync.
    GRID_PX: int = 64


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

        # Build a fast name→uuid lookup from the deployment result. Deliberately
        # "created" only — a "linked" scene (reused from a pre-existing Foundry
        # document, e.g. a DDBImporter map) already has its own real walls/
        # lights from the professional map; overwriting them with this
        # campaign's hallucinated scene_setup would corrupt a good map.
        deployed_scene_names = {
            s["name"] for s in deployment.get("scenes", []) if s.get("status") == "created"
        }
        linked_scene_names = {
            s["name"] for s in deployment.get("scenes", []) if s.get("status") == "linked"
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
                if scene_name in linked_scene_names:
                    logger.info(
                        f"[Enrich] '{scene_name}' is a linked (reused) scene — "
                        "skipping enrichment to avoid overwriting its real map's walls/lights"
                    )
                else:
                    logger.warning(
                        f"[Enrich] '{scene_name}' was not deployed (no created or linked "
                        "entry found) — skipping"
                    )
                continue

            if on_progress:
                try:
                    on_progress(f"🏗️ Enriching scene: {scene_name}", step="enrich")
                except Exception:
                    logger.debug("on_progress callback raised", exc_info=True)

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

        # A checkpoint is only eligible when the caller supplied a stable name.
        # The prompt is stored as a guard so a retry for a different campaign
        # cannot accidentally reuse generated content from an earlier build.
        checkpoint = None
        checkpoint_state = None
        resumed_from_assets = False
        if campaign_name:
            safe_name = sanitize_filename(campaign_name.lower())
            checkpoint = BuildCheckpoint(safe_name)
            checkpoint_state = await checkpoint.load()
            if checkpoint_state and checkpoint_state.get("prompt") == prompt:
                if checkpoint_state.get("phase") == "assets" and isinstance(
                    checkpoint_state.get("campaign_data"), dict
                ):
                    campaign_data = checkpoint_state["campaign_data"]
                    campaign_name = checkpoint_state.get("campaign_name", campaign_name)
                    resumed_from_assets = True
                    result["campaign_data"] = campaign_data
                    result["assets"] = checkpoint_state.get("asset_info", {})

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
        owns_client = llm_client is None
        # Own only what we create: the HTTP routes pass their own client and
        # close it themselves, so closing it here would reach into the
        # caller's resource.
        if owns_client:
            import httpx
            llm_client = httpx.AsyncClient(timeout=300)

        if resumed_from_assets:
            progress("♻️ Resuming campaign build from asset checkpoint", step="assets")
        elif campaign_data is not None:
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
                if checkpoint:
                    await checkpoint.save(
                        "generate", prompt=prompt, campaign_name=campaign_name,
                        campaign_data=campaign_data,
                    )

            # ── Phase 2b: Generate settlements ──
            if not resumed_from_assets:
                progress("🏘️ Generating settlements with NPCs and schedules...", step="settlements")
                try:
                    from campaign.settlement_integration import SettlementIntegration
                    settlement_gen = SettlementIntegration(llm_client if hasattr(llm_client, 'post') else None)
                    campaign_context = campaign_data.get("campaign", {}).get("description", "")
                    settlements = await settlement_gen.generate_settlements_from_campaign(
                        campaign_data, campaign_context, max_settlements=3
                    )
                    if settlements:
                        campaign_data["settlements"] = settlements
                        progress(
                            f"✅ Generated {len(settlements)} settlement(s)",
                            step="settlements",
                            detail=", ".join(s.name for s in settlements.values()),
                        )
                    else:
                        progress("ℹ️ No settlements generated (no settlement names found)", step="settlements")
                except Exception as e:
                    progress(f"⚠️ Settlement generation failed: {e}", step="settlements")
                    logger.warning(f"Settlement generation error: {e}")

            # ── Phase 3: Save to Obsidian vault ──
            progress("💾 Saving campaign to Obsidian vault...", step="vault")
            # Serialize settlements before saving (LLM output has Settlement objects)
            campaign_to_save = campaign_data.copy()
            if "settlements" in campaign_to_save:
                from campaign.settlement_integration import serialize_settlements
                settlements_obj = campaign_to_save.pop("settlements")
                campaign_to_save["settlements"] = serialize_settlements(settlements_obj)
            manifest = await self.save_to_vault(campaign_to_save, vault_path)
            result["manifest"] = manifest
            progress(f"✅ Campaign saved to vault", step="vault", detail=manifest.get("campaign_folder", ""))
            if checkpoint and not resumed_from_assets:
                await checkpoint.save(
                    "vault", prompt=prompt, campaign_name=campaign_name,
                    campaign_data=campaign_to_save,
                )

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

            asset_info = result.get("assets", {"maps": [], "portraits": [], "status": "skipped"})
            if not resumed_from_assets and map_generator:
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

            # MapGenerator owns an HTTP client even when this retry is using
            # already-generated assets; always release it on every path.
            if resumed_from_assets and map_generator:
                await map_generator.close()

            if checkpoint and not resumed_from_assets:
                await checkpoint.save(
                    "assets",
                    prompt=prompt,
                    campaign_name=campaign_name,
                    campaign_data=campaign_to_save,
                    asset_info=asset_info,
                )

            result["assets"] = asset_info

            # ── Phase 4b-4d: Upload generated assets to Foundry ──
            await self._upload_generated_assets(
                campaign_data, asset_info, foundry_client,
                asset_output_dir, safe_campaign_name, result, progress,
            )

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
            if checkpoint:
                await checkpoint.clear()

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
            if owns_client and llm_client:
                await llm_client.aclose()

        return result

    # ─── Campaign import ────────────────────────────────────────────────────










    # ─── Arc extension ───────────────────────────────────────────────────────


    async def _upload_generated_assets(
        self, campaign_data, asset_info, foundry_client,
        asset_output_dir, safe_campaign_name, result, progress,
    ) -> None:
        """Upload maps, portraits and prologue panels to Foundry.

        These were three near-identical twenty-line blocks differing only in
        the noun, the uploader, the gate and the result key. A failure in one
        must not stop the others, which is why each is caught individually.

        Maps and portraits also upload when nothing was generated this run:
        import mode matches pre-existing files, so total_* is 0 with real work
        to do. Prologue panels have no such fallback — they only ever exist if
        this run generated them.
        """
        uploads = (
            (
                "map",
                self.upload_maps_to_foundry,
                asset_info.get("total_maps", 0) > 0
                or any(s.get("map_file") for s in campaign_data.get("scenes", [])),
                "upload_summary",
            ),
            (
                "portrait",
                self.upload_portraits_to_foundry,
                asset_info.get("total_portraits", 0) > 0
                or any(n.get("portrait_file") for n in campaign_data.get("npcs", [])),
                "portrait_upload_summary",
            ),
            (
                "prologue panel",
                self.upload_prologue_to_foundry,
                asset_info.get("total_prologue_panels", 0) > 0,
                "prologue_upload_summary",
            ),
        )

        for noun, uploader, should_upload, result_key in uploads:
            if not (foundry_client and should_upload):
                continue
            progress(f"📤 Uploading {noun}s to FoundryVTT...", step="upload")
            try:
                summary = await uploader(
                    campaign_data, foundry_client, asset_output_dir, safe_campaign_name
                )
                progress(
                    f"✅ Uploaded {summary['uploaded']} {noun}(s) to Foundry",
                    step="upload",
                    detail=f"uploaded={summary['uploaded']}, failed={summary['failed']}",
                )
                if summary["errors"]:
                    logger.warning(f"{noun.capitalize()} upload errors: {summary['errors']}")
                result[result_key] = summary
            except Exception as e:
                progress(f"⚠️ {noun.capitalize()} upload failed: {e}", step="upload")
                logger.exception("%s upload to Foundry failed", noun.capitalize())

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
                    logger.debug("on_progress callback raised", exc_info=True)

        if llm_client is None:
            # This used to build its own client and never close it, leaking a
            # connection pool per call. The method has two return points and no
            # wrapping try, so rather than thread ownership through 250 lines,
            # require the caller to supply one — the only caller
            # (api/routes/campaign.py) already does, and manages its lifetime.
            raise ValueError(
                "extend_campaign_arc requires an llm_client; the caller owns "
                "its lifetime (see api/routes/campaign.py)"
            )

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
        lore_context = await self._arc_save_to_vault(
            campaign_name, vault_path, existing_data, progress
        )
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
            "max_tokens": CAMPAIGN_GEN_MAX_TOKENS,
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
        asset_info = await self._arc_generate_maps(
            arc_data, existing_data, map_generator, asset_output_dir, progress
        )

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
                    arc_data, foundry_client, deployment, on_progress=on_progress,
                )
                progress(
                    f"✅ Enriched {enrich_result.get('enriched', 0)} scenes",
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


    # ─── Convenience wrapper ─────────────────────────────────────────────────


    async def _arc_save_to_vault(self, campaign_name, vault_path, existing_data, progress):
        lore_context = ""
        if vault_path:
            try:
                from context.loader import CampaignLoader
                # vault_path is a constructor argument, not a load() one.
                loader = CampaignLoader(vault_path=vault_path)
                await loader.load(campaign_name)
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
                            logger.debug("progress callback raised", exc_info=True)
            except Exception as e:
                logger.debug(f"[ArcExtend] Lore injection skipped: {e}")
                lore_context = ""

        return lore_context

    async def _arc_generate_maps(self, arc_data, existing_data, map_generator, asset_output_dir, progress):
        asset_info = {}
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

        return asset_info
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
