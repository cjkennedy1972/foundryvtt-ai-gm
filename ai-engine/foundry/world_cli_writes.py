"""World CLI write adapters: each returns the (preflight, execute) pair that WorldCLIRouter.write runs.

`preflight` is a dry run (it persists nothing). `execute(cli, key)` is the real call, and returns the
shape the relay path it replaces returned, so callers don't change. The never-write-twice rule lives in
WorldCLIRouter.write; adapters only have to pass `key` as the idempotency key when the command takes one
and raise on a partial result so the router treats it as "may have applied".
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

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


def resolve_token(rows: List[dict], ident: str) -> Optional[dict]:
    """The scene token an identifier names, trying what the model tends to hand over in the order the old
    in-browser lookup did: the exact token id, then an actor id or 'Actor.<id>' uuid, then the token's name."""
    want = str(ident)
    wl = want.lower()
    short = wl.split(".")[-1]
    for match in (
        lambda t: t.get("id") == want,
        lambda t: bool(t.get("actorId")) and (t["actorId"].lower() == short or f"actor.{t['actorId'].lower()}" == wl),
        lambda t: bool(t.get("name")) and t["name"].lower() == wl,
    ):
        found = next((t for t in rows if match(t)), None)
        if found:
            return found
    return None


def token_move(scene_id: str, row: dict, x: float, y: float) -> Write:
    """Move a token to absolute pixels, returning move_token's old result: {ok, id, name, x, y, fromX, fromY},
    or ok False when Foundry left it where it was (scene bounds, walls) or it was already there."""
    params = {"sceneId": scene_id, "tokenId": row["id"], "patch": {"x": float(x), "y": float(y)}}
    from_x, from_y = row.get("x"), row.get("y")

    async def preflight(cli):
        await cli.call("scene.token.update", params, dry_run=True)

    async def execute(cli, key):
        token = (await cli.call("scene.token.update", params)).get("token") or {}
        now_x, now_y = token.get("x"), token.get("y")
        if now_x == from_x and now_y == from_y:
            return {"ok": False, "id": row["id"], "name": row.get("name"),
                    "error": "Foundry did not move the token (blocked by scene bounds or walls)"}
        return {"ok": True, "id": row["id"], "name": token.get("name", row.get("name")), "x": now_x, "y": now_y,
                "fromX": from_x, "fromY": from_y, "via": "world-cli"}

    return preflight, execute


def combat_roll_initiative(combat_id: str) -> Write:
    """Roll initiative for every combatant without one, as the old `game.combat.rollAll()` did, returning
    its old execute-js result {"result": "ok"}. Anything short of a confirmed roll is uncertain, never a retry."""
    params = {"combatId": combat_id, "select": "all"}

    async def preflight(cli):
        # The command requires an idempotency key even for a dry run; this one is thrown away.
        await cli.call("combat.roll-initiative", params, dry_run=True, idempotency_key=f"dry-{uuid4().hex}")

    async def execute(cli, key):
        out = await cli.call("combat.roll-initiative", params, idempotency_key=key)
        if out.get("mutation") == "not-executed":
            raise WorldCLIError("NOT_EXECUTED", "World CLI rolled nothing")
        if out.get("mutation") == "unknown" or out.get("complete") is False or out.get("unconfirmedCombatantIds"):
            raise WorldCLIError("PARTIAL_WRITE", "initiative rolls could not all be confirmed", {"partial": True})
        return {"result": "ok", "via": "world-cli"}

    return preflight, execute


def combat_end(combat_id: str) -> Write:
    """End (delete) the combat. No caller inspects the relay's end-encounter reply; this returns a success marker."""
    params = {"combatId": combat_id}

    async def preflight(cli):
        await cli.call("combat.delete", params, dry_run=True)

    async def execute(cli, key):
        await cli.call("combat.delete", params)
        return {"success": True, "via": "world-cli"}

    return preflight, execute
