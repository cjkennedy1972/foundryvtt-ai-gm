"""World CLI write adapters: each returns the (preflight, execute) pair that WorldCLIRouter.write runs.

`preflight` is a dry run (it persists nothing). `execute(cli, key)` is the real call, and returns the
shape the relay path it replaces returned, so callers don't change. The never-write-twice rule lives in
WorldCLIRouter.write; adapters only have to pass `key` as the idempotency key when the command takes one
and raise on a partial result so the router treats it as "may have applied".
"""

import base64
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from foundry.world_cli import WorldCLI, WorldCLIError

Write = Tuple[Callable[[WorldCLI], Awaitable[Any]], Callable[[WorldCLI, str], Awaitable[Any]]]

# The client's plural canvas layer names -> World CLI's embedded-document kinds.
KINDS = {"walls": "wall", "lights": "light", "sounds": "sound", "tiles": "tile", "drawings": "drawing",
         "notes": "note", "regions": "region", "templates": "template", "tokens": "token"}


def doc_id(doc: Dict[str, Any]) -> Optional[str]:
    return doc.get("_id") or doc.get("id")


def split_uuid(uuid: str, doc_type: str) -> Optional[Tuple[str, str]]:
    """(sceneId, documentId) from an embedded document uuid like Scene.<id>.Wall.<id>, else None."""
    parts = (uuid or "").split(".")
    if len(parts) == 4 and parts[0] == "Scene" and all(parts):
        return parts[1], parts[3]
    return None


def chat_create(text: str) -> Write:
    data = {"content": text}

    async def preflight(cli):
        await cli.call("chat.create", {"data": data}, dry_run=True)

    async def execute(cli, key):
        message = (await cli.call("chat.create", {"data": data}, idempotency_key=key))["message"]
        return {"success": True, "type": "chat-send-result", "via": "world-cli",
                "data": {"id": message["id"], "uuid": f"ChatMessage.{message['id']}", "content": message.get("content", text),
                         "whisper": message.get("whisper", []), "flags": message.get("flags", {})}}

    return preflight, execute


def canvas_create(scene_id: str, doc_type: str, docs: List[dict]) -> Optional[Write]:
    kind = KINDS.get(doc_type)
    if kind is None:
        return None
    command, params = f"scene.{kind}.create-many", {"sceneId": scene_id, "data": docs}

    async def preflight(cli):
        await cli.call(command, params, dry_run=True)

    async def execute(cli, key):
        outcome = await cli.call(command, params, idempotency_key=key)
        created = [o for o in outcome.get("outcomes", []) if o.get("status") == "created"]
        if not outcome.get("complete", True):
            raise WorldCLIError("PARTIAL_WRITE", f"only {len(created)} of {len(docs)} {doc_type} were created", {"partial": True})
        return {"success": True, "via": "world-cli", "data": {"created": [o.get("id") for o in created]}}

    return preflight, execute


def canvas_delete(scene_id: str, doc_type: str, ids: List[str]) -> Optional[Write]:
    kind = KINDS.get(doc_type)
    if kind is None or not ids:
        return None
    command, params = f"scene.{kind}.delete-many", {"sceneId": scene_id, "ids": ids}

    async def preflight(cli):
        await cli.call(command, params, dry_run=True)

    async def execute(cli, key):
        outcome = await cli.call(command, params)
        if outcome.get("complete") is False:
            raise WorldCLIError("PARTIAL_WRITE", f"only part of the {len(ids)} {doc_type} were deleted", {"partial": True})
        return {"success": True, "via": "world-cli", "result": len(ids)}

    return preflight, execute


def canvas_update(scene_id: str, doc_type: str, doc_id_: str, patch: Dict[str, Any]) -> Optional[Write]:
    kind = KINDS.get(doc_type)
    if kind is None:
        return None
    command, params = f"scene.{kind}.update", {"sceneId": scene_id, f"{kind}Id": doc_id_, "patch": patch}

    async def preflight(cli):
        await cli.call(command, params, dry_run=True)

    async def execute(cli, key):
        await cli.call(command, params)
        return {"success": True, "via": "world-cli"}

    return preflight, execute


def scene_update(scene_id: str, patch: Dict[str, Any]) -> Write:
    params = {"sceneId": scene_id, "patch": patch}

    async def preflight(cli):
        await cli.call("scene.update", params, dry_run=True)

    async def execute(cli, key):
        scene = (await cli.call("scene.update", params)).get("scene")
        return {"success": True, "via": "world-cli", "data": scene}

    return preflight, execute


def file_upload(file_bytes: bytes, path: str, filename: str, mime_type: str, source: str, overwrite: bool) -> Optional[Write]:
    """file.upload. The relay's /upload took a directory plus a filename; World CLI takes the full destination
    path (and only accepts one under worlds/<worldId>/, so other directories are refused by the dry run and
    fall back). Only the plain overwrite-the-data-directory case is routed; the relay's `requestId` has no
    World CLI counterpart, and no caller reads it (they read `path`)."""
    if source != "data" or not overwrite:
        return None
    dest = f"{path.strip('/')}/{filename}".lstrip("/")
    params = {"path": dest, "contentBase64": base64.b64encode(file_bytes).decode("ascii"), "mimeType": mime_type}

    async def preflight(cli):
        await cli.call("file.upload", params, dry_run=True)

    async def execute(cli, key):
        stored = (await cli.call("file.upload", params, idempotency_key=key))["file"]["path"]
        return {"type": "upload-file-result", "success": True, "path": stored, "via": "world-cli"}

    return preflight, execute
