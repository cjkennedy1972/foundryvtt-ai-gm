"""One player's Foundry session, driven through the playwright transport.

Everything a bot does runs as its own Foundry user, so the world's permissions apply. A bot that
tries something its player could not do gets Foundry's own refusal back, which is what a playtest
should surface.
"""

import json
from typing import Any, Dict, List, Optional

from foundry.playwright_transport import PlaywrightTransport
from playtest.accounts import PlayerAccount

_MOVE_JS = """async ({id, x, y}) => {
  const t = canvas?.tokens?.get(id);
  if (!t) return {ok: false, error: `token ${id} is not on the active scene`};
  await t.document.update({x, y});
  return {ok: true};
}"""

_MESSAGES_JS = """(() => {
  return game.messages.contents.slice(-60).map(m => ({
    id: m.id,
    author: m.author?.name ?? null,
    content: m.content ?? "",
    timestamp: m.timestamp ?? 0,
  }));
})()"""

_ROSTER_JS = """(() => ({
  world: game.world.title,
  users: game.users.contents.map(u => ({name: u.name, isGM: !!u.isGM, active: !!u.active})),
}))()"""


class PlayerBot:
    def __init__(self, account: PlayerAccount, transport: Optional[PlaywrightTransport] = None):
        self.account = account
        self.transport = transport or PlaywrightTransport(login=(account.username, account.password))

    async def connect(self) -> None:
        await self.transport.connect()

    async def close(self) -> None:
        await self.transport.close()

    async def _eval(self, script: str) -> Any:
        return (await self.transport.send("execute-js", script=script)).get("result")

    async def say(self, text: str) -> Dict[str, Any]:
        return await self.transport.send("chat-send", content=text)

    async def roll(self, formula: str) -> Dict[str, Any]:
        return await self.transport.send(
            "roll", formula=formula, speaker=self.account.label, flavor=None, createChatMessage=True)

    async def move_token(self, token: str, x: float, y: float) -> Dict[str, Any]:
        script = f"return await ({_MOVE_JS})({json.dumps({'id': token, 'x': x, 'y': y})});"
        result = await self._eval(script)
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(f"move refused: {result}")
        return result

    async def messages(self) -> List[Dict[str, Any]]:
        result = await self._eval(f"return {_MESSAGES_JS};")
        return result if isinstance(result, list) else []

    async def roster(self) -> Dict[str, Any]:
        result = await self._eval(f"return {_ROSTER_JS};")
        return result if isinstance(result, dict) else {"world": "", "users": []}
