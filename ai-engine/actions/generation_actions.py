"""LLM-backed content generation: encounters, treasure, NPCs, quests.

Split out of actions/executors.py, which had grown to 2,445 lines. The
functions are moved verbatim; executors.py still owns the ACTION_HANDLERS
dispatch table and re-exports these names, so existing imports keep working.
"""

import asyncio
import html
import logging
from pathlib import Path
from typing import Any, Optional

from actions.executors_shared import ExecutionError, _extract_token_id, _require
from config import settings
from foundry.client import FoundryClient
from tts import playback as tts_playback
from utils.tasks import spawn

logger = logging.getLogger(__name__)


async def _resolve_scene_dimensions(foundry: FoundryClient) -> tuple:
    """Return (width, height, grid_size) for the active scene, with safe defaults.

    Foundry scene payloads vary in shape across versions (top-level vs. nested
    under "data"; grid as a number or a {size} object), so parse defensively.
    """
    width, height, grid = 800, 600, 100
    try:
        if not foundry:
            return width, height, grid
        details = await foundry.get_scene_details()
        if not isinstance(details, dict):
            return width, height, grid
        data = details.get("data") if isinstance(details.get("data"), dict) else {}
        width = int(details.get("width") or data.get("width") or width)
        height = int(details.get("height") or data.get("height") or height)
        g = details.get("grid", data.get("grid"))
        if isinstance(g, dict):
            grid = int(g.get("size") or grid)
        elif isinstance(g, (int, float)) and g:
            grid = int(g)
        else:
            grid = int(details.get("gridSize") or data.get("gridSize") or grid)
    except Exception as e:
        logger.debug(f"[CompendiumEncounter] Scene dimension lookup failed: {e}")
    return width, height, max(1, grid)


async def execute_generate_encounter(
    party_level: int, party_size: int, difficulty: str = "medium",
    environment: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None, source: Optional[str] = None
) -> dict:
    """Generate a balanced encounter from Foundry D&D 5e compendium.

    Uses real monster stat blocks instead of LLM-generated creatures.
    Queries the D&D 5e compendium, selects monsters that fit party power,
    and positions them tactically on the map.
    """
    try:
        from combat.compendium_generator import CompendiumEncounterGenerator

        # Build the generator against the *real* scene so placements land on the
        # canvas and snap to its grid (defaults are only used if the scene query
        # fails).
        scene_w, scene_h, grid = await _resolve_scene_dimensions(foundry)
        gen = CompendiumEncounterGenerator(
            foundry=foundry, scene_width=scene_w, scene_height=scene_h, grid_size=grid
        )
        encounter = await gen.generate(
            party_level=party_level,
            party_size=party_size,
            difficulty=difficulty,
            environment=environment,
        )

        logger.info(
            f"[CompendiumEncounter] {encounter['notes']} "
            f"(adjusted XP {encounter['adjusted_xp']:.0f}/{encounter['budget']:.0f})"
        )

        result = {
            "type": "generate_encounter",
            "encounter": {
                "difficulty": encounter["difficulty_rating"],
                "notes": encounter["notes"],
                "budget": encounter["budget"],
                "adjusted_xp": encounter["adjusted_xp"],
                "creatures": encounter["creatures"],
                "placements": encounter["placements"],
            }
        }

        # Deploy to Foundry if connected.
        if foundry and foundry.is_connected:
            from campaign.monster_actor import ensure_monster_actor
            placed_tokens = []

            for placement in encounter["placements"]:
                monster_name = placement.get("name", "Monster")
                cr = placement.get("cr", 1)
                x = placement.get("x", 200)
                y = placement.get("y", 200)

                if placement.get("source") == "world":
                    # Existing campaign NPC — already a world actor; place by its
                    # own UUID, no import needed.
                    world_uuid = placement.get("uuid", "")
                else:
                    # Compendium monster — import the real stat block into the
                    # world (or reuse an existing world actor), then place by the
                    # resolved world UUID. Placing by name alone fails for
                    # monsters not yet in the world.
                    world_uuid = await ensure_monster_actor(foundry, monster_name, cr=cr)
                if not world_uuid:
                    logger.warning(
                        f"[CompendiumEncounter] Could not resolve actor for "
                        f"'{monster_name}' — skipping"
                    )
                    continue

                token_result = await foundry.place_token(
                    uuid=world_uuid, x=x, y=y, disposition=-1
                )
                if token_result and "error" not in token_result:
                    tid = _extract_token_id(token_result)
                    if tid:
                        placed_tokens.append(tid)
                        logger.debug(
                            f"[CompendiumEncounter] Placed {monster_name} at ({x}, {y})"
                        )
                    else:
                        logger.warning(
                            f"[CompendiumEncounter] Placed {monster_name} but could not "
                            f"read a token id from result keys={list(token_result)}"
                        )

            if placed_tokens:
                # Start combat with EVERY token on the scene (party included),
                # not just the placed monsters — otherwise the combat tracker
                # has hostiles only and the PCs never get initiative.
                combat_ids = list(placed_tokens)
                try:
                    scene_tokens = await foundry.get_scene_tokens()
                    combat_ids = [t["id"] for t in scene_tokens if t.get("id")] or combat_ids
                except Exception as e:
                    logger.warning(
                        f"[CompendiumEncounter] Could not fetch scene tokens for "
                        f"combat ({e}) — starting with placed monsters only"
                    )
                await foundry.start_encounter(combat_ids, roll_all=True)
                logger.info(
                    f"[CompendiumEncounter] Deployed {len(placed_tokens)} tokens, "
                    f"started encounter with {len(combat_ids)} combatants"
                )

            result["placed_tokens"] = placed_tokens
            result["deployed_to_foundry"] = len(placed_tokens) > 0

        return result

    except Exception as e:
        logger.error(f"[CompendiumEncounter] Generation failed: {e}", exc_info=True)
        return {"type": "generate_encounter", "error": str(e)}


