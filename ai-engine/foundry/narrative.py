"""Outbound narrative delivery for the Foundry-only game surface.

The protocol is deliberately about in-world artifacts rather than transport:
future world simulation can deliver narration, durable journals, chat cards,
and actor effects without knowing about the Foundry client.  Foundry remains
the only implementation and the only supported play surface.
"""

from typing import List, Optional, Protocol


class NarrativeSink(Protocol):
    """Named delivery path for AI and simulated-world output."""

    async def narration(
        self, text: str, *, speaker: str = "GM", whisper: Optional[List[str]] = None
    ) -> dict:
        """Send player-visible narrative text."""

    async def journal_entry(self, name: str, content: str) -> dict:
        """Create a durable in-world journal artifact."""

    async def chat_card(self, content: str, *, speaker: str = "GM") -> dict:
        """Send richer chat-card content."""

    async def effect(
        self, actor_uuid: str, status_id: str, *, active: bool = True
    ) -> dict:
        """Apply or remove an actor effect in the world."""


class FoundryNarrativeSink:
    """Deliver narrative artifacts through the existing Foundry client."""

    def __init__(self, foundry):
        self.foundry = foundry

    async def narration(
        self, text: str, *, speaker: str = "GM", whisper: Optional[List[str]] = None
    ) -> dict:
        return await self.foundry.chat_message(text, speaker=speaker, whisper=whisper)

    async def journal_entry(self, name: str, content: str) -> dict:
        return await self.foundry.create_entity(
            "JournalEntry",
            {
                "name": name,
                "pages": [{
                    "name": name,
                    "type": "text",
                    "text": {"content": content, "format": 1},
                }],
            },
        )

    async def chat_card(self, content: str, *, speaker: str = "GM") -> dict:
        return await self.foundry.chat_message(content, speaker=speaker)

    async def effect(
        self, actor_uuid: str, status_id: str, *, active: bool = True
    ) -> dict:
        if active:
            return await self.foundry.add_effect(actor_uuid, status_id)
        return await self.foundry.remove_effect(actor_uuid, status_id)
