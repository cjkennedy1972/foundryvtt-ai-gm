"""Players talking to an NPC directly: `/npc <name>: <message>`.

A player's ordinary chat goes through the GM turn — one large prompt, JSON actions,
the referee — which is the right path for what the party *does*. Asking a tavern
keeper a question is not that: it wants the NPC's own voice, quickly. This answers
as the NPC, from the NPC's record, what it remembers, the vault lore nearest the
question and the last few lines of this conversation, and returns plain dialogue for
the caller to speak in Foundry (voice and TTS included).
"""

import logging
from collections import deque
from typing import Awaitable, Callable, Deque, Dict, List, Optional, Tuple

from llm.router import ModelRouter
from npc.memory import NPCMemory
from npc.registry import NPCRecord, NPCRegistry

logger = logging.getLogger(__name__)

HISTORY_TURNS = 6
MEMORY_LINES = 5

SYSTEM_PROMPT = (
    "You are {name}, a character in a tabletop RPG, answering a player who speaks to you. "
    "Reply as {name} in one to three sentences of spoken dialogue only: no narration, no stage "
    "directions, no asterisks, no quotation marks, no JSON. Say only what {name} would know and "
    "be willing to say; for anything beyond that, deflect in character. Where the lore below "
    "disagrees with your own assumptions, the lore is right. Never mention being an AI or a game."
)


def parse_npc_chat(registry: NPCRegistry, text: str) -> Tuple[Optional[NPCRecord], str]:
    """Split `Name: message` or `Name message` into (NPC, message); (None, "") when no NPC matches.

    The colon form takes the name from a fuzzy lookup. Without a colon the longest
    registered name that starts the text wins, since names can hold spaces.
    """
    text = text.strip()
    if ":" in text:
        name, _, message = text.partition(":")
        found = registry.find_npc_by_name_fuzzy(name)
        return (found[1], message.strip()) if found and message.strip() else (None, "")
    lowered = text.lower()
    for npc in sorted(registry.list_npcs(), key=lambda n: len(n.npc_name), reverse=True):
        name = npc.npc_name.lower()
        if lowered.startswith(name + " "):
            return npc, text[len(name):].strip()
    return None, ""


class NPCChat:
    def __init__(
        self,
        registry: NPCRegistry,
        memory: NPCMemory,
        model_router: ModelRouter,
        lore: Optional[Callable[[str], Awaitable[str]]] = None,
    ):
        self.registry = registry
        self.memory = memory
        self.model_router = model_router
        self.lore = lore
        self._history: Dict[str, Deque[Tuple[str, str, str]]] = {}

    async def reply(self, campaign: str, npc: NPCRecord, speaker: str, message: str) -> str:
        context = await self._context(campaign, npc, message)
        text = await self.model_router.get("npc").generate_text(
            user_message=f"{speaker} says to you: {message}",
            system_prompt=SYSTEM_PROMPT.format(name=npc.npc_name),
            context=context,
        )
        reply = text.strip().strip('"').strip()
        self._history.setdefault(npc.npc_id, deque(maxlen=HISTORY_TURNS)).append((speaker, message, reply))
        return reply

    async def _context(self, campaign: str, npc: NPCRecord, message: str) -> str:
        parts: List[str] = [f"## WHO YOU ARE\n{self.registry.get_npc_context(npc.npc_id) or npc.npc_name}"]
        if npc.description:
            parts.append(npc.description)
        goals = [g.description for g in npc.goals if g.status == "active"]
        if goals:
            parts.append("What you want right now: " + "; ".join(goals))
        try:
            events = await self.memory.recall(campaign, npc.npc_id, limit=MEMORY_LINES)
        except Exception:
            logger.warning(f"[NPCChat] memory recall failed for {npc.npc_name}", exc_info=True)
            events = []
        if events:
            parts.append("What you remember: " + "; ".join(e.get("description") or e.get("type") for e in events))
        if self.lore:
            lore = await self.lore(f"{npc.npc_name} {message}")
            if lore:
                parts.append(lore)
        past = self._history.get(npc.npc_id)
        if past:
            parts.append("## THIS CONVERSATION SO FAR\n" + "\n".join(f"{s}: {m}\n{npc.npc_name}: {r}" for s, m, r in past))
        return "\n\n".join(parts)