async def execute_generate_treasure(
    cr: float, rarity_preference: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None, source: Optional[str] = None
) -> dict:
    """Generate loot and treasure: gold, gems, mundane items, and magical
    items — written to a loot journal entry, and (if Item Piles is active)
    also deployed as a real, physical, lootable pile actor on the scene.

    Previously called gen.generate_treasure(cr), a method that doesn't
    exist on ProceduralGenerator (the real one is gen.treasure_gen.generate),
    and read the result with dict .get() calls against what generate()
    actually returns — a GeneratedTreasure dataclass. Every call raised
    AttributeError, silently swallowed by the except below: this action has
    never generated any treasure at all until this fix.
    """
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        treasure = gen.treasure_gen.generate(cr)

        logger.info(f"[Procedural] Generated treasure worth {treasure.total_value}gp")

        result = {
            "type": "generate_treasure",
            "treasure": {
                "gold": treasure.gold,
                "gems": treasure.gems,
                "items": treasure.items,
                "magical_items": treasure.magical_items,
                "total_value_gp": treasure.total_value,
            }
        }

        if foundry and foundry.is_connected:
            def _li(name, value=None):
                text = html.escape(str(name))
                return f"<li>{text}{f' ({html.escape(str(value))})' if value else ''}</li>"

            gem_lines = "".join(_li(g.get("name", "Gem"), g.get("value")) for g in treasure.gems)
            item_lines = "".join(_li(i.get("name", "Item"), i.get("value")) for i in treasure.items)
            magic_lines = "".join(_li(m.get("name", "Magic Item"), m.get("value")) for m in treasure.magical_items)
            content = (
                f"<h2>Loot Found</h2>"
                f"<p>Total value: {treasure.total_value} gp</p>"
                + (f"<p>{treasure.gold} gold coins</p>" if treasure.gold else "")
                + (f"<h3>Gems</h3><ul>{gem_lines}</ul>" if gem_lines else "")
                + (f"<h3>Items</h3><ul>{item_lines}</ul>" if item_lines else "")
                + (f"<h3>Magic Items</h3><ul>{magic_lines}</ul>" if magic_lines else "")
            )
            journal_data = {
                "name": f"Treasure (CR {cr})",
                "pages": [{"name": "Loot", "type": "text", "text": {"content": content, "format": 1}}],
            }
            journal_result = await foundry.create_entity("JournalEntry", journal_data)
            result["journal_uuid"] = (journal_result or {}).get("uuid", "")
            result["deployed_to_foundry"] = bool(result["journal_uuid"])
            logger.info(f"[Procedural] Created loot journal entry: {result['journal_uuid']}")

            # A journal entry is a record, not something a player can pick up.
            # If Item Piles is active, also deploy a real physical pile —
            # reusing the same on_loot_table integration campaign generation
            # already uses for pre-built loot tables (campaign/orchestrator.py).
            try:
                from foundry import scripts as _scripts
                mods_res = await foundry.execute_js(_scripts.get_active_modules())
                active_mods = mods_res.get("result") if isinstance(mods_res, dict) else None
                mods = {m.get("id"): m for m in active_mods} if isinstance(active_mods, list) else {}
            except Exception:
                mods = {}

            if "item-piles" in mods:
                from campaign.modules.registry import MODULE_REGISTRY
                item_piles_integration = MODULE_REGISTRY.get("item-piles")
                if item_piles_integration and item_piles_integration.on_loot_table:
                    def _gp(value_str):
                        try:
                            return gen.treasure_gen._estimate_value(str(value_str))
                        except Exception:
                            return 0

                    entries = [
                        {"name": g.get("name", "Gem"), "foundry_item_type": "loot", "value_gp": _gp(g.get("value", "10gp"))}
                        for g in treasure.gems
                    ] + [
                        {"name": i.get("name", "Item"), "foundry_item_type": "loot", "value_gp": _gp(i.get("value", "10gp"))}
                        for i in treasure.items
                    ] + [
                        {"name": m.get("name", "Magic Item"), "foundry_item_type": "equipment",
                         "value_gp": _gp(m.get("value", "500gp")), "rarity": m.get("rarity", "common")}
                        for m in treasure.magical_items
                    ]
                    try:
                        pile_actor = await item_piles_integration.on_loot_table(
                            {"name": f"Treasure (CR {cr})", "entries": entries}, mods
                        )
                        if pile_actor:
                            pile_result = await foundry.create_entity("Actor", pile_actor)
                            pile_uuid = (pile_result or {}).get("uuid", "")
                            if pile_uuid:
                                token_result = await foundry.place_token(pile_actor["name"], x=400, y=400, disposition=0)
                                result["loot_pile_uuid"] = pile_uuid
                                result["loot_pile_token_id"] = (token_result or {}).get("id", "")
                                logger.info(f"[Procedural] Created loot pile: {pile_uuid}")
                    except Exception as e:
                        logger.warning(f"[Procedural] Loot pile creation failed: {e}")

        return result
    except Exception as e:
        logger.error(f"[Procedural] Treasure generation failed: {e}", exc_info=True)
        return {"type": "generate_treasure", "error": str(e)}


