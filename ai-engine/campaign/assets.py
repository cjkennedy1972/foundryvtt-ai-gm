"""Single upload implementation for campaign images (maps, portraits).

orchestrator.py had three divergent copies of "read file bytes, upload to
Foundry, resolve the served path" — one each for build-time map upload,
build-time portrait upload, and regenerate-time (which also attaches the
result to an already-deployed scene/actor). Unifying just the upload step
here removes the duplication that caused it; "what happens after upload"
(stash on a dict vs. push into an existing Foundry doc) stays with the
caller, since build-time and regenerate-time genuinely differ there —
build's entities don't exist in Foundry yet, regenerate's already do.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote

logger = logging.getLogger(__name__)


def resolve_uploaded_path(upload: Any, fallback: str) -> str:
    """Extract the served path from a relay upload_file response, or fall back.

    The relay may return a percent-encoded path; unquote it. Falls back to a
    constructed path (ai-gm-.../safe_name/filename) when the response isn't
    the expected {"path": ...} shape.
    """
    if isinstance(upload, dict):
        path = upload.get("path")
        if path:
            return unquote(path)
    return fallback


async def upload_image(
    foundry_client,
    img_path: Path,
    upload_dir: str,
    filename: str,
    fallback_path: str,
) -> Dict[str, Any]:
    """Upload one image file to Foundry.

    Returns {"ok": True, "src": <resolved path>} on success, or
    {"ok": False, "error": "<ExceptionType>: <message>"} on failure. Never
    raises — callers decide how upload failures affect their summary.
    """
    try:
        img_bytes = await asyncio.to_thread(img_path.read_bytes)
        upload = await foundry_client.upload_file(
            file_bytes=img_bytes,
            path=upload_dir,
            filename=filename,
            mime_type="image/png",
        )
        return {"ok": True, "src": resolve_uploaded_path(upload, fallback_path)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
