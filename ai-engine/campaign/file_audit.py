"""After a campaign is deployed, ask Foundry which files its documents reference but cannot find.

Generation and upload are separate steps, and a portrait, token or map that failed to upload still
leaves a document pointing at it; nothing then says so until a player sees a broken image. When the
fvtt-world-cli bridge is available, `world.audit-files` lists them. This is a report, never a gate:
it returns None when World CLI is not configured and a status dict when it cannot answer, and it
never raises, so it cannot fail a deploy.
"""

import logging
from collections import Counter
from typing import Any, Dict, Optional

from foundry.world_cli import WorldCLIError

logger = logging.getLogger(__name__)

SAMPLE = 20


async def audit_world_files(world_cli, limit: int = 100) -> Optional[Dict[str, Any]]:
    """The world's broken file references as {"status": "ok", "checked", "broken", "by_type", "sample"},
    {"status": "unavailable", "reason": code} when the bridge cannot answer, or None when unconfigured."""
    if world_cli is None:
        return None
    try:
        result = await world_cli.call("world.audit-files", {"limit": limit})
    except WorldCLIError as e:
        logger.info(f"[Audit] file audit unavailable ({e.code}): {e.message}")
        return {"status": "unavailable", "reason": e.code}
    except Exception:
        logger.warning("[Audit] file audit failed unexpectedly", exc_info=True)
        return {"status": "unavailable", "reason": "ERROR"}

    broken = result.get("broken") or []
    total = result.get("total", len(broken))
    summary = {
        "status": "ok",
        "checked": result.get("checkedRefs", 0),
        "broken": total,
        "by_type": dict(Counter(b.get("docType", "?") for b in broken)),
        "sample": [
            {k: b.get(k) for k in ("docType", "id", "field", "path")} for b in broken[:SAMPLE]
        ],
    }
    if total:
        logger.warning(
            f"[Audit] {total} broken file reference(s) in the deployed world "
            f"({summary['by_type']}); first: "
            + "; ".join(f"{s['docType']} {s['id']} {s['field']} -> {s['path']}" for s in summary["sample"][:3])
        )
    else:
        logger.info(f"[Audit] no broken file references ({summary['checked']} checked)")
    return summary
