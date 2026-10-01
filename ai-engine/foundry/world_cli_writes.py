"""Scene placeables (walls, lights, sounds) written through World CLI instead of the relay.

The relay path (FoundryClient.canvas_create) is a generic call on the current scene with no check on
the batch. World CLI validates it, can dry-run it, writes through Foundry's document API and confirms
the stored result, and its bulk `*.create-many` takes a scene's 20 walls in ~260 ms against ~3.4 s one
at a time. This module is the opt-in (WORLD_CLI_WRITES_ENABLED) fast path; the relay stays the fallback.

The one rule that matters: never run a write twice. A fallback is taken only when the failure proves
nothing was executed (WorldCLIError.maybe_applied is False) or the failing call was the dry run, which
persists nothing by definition. A timeout, a connection lost mid-request or a partial result is
reported as a failure and is NOT retried on the relay.
"""

import logging
from typing import Any, Dict, List, Optional

from config import settings
from foundry.world_cli import WorldCLIError

logger = logging.getLogger(__name__)

_KINDS = {"walls": "wall", "lights": "light", "sounds": "sound"}


def _doc_id(doc: Dict[str, Any]) -> Optional[str]:
    return doc.get("_id") or doc.get("id")


async def place_via_world_cli(
    app_state, foundry, layer: str, docs: List[dict], clear_existing: bool,
) -> Optional[Dict[str, Any]]:
    """Place `docs` on the current scene's `layer` ("walls" | "lights" | "sounds") through World CLI.

    Returns a result dict when World CLI handled it (success or a reported failure), or None when the
    caller should use the relay: not enabled, no scene, or a failure that provably executed nothing.
    """
    cli = getattr(app_state, "world_cli", None)
    kind = _KINDS.get(layer)
    if cli is None or kind is None or not settings.world_cli_writes_enabled:
        return None

    scene_id = await foundry.get_active_scene_id()
    if not scene_id:
        return None

    # Preflight. A dry run persists nothing, so whatever goes wrong here, the relay may take over.
    try:
        await cli.call(f"scene.{kind}.create-many", {"sceneId": scene_id, "data": docs}, dry_run=True)
    except WorldCLIError as e:
        logger.info(f"[WorldCLI] {layer} preflight failed ({e.code}); using the relay: {e.message}")
        return None

    try:
        cleared = 0
        if clear_existing:
            ids = [i for i in (_doc_id(d) for d in await foundry.canvas_get(layer)) if i]
            if ids:
                await cli.call(f"scene.{kind}.delete-many", {"sceneId": scene_id, "ids": ids})
                cleared = len(ids)
        outcome = await cli.call(f"scene.{kind}.create-many", {"sceneId": scene_id, "data": docs})
    except WorldCLIError as e:
        if not e.maybe_applied:
            logger.info(f"[WorldCLI] {layer} write refused ({e.code}), nothing executed; using the relay: {e.message}")
            return None
        logger.error(f"[WorldCLI] {layer} write may have partly applied ({e.code}); NOT retrying on the relay: {e.message}")
        return {"success": False, "via": "world-cli", "error": f"{e.code}: {e.message}", "maybe_applied": True}

    complete = bool(outcome.get("complete", True))
    created = [o for o in outcome.get("outcomes", []) if o.get("status") == "created"]
    logger.info(f"[WorldCLI] {layer}: created {len(created)}/{len(docs)}" + (f", cleared {cleared}" if cleared else ""))
    if not complete:
        return {"success": False, "via": "world-cli", "error": f"only {len(created)} of {len(docs)} {layer} were created",
                "outcomes": outcome.get("outcomes"), "maybe_applied": True}
    return {"success": True, "via": "world-cli", "created": len(created), "cleared": cleared, "sceneId": scene_id}
