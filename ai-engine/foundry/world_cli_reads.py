"""World CLI read adapters: each returns exactly what the FoundryClient method it replaces returns.

The shapes are the contract, not World CLI's: callers across the engine depend on them (including
details like `disposition` being preserved rather than defaulted, which once stalled combat). Each
adapter returns None to say "I can't answer this one", which sends the caller to the old path.

World CLI reports authored state. Where the old execute_js read a derived runtime value (an actor's
`hasPlayerOwner`) it is recomputed here from ownership and user roles the way Foundry defines it.
"""

import asyncio
from typing import Any, Dict, List, Optional

PAGE = 100
OWNER = 3          # CONST.DOCUMENT_OWNERSHIP_LEVELS.OWNER
_EMBEDDED = {"tokens": "token", "walls": "wall", "lights": "light", "sounds": "sound", "tiles": "tile",
             "drawings": "drawing", "notes": "note", "regions": "region", "templates": "template"}


async def _all(cli, command: str, params: Dict[str, Any], key: str) -> List[dict]:
    """Every row of a paginated list command."""
    rows: List[dict] = []
    offset = 0
    while True:
        page = await cli.call(command, {**params, "limit": PAGE, "offset": offset})
        batch = page.get(key) or []
        rows += batch
        if not page.get("hasMore") or not batch:
            return rows
        offset += len(batch)


async def _actors_full(cli) -> List[dict]:
    ids = [r["id"] for r in await _all(cli, "actor.list", {}, "actors")]
    full: List[dict] = []
    for i in range(0, len(ids), 50):
        full += (await cli.call("actor.get-many", {"ids": ids[i:i + 50]})).get("actors") or []
    return full


def _owner_ids(actor: dict, player_ids: List[str]) -> List[str]:
    """Non-GM users who own the actor: the user's own level, else the default (Foundry's testUserPermission)."""
    ownership = actor.get("ownership") or {}
    default = ownership.get("default", 0)
    return [uid for uid in player_ids if ownership.get(uid, default) >= OWNER]


async def actors(cli) -> List[dict]:
    full, users = await asyncio.gather(_actors_full(cli), _all(cli, "user.list", {}, "users"))
    players = [u["id"] for u in users if not u.get("isGM")]
    out = []
    for a in full:
        hp = ((a.get("system") or {}).get("attributes") or {}).get("hp") or {}
        out.append({
            "name": a.get("name", "Unknown"),
            "uuid": f"Actor.{a['id']}",
            "type": a.get("type", "unknown"),
            "has_player_owner": bool(_owner_ids(a, players)),
            "hp": hp.get("value"),
            "max_hp": hp.get("max"),
        })
    return out


async def player_actor_mapping(cli) -> Dict[str, dict]:
    full, users = await asyncio.gather(_actors_full(cli), _all(cli, "user.list", {}, "users"))
    players = {u["id"] for u in users if not u.get("isGM")}
    mapping: Dict[str, dict] = {"actor_names": {}, "actor_uuids": {}}
    for a in full:
        # Per-user OWNER entries only: the old script skipped `default`, so a world-wide default does
        # not make every player the owner. Order follows the ownership map, as it did.
        owners = [uid for uid, level in (a.get("ownership") or {}).items()
                  if uid != "default" and level >= OWNER and uid in players]
        if owners and a.get("name"):
            mapping["actor_names"][a["name"]] = owners[0]
            mapping["actor_uuids"][f"Actor.{a['id']}"] = owners[0]
    return mapping


async def _scenes(cli, name: Optional[str] = None) -> List[dict]:
    return await _all(cli, "scene.list", {"name": name} if name else {}, "scenes")


async def scene_names(cli) -> List[str]:
    return [s["name"] for s in await _scenes(cli)]


async def active_scene(cli) -> Optional[Dict[str, str]]:
    """{id, name} of the active scene. The old path preferred the headless client's canvas scene, which
    World CLI cannot see; they agree whenever the client follows the active scene, and a None here
    (nothing active) sends the caller to that old path."""
    for s in await _scenes(cli):
        if s.get("active"):
            return {"id": s["id"], "name": s["name"]}
    return None


async def users(cli) -> List[dict]:
    return [{
        "active": u.get("active", False), "avatar": u.get("avatar") or "icons/svg/mystery-man.svg",
        "character": u.get("character"), "color": u.get("color"), "id": u["id"],
        "isGM": u.get("isGM", False), "name": u.get("name"), "role": u.get("role"),
    } for u in await _all(cli, "user.list", {}, "users")]


async def world_metadata(cli) -> Dict[str, Any]:
    info, n_actors, n_items = await asyncio.gather(
        cli.call("system.info"), cli.call("actor.list", {"limit": 1}), cli.call("item.list", {"limit": 1}))
    system = info.get("system") or {}
    return {
        "name": (info.get("world") or {}).get("title") or "Unknown",
        "id": (info.get("world") or {}).get("id", ""),
        "version": (info.get("foundry") or {}).get("version", ""),
        "systems": [{"name": system["id"], "version": system.get("version", ""), "enabled": True}] if system.get("id") else [],
        "rooms": [],
        "totalActors": n_actors.get("total", 0),
        "totalItems": n_items.get("total", 0),
    }