async def execute_generate_npc(
    role: Optional[str] = None, faction: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None, source: Optional[str] = None
) -> dict:
    """Generate a new NPC and create a Foundry actor + token on the current scene.

    Same defect the treasure executor above carried: gen.generate_npc() is not
    a method on ProceduralGenerator (the real one is gen.npc_gen.generate), and
    the result was read with dict .get() against a GeneratedNPC dataclass. The
    AttributeError was swallowed by the except below, so this action has never
    produced an NPC. GeneratedNPC carries no alignment, so the actor keeps the
    neutral default the dict reads previously fell back to anyway.
    """
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        npc = gen.npc_gen.generate()

        name = npc.name
        alignment = "Neutral"
        description = f"{npc.appearance} {npc.background}".strip()
        logger.info(f"[Procedural] Generated NPC: {name} ({npc.class_name})")

        result = {
            "type": "generate_npc",
            "npc": {
                "name": name,
                "race": npc.race,
                "class": npc.class_name,
                "level": npc.level,
                "alignment": alignment,
                "description": description,
            }
        }

        if foundry and foundry.is_connected:
            hp = max(1, npc.level * 4)
            actor_data = {
                "name": name,
                "type": "npc",
                "system": {
                    "details": {
                        "alignment": alignment,
                        "biography": {"value": description},
                    },
                    "attributes": {
                        "hp": {"value": hp, "max": hp},
                    },
                },
            }
            actor_result = await foundry.create_entity("Actor", actor_data)
            actor_uuid = (actor_result or {}).get("uuid", "")

            # Offset by the current token count so multiple generated NPCs
            # don't stack on the same square.
            try:
                n_existing = len(await foundry.get_scene_tokens())
            except Exception:
                n_existing = 0
            token_result = await foundry.place_token(name, x=400 + (n_existing % 8) * 100, y=400, disposition=0)
            token_id = (token_result or {}).get("id", "") if "error" not in (token_result or {}) else ""

            result["npc"]["actor_uuid"] = actor_uuid
            result["npc"]["token_id"] = token_id
            result["deployed_to_foundry"] = bool(actor_uuid)
            logger.info(f"[Procedural] Created NPC actor {actor_uuid}, token {token_id}")

        return result
    except Exception as e:
        logger.error(f"[Procedural] NPC generation failed: {e}", exc_info=True)
        return {"type": "generate_npc", "error": str(e)}


