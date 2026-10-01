"""One-step pairing of fvtt-world-cli with the engine's own GM session, and the seed that keeps it paired.

Pairing is a trust decision that normally needs a human: the GM presses Pair in Foundry, then approves
the request at a terminal. That is fine once, but a relay-launched headless session starts from an empty
browser every time, so the pairing has to be seeded into it (the relay's HEADLESS_LOCALSTORAGE_SEED).
This does the manual steps and returns exactly what to seed:

  1. open the module's Authorization window in the engine's GM session, label the request with a
     one-time nonce, and press Pair;
  2. approve ONLY the pending request that carries that nonce, from this world, user and origin;
  3. read the credential back and build the seed (plus an optional command policy).

It is idempotent: if the bridge is already connected it skips straight to reading the pairing back.

The command policy is deliberately narrow. Nothing that executes code or changes who can do what can be
allowed through here, whatever the caller asks for.
"""

import asyncio
import json
import re
import secrets
import shlex
from typing import Any, Dict, List, Optional

from foundry.world_cli import WorldCLIError

# Commands the module ships denied because they run code, change roles/permissions, or persist outside
# the world (protocol docs, "Commands that are off by default"). Never allowable via this helper.
NEVER_ALLOW_PREFIXES = ("macro.execute", "setting.set", "user.role.set", "user.permissions.set",
                        "scene.region.behavior.executable.")
_COMMAND = re.compile(r"^[a-z][a-z-]*(\.[a-z-]+)+$")

OPEN_AND_PAIR_JS = """
const menu = game.settings.menus.get('fvtt-world-cli.authorization');
if (!menu) return 'module-not-enabled';
const app = new menu.type();
await app.render({ force: true });
await new Promise(r => setTimeout(r, 1000));
const input = document.querySelector('#fvtt-world-cli-client-label');
if (input) input.value = %s;
const btn = document.querySelector('#fvtt-world-cli-authorization [data-action="pair"]');
if (!btn) return 'no-pair-button';
btn.click();
return 'clicked';
"""
SESSION_JS = "return {origin: window.location.origin, world: game.world.id, user: game.user.id, isGM: game.user.isGM};"
READ_JS = ("return {credentials: JSON.stringify(game.settings.get('fvtt-world-cli', 'credentials')), "
           "clientId: JSON.stringify(game.settings.get('fvtt-world-cli', 'clientId'))};")


class SetupError(Exception):
    """A setup step an operator can act on. `message` is authored text, safe to show a caller; `detail`
    (what Foundry or the daemon actually returned) is only for the server log."""

    def __init__(self, message: str, detail: Any = None):
        super().__init__(message)
        self.message, self.detail = message, detail


def validate_allow(commands: List[str]) -> List[str]:
    """The commands to allow unattended, or SetupError if any is malformed or never allowable."""
    for c in commands:
        if not _COMMAND.match(c):
            raise SetupError(f"{c!r} is not a World CLI command name (like scene.wall.delete-many)")
        if c.startswith(NEVER_ALLOW_PREFIXES):
            raise SetupError(f"{c} runs code or changes access and cannot be allowed through setup; enable it by hand if you must")
    return sorted(set(commands))


async def _js(foundry, script: str) -> Any:
    res = await foundry.execute_js(script)
    if not isinstance(res, dict) or "result" not in res:
        raise SetupError("Foundry did not run the setup script in this session.", detail=res)
    return res["result"]


async def _bridge_connected(world_cli) -> bool:
    try:
        pong = await world_cli.call("system.ping")
    except WorldCLIError:
        return False
    return ((pong or {}).get("bridge") or {}).get("status") == "connected"


async def _pair(world_cli, foundry, session: Dict[str, Any], wait_s: float) -> None:
    label = f"aigm-setup-{secrets.token_hex(4)}"
    clicked = await _js(foundry, OPEN_AND_PAIR_JS % json.dumps(label))
    if clicked != "clicked":
        raise SetupError({"module-not-enabled": "The World CLI module is not enabled in this world.",
                          "no-pair-button": "The module's Authorization window has no Pair button (already paired, or a different version)."}
                         .get(clicked, "Could not press Pair in the Foundry session."), detail=clicked)

    deadline = asyncio.get_running_loop().time() + wait_s
    while True:
        pending = (await world_cli.control("auth.pending") or {}).get("pending") or []
        mine = [p for p in pending if p.get("label") == label and p.get("origin") == session["origin"]
                and p.get("worldId") == session["world"] and p.get("userId") == session["user"]]
        if mine:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise SetupError("No pairing request from this Foundry session reached the daemon; is `fvtt-world-cli bridge serve` running?")
        await asyncio.sleep(0.5)
    await world_cli.control("auth.approve", {"code": mine[0]["code"]})

    while not await _bridge_connected(world_cli):
        if asyncio.get_running_loop().time() >= deadline:
            raise SetupError("Approved, but the bridge did not connect in time.")
        await asyncio.sleep(0.5)


async def pair_world_cli(world_cli, foundry, allow_commands: Optional[List[str]] = None, wait_s: float = 20.0) -> Dict[str, Any]:
    """Pair (if not already paired) and return the relay seed for this Foundry session's origin."""
    allow = validate_allow(allow_commands or [])
    session = await _js(foundry, SESSION_JS)
    if not session.get("isGM"):
        raise SetupError("The engine's Foundry session is not a GM; pairing needs one.")

    paired_now = False
    if not await _bridge_connected(world_cli):
        await _pair(world_cli, foundry, session, wait_s)
        paired_now = True

    stored = await _js(foundry, READ_JS)
    if not stored.get("credentials") or not stored.get("clientId") or stored["credentials"] in ("{}", "null"):
        raise SetupError("The pairing is not stored in this browser session; nothing to seed.")
    entries = {"fvtt-world-cli.credentials": stored["credentials"], "fvtt-world-cli.clientId": stored["clientId"]}
    if allow:
        entries["fvtt-world-cli.commandPolicy"] = json.dumps({"version": 1, "overrides": {c: "allow" for c in allow}})
    seed = {session["origin"]: entries}
    return {
        "origin": session["origin"], "world": session["world"], "paired_now": paired_now,
        "allowed": allow, "seed": seed,
        "export": f"export RELAY_ENV_HEADLESS_LOCALSTORAGE_SEED={shlex.quote(json.dumps(seed, separators=(',', ':')))}",
    }
