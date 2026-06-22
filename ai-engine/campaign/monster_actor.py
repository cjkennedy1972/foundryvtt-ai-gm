"""
Shared helper for resolving or creating a world actor for a named monster.

Used by both CampaignOrchestrator (pre-staged encounters) and the procedural
action executor (runtime encounter generation) so both paths get the same
compendium-lookup + portrait-preservation behaviour.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def ensure_monster_actor(
    foundry_client,
    name: str,
    cr: float = 1,
    hp: int = 10,
    ac: int = 10,
) -> Optional[str]:
    """Return the UUID of a world actor matching *name*, creating one if needed.

    Strategy
    --------
    1. World actor lookup — fast path, covers actors already in the world.
    2. Compendium search + import — preserves the full stat block and portrait.
       If the import itself fails, fetches img/token art from the compendium
       entry to use in the placeholder so the mystery-man icon is avoided.
    3. Placeholder fallback — minimal NPC with best available portrait art.
    """
    # ── 1. World actor lookup ────────────────────────────────────────────────
    try:
        actors = await foundry_client.get_actors(world_only=True)
        match = next(
            (a for a in actors if a.get("name", "").lower() == name.lower()),
            None,
        )
        if match:
            logger.debug(f"ensure_monster_actor: '{name}' found in world actors")
            return match.get("uuid", "")
    except Exception as e:
        logger.debug(f"ensure_monster_actor world lookup failed for '{name}': {e}")

    # ── 2. Compendium search + import ────────────────────────────────────────
    compendium_img = ""
    compendium_token_img = ""
    try:
        result = await foundry_client._send("search", query=name, excludeCompendiums=False)
        items = result.get("results", result.get("data", []))
        if isinstance(items, dict):
            items = items.get("results", items.get("entries", []))
        if isinstance(items, list):
            compendium_entry = next(
                (
                    i for i in items
                    if i.get("name", "").lower() == name.lower()
                    and i.get("documentType") == "Actor"
                    and i.get("package")
                ),
                None,
            )
            if compendium_entry:
                # Grab any portrait the search result already carries
                compendium_img = compendium_entry.get("img", "")
                comp_uuid = compendium_entry.get("uuid", "")
                if comp_uuid:
                    try:
                        # Full import: brings complete stat block, portrait,
                        # and prototype token image in one operation. Stamp the
                        # ai-gm flag so campaign teardown ("Remove from World")
                        # can find and delete it later — a raw toObject() copy
                        # carries no flag and would otherwise be orphaned.
                        js = (
                            f'const doc = await fromUuid("{comp_uuid}");'
                            'if (!doc) return {error: "not found"};'
                            'const data = doc.toObject();'
                            'data.flags = data.flags || {};'
                            f'data.flags["ai-gm"] = {{imported_monster: true, source_uuid: "{comp_uuid}"}};'
                            'const imported = await Actor.create(data);'
                            'return {uuid: imported?.uuid ?? ""};'
                        )
                        import_result = await foundry_client.execute_js(js)
                        imported_uuid = (import_result.get("result", {}) or {}).get("uuid", "")
                        if imported_uuid:
                            logger.info(f"Imported '{name}' from compendium: {imported_uuid}")
                            return imported_uuid
                    except Exception as e:
                        logger.warning(f"Compendium import failed for '{name}': {e}")

                    # Import failed — fetch portrait + token art for placeholder
                    try:
                        js = (
                            f'const doc = await fromUuid("{comp_uuid}");'
                            'if (!doc) return {};'
                            'return {'
                            '  img: doc.img ?? "",'
                            '  tokenImg: doc.prototypeToken?.texture?.src ?? doc.img ?? ""'
                            '};'
                        )
                        art_result = await foundry_client.execute_js(js)
                        art = (art_result.get("result", {}) or {})
                        compendium_img = art.get("img", compendium_img)
                        compendium_token_img = art.get("tokenImg", compendium_img)
                        logger.debug(
                            f"Fetched compendium art for '{name}': "
                            f"portrait={compendium_img!r} token={compendium_token_img!r}"
                        )
                    except Exception as e:
                        logger.debug(f"Art fetch failed for '{name}': {e}")
    except Exception as e:
        logger.debug(f"Compendium search failed for '{name}': {e}")

    # ── 3. Placeholder fallback ──────────────────────────────────────────────
    try:
        data = {
            "name": name,
            "type": "npc",
            "system": {
                "details": {
                    "cr": cr,
                    "biography": {"value": f"Auto-generated placeholder for {name} (CR {cr})"},
                },
                "attributes": {
                    "hp": {"value": hp, "max": hp, "formula": ""},
                    "ac": {"flat": ac, "calc": "natural"},
                    "speed": {"value": 30, "units": "ft"},
                },
            },
            "flags": {"ai-gm": {"auto_placeholder": True, "encounter_monster": True}},
        }
        # Use compendium portrait art when available so the placeholder doesn't
        # show the mystery-man icon.
        if compendium_img:
            data["img"] = compendium_img
        if compendium_token_img:
            data["prototypeToken"] = {"texture": {"src": compendium_token_img}}

        result = await foundry_client._send("create", entityType="Actor", data=data)
        actor_data = result.get("data", result) if isinstance(result, dict) else {}
        uuid = actor_data.get("uuid", actor_data.get("_id", ""))
        logger.info(
            f"Created placeholder actor '{name}' (CR {cr})"
            f"{' with compendium portrait' if compendium_img else ''}: {uuid}"
        )
        return uuid
    except Exception as e:
        logger.warning(f"Placeholder creation failed for '{name}': {e}")
        return None
