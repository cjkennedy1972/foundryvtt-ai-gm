"""Writing a generated campaign into a Foundry world, and tearing it back out.

Extracted verbatim from campaign/orchestrator.py, which had grown to 4,469
lines in a single class — 9x this project's 500-line limit. Mixins rather than
free functions so every `self.` call inside these methods keeps working
unchanged; CampaignOrchestrator composes them.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from campaign.prologue import build_prologue_pages
import campaign.modules  # noqa: F401 — populates registry.MODULE_REGISTRY on import
from campaign.modules.registry import MODULE_REGISTRY, NpcContext, run_flag_hook, run_npc_hooks
from utils.path_safety import sanitize_filename

logger = logging.getLogger(__name__)


class DeploymentMixin:
    """Writing a generated campaign into a Foundry world, and tearing it back out."""

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
                    # Record on campaign_data itself (not just the deployment
                    # report) so a checkpoint-driven retry that re-enters this
                    # loop with the same campaign_data treats this NPC as
                    # already-linked instead of recreating it (see the
                    # existing_uuid check at the top of this loop).
                    npc["existing_uuid"] = _uuid(result)
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
                # A checkpoint-driven retry re-enters this loop with the same
                # campaign_data — skip anything a prior (crashed) attempt at
                # THIS import already created, rather than duplicating it.
                if entry.get("_deployed_uuid"):
                    deployment["journal_entries"].append(
                        {"title": entry.get("title", "?"), "uuid": entry["_deployed_uuid"], "status": "linked"}
                    )
                    continue
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
                    entry["_deployed_uuid"] = _uuid(result)
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
            if panels and prologue.get("_deployed_uuid"):
                deployment.setdefault("prologue", {})["uuid"] = prologue["_deployed_uuid"]
                deployment.setdefault("prologue", {})["title"] = title
                deployment.setdefault("prologue", {})["status"] = "linked"
                deployment["journal_entries"].append(
                    {"title": f"Prologue — {title}", "uuid": prologue["_deployed_uuid"], "status": "linked"}
                )
            elif panels:
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
                    prologue["_deployed_uuid"] = prologue_uuid
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
                if quest.get("_deployed_uuid"):
                    deployment["quest_logs"].append(
                        {"title": quest.get("title", "?"), "uuid": quest["_deployed_uuid"], "status": "linked"}
                    )
                    continue
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
                    quest["_deployed_uuid"] = _uuid(result)
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
                if table.get("_deployed_uuid"):
                    deployment["loot_tables"].append(
                        {"name": table.get("name", "?"), "uuid": table["_deployed_uuid"], "status": "linked"}
                    )
                    continue
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
                    table["_deployed_uuid"] = _uuid(result)
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
                        "foundry_name": scene.get("foundry_scene_name") or scene["name"],
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
                    # Same as the NPC branch above: mark this on campaign_data
                    # so a checkpoint-driven retry sees it as already-linked
                    # rather than creating a second copy of the scene.
                    scene["existing_uuid"] = _uuid(result)
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

        # Snapshot which actors already exist BEFORE we place anything.
        # _ensure_monster_actor's fast path returns a matching world actor's
        # UUID when one exists, with no signal that it was reused rather than
        # created — so without this, encounter_actors recorded the user's own
        # pre-existing stat blocks as ours and teardown deleted them (observed
        # live: 4 of the user's DDBImporter monsters destroyed). One call for
        # the whole deployment, not per monster.
        pre_existing_actor_uuids: Set[str] = set()
        try:
            for actor in await foundry_client.get_actors(world_only=True):
                if actor.get("uuid"):
                    pre_existing_actor_uuids.add(actor["uuid"])
        except Exception as e:
            # Fail SAFE: an empty snapshot would mark every reused actor as
            # ours and make teardown destructive, so treat a failed snapshot
            # as "can't prove ownership of anything" instead.
            logger.warning(
                f"[Encounter] Could not snapshot pre-existing actors ({e}) — "
                "encounter actors will not be tracked for teardown"
            )
            pre_existing_actor_uuids = None  # type: ignore[assignment]

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
        # Foundry-side calls must use the real document name, not the
        # generated name linked scenes are tracked under above (see
        # foundry_scene_name in import_campaign) — the two can be almost
        # unrelated text for pin-label matches.
        foundry_name_by_scene = {
            s["name"]: s.get("foundry_name", s["name"])
            for s in deployment.get("scenes", []) if s.get("status") == "linked"
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
                # A linked scene is tracked under its generated name, but any
                # actual Foundry call must target the real document name.
                foundry_scene_name = foundry_name_by_scene.get(linked_scene, linked_scene)
                try:
                    switch_result = await foundry_client.activate_scene_and_wait(foundry_scene_name, timeout=7)
                    if isinstance(switch_result, dict) and switch_result.get("ok") is False:
                        enc_result["errors"].append(f"scene switch: {switch_result.get('error', 'not found')}")
                        enc_result["status"] = "partial"
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
                        real_scene = await foundry_client.get_scene_by_name(foundry_scene_name)
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
                    # An actor that already existed before this deployment is
                    # the user's own (e.g. a DDBImporter stat block) — record
                    # it as reused so teardown leaves it alone. When the
                    # snapshot is unavailable, mark everything reused: failing
                    # to clean up our own actor is recoverable, deleting the
                    # user's is not.
                    if actor_uuid:
                        enc_actors = deployment.setdefault("encounter_actors", [])
                        if not any(a.get("uuid") == actor_uuid for a in enc_actors):
                            reused = (
                                True if pre_existing_actor_uuids is None
                                else actor_uuid in pre_existing_actor_uuids
                            )
                            enc_actors.append({
                                "name": monster_name, "uuid": actor_uuid, "reused": reused,
                            })

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
                # NEVER delete a document the AI GM didn't create. Deployment
                # records a document it REUSED rather than created two ways:
                # status="linked" (a scene/NPC matched to a pre-existing
                # Foundry document, e.g. a DDBImporter map) and reused=True
                # (an encounter monster resolved to a stat block that already
                # existed). Both are the user's own content. Deleting them
                # here destroyed 14 pre-made maps and 7 actors in a live run
                # before this guard existed.
                skipped: List[str] = []
                for section, doc_type in section_type_map.items():
                    for item in state.get(section, []):
                        uuid = item.get("uuid", "")
                        if not uuid:
                            continue
                        if item.get("status") == "linked" or item.get("reused"):
                            skipped.append(f"{item.get('name', uuid)} ({section})")
                            continue
                        uuids.setdefault(doc_type, []).append(uuid)

                if skipped:
                    logger.info(
                        f"[Teardown] Preserving {len(skipped)} pre-existing document(s) "
                        f"the AI GM reused rather than created: {skipped}"
                    )
                    result["preserved"] = skipped

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