async def active_modules(cli, include_world: bool = False) -> Dict[str, Any]:
    info = await cli.call("system.info")
    payload: Dict[str, Any] = {"modules": [
        {"id": m["id"], "title": m.get("title") or m["id"], "version": m.get("version") or "", "active": bool(m.get("active"))}
        for m in info.get("modules") or []]}
    if include_world:
        world = info.get("world") or {}
        payload["world"] = {"title": world.get("title", ""), "id": world.get("id", "")}
    return payload


async def _scene_id(cli, name: str) -> Optional[str]:
    matches = [s for s in await _scenes(cli, name) if s.get("name") == name]
    return matches[0]["id"] if matches else None


async def scene_id(cli, scene_name: str) -> Optional[str]:
    """The id of the scene with exactly this name, or None."""
    return await _scene_id(cli, scene_name)


def _token(t: dict) -> dict:
    """The normalized token get_scene_tokens returns. `disposition` stays None when absent: consumers
    (the combat loop) decide what unknown means, and defaulting it once stalled combat."""
    return {
        "name": t.get("name", "Unknown"), "x": t.get("x", 0), "y": t.get("y", 0),
        "width": t.get("width", 1), "height": t.get("height", 1),
        "actorUuid": t.get("actorId") or "", "id": t.get("id", ""),
        "emitter": 0, "brightness": 1, "disposition": t.get("disposition"),
    }


async def _tokens(cli, scene_id: str) -> List[dict]:
    rows = await _all(cli, "scene.token.list", {"sceneId": scene_id}, "tokens")
    got = await asyncio.gather(*[cli.call("scene.token.get", {"sceneId": scene_id, "tokenId": r["id"]}) for r in rows])
    return [g["token"] for g in got]


async def scene_tokens(cli, scene_name: str) -> Optional[List[dict]]:
    scene_id = await _scene_id(cli, scene_name)
    if scene_id is None:
        return None
    return [_token(t) for t in await _tokens(cli, scene_id)]


async def scene_details(cli, scene_name: str) -> Optional[Dict[str, Any]]:
    """The relay's get-scene envelope: {"data": scene document with its embedded documents}."""
    scene_id = await _scene_id(cli, scene_name)
    if scene_id is None:
        return None
    scene = (await cli.call("scene.get", {"sceneId": scene_id}))["scene"]
    counts = scene.get("counts") or {}
    kinds = [k for k in _EMBEDDED if counts.get(k)]

    async def fetch(kind: str):
        if kind == "tokens":
            return kind, await _tokens(cli, scene_id)
        return kind, await _all(cli, f"scene.{_EMBEDDED[kind]}.list", {"sceneId": scene_id}, kind)

    data = {k: v for k, v in scene.items() if k != "counts"}
    data["_id"] = scene.get("_id") or scene.get("id")
    data.update({k: [] for k in _EMBEDDED})
    data.update(dict(await asyncio.gather(*[fetch(k) for k in kinds])))
    return {"data": data}


async def canvas_documents(cli, doc_type: str) -> Optional[List[dict]]:
    """The active scene's embedded documents of one type. Rows are World CLI's list rows (walls carry
    c/door/ds, which is everything their consumers read); tokens are not served here."""
    kind = _EMBEDDED.get(doc_type)
    if kind is None or doc_type == "tokens":
        return None
    scene = await active_scene(cli)
    if scene is None:
        return None
    return await _all(cli, f"scene.{kind}.list", {"sceneId": scene["id"]}, doc_type)


async def active_combat_id(cli) -> Optional[str]:
    """The id of the active combat (what `game.combat` is), or None when there is none."""
    rows = await _all(cli, "combat.list", {}, "combats")
    return next((r["id"] for r in rows if r.get("active")), None)


async def token_rows(cli, scene_id: str) -> List[dict]:
    """The scene's tokens as World CLI list rows: id, name, actorId, x, y (enough to resolve and move one)."""
    return await _all(cli, "scene.token.list", {"sceneId": scene_id}, "tokens")


async def actor_image(cli, actor_id: str) -> Optional[str]:
    """The actor's prototype token art, else its portrait: what place_token reads so a token shows the actor's image."""
    actor = (await cli.call("actor.get", {"actorId": actor_id})).get("actor") or {}
    return ((actor.get("prototypeToken") or {}).get("texture") or {}).get("src") or actor.get("img")


async def actor_dispositions(cli, names: List[str]) -> Dict[str, int]:
    """{actor name: prototype token disposition} for actors whose name equals or overlaps a wanted name
    (either containing the other, case-insensitively), -1 when the prototype has none: what auto-placed
    combatants use so an ally stays friendly and a monster stays hostile."""
    want = [str(n).lower() for n in names]
    rows = [r for r in await _all(cli, "actor.list", {}, "actors")
            if r.get("name") and any(w == r["name"].lower() or w in r["name"].lower() or r["name"].lower() in w for w in want)]
    out: Dict[str, int] = {}
    for i in range(0, len(rows), 50):
        for a in (await cli.call("actor.get-many", {"ids": [r["id"] for r in rows[i:i + 50]]})).get("actors") or []:
            disposition = (a.get("prototypeToken") or {}).get("disposition")
            out[a["name"]] = -1 if disposition is None else disposition
    return out