async def execute_generate_quest(
    theme: Optional[str] = None, difficulty: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None, source: Optional[str] = None
) -> dict:
    """Generate a new quest and create a Foundry JournalEntry for it.

    Third instance of the treasure/NPC defect above: gen.generate_quest() is
    not a method on ProceduralGenerator (the real one is gen.quest_gen.generate)
    and GeneratedQuest is a dataclass, not a dict, so every call raised
    AttributeError into the except below and no quest was ever generated.
    GeneratedQuest carries no difficulty of its own, so the caller's requested
    difficulty stands; its resolution_options are the per-quest steps the
    journal's task list was reaching for.
    """
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        quest = gen.quest_gen.generate()

        title = quest.title
        quest_difficulty = difficulty or "medium"
        logger.info(f"[Procedural] Generated quest: {title} ({quest_difficulty})")

        result = {
            "type": "generate_quest",
            "quest": {
                "title": title,
                "objective": quest.objective,
                "difficulty": quest_difficulty,
                "reward": quest.reward,
                "objectives": list(quest.resolution_options),
            }
        }

        if foundry and foundry.is_connected:
            objectives = quest.resolution_options
            obj_html = (
                "".join(f"<li>{html.escape(str(o))}</li>" for o in objectives)
                if objectives else f"<li>{html.escape(quest.objective)}</li>"
            )
            content = (
                f"<h2>{html.escape(title)}</h2>"
                f"<h3>Objective</h3><p>{html.escape(quest.objective)}</p>"
                f"<h3>Ways to Resolve It</h3><ul>{obj_html}</ul>"
                f"<h3>Reward</h3><p>{html.escape(quest.reward)}</p>"
                f"<p><em>Difficulty: {html.escape(quest_difficulty)}</em></p>"
            )
            journal_data = {
                "name": title,
                "pages": [{"name": "Quest Details", "type": "text", "text": {"content": content, "format": 1}}],
            }
            journal_result = await foundry.create_entity("JournalEntry", journal_data)
            journal_uuid = (journal_result or {}).get("uuid", "")
            result["quest"]["journal_uuid"] = journal_uuid
            result["deployed_to_foundry"] = bool(journal_uuid)
            logger.info(f"[Procedural] Created quest journal entry: {journal_uuid}")

        return result
    except Exception as e:
        logger.error(f"[Procedural] Quest generation failed: {e}", exc_info=True)
        return {"type": "generate_quest", "error": str(e)}
