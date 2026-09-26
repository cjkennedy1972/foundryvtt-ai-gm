"""Semantic Vault RAG — inject campaign lore relevant to what a player said.

The player's whole message is the query. This used to pull capitalised words
and a fixed list of D&D keywords out of the message and search for each word
alone, so "I look for the innkeeper" searched for nothing, and a lone word
carries none of the meaning an embedding model is there to match. A similarity
floor keeps table chatter ("ok", "brb") from dragging in whichever notes
happen to be nearest.
"""

import logging
from dataclasses import dataclass
from typing import List, Set

logger = logging.getLogger(__name__)


@dataclass
class LoreInjection:
    """Injected lore with provenance."""
    text: str
    source: str  # e.g., "Locations/The Void's Maw"
    score: float  # 0-1 relevance


class SemanticRAG:
    def __init__(self, indexer, min_similarity: float = 0.6):
        """
        Args:
            indexer: SemanticIndexer instance
            min_similarity: cosine similarity a chunk needs to be injected.
                Model-specific: 0.6 separates play from chatter for
                qwen3-embedding-4b on a real campaign vault.
        """
        self.indexer = indexer
        self.min_similarity = min_similarity

    async def inject_lore(self, narrative: str, top_k: int = 3) -> List[LoreInjection]:
        """Up to top_k lore chunks relevant to `narrative`, one per note."""
        query = (narrative or "").strip()
        if not query:
            return []
        # Over-fetch so dropping repeats of one note still leaves top_k.
        results = await self.indexer.query(query, top_k=top_k * 2)
        injections: List[LoreInjection] = []
        seen: Set[str] = set()
        for r in results:
            # The indexer reports (cosine + 1) / 2; the floor is in cosine.
            if r.score * 2 - 1 < self.min_similarity or r.source in seen:
                continue
            seen.add(r.source)
            injections.append(LoreInjection(text=r.text, source=r.source, score=r.score))
            if len(injections) == top_k:
                break
        return injections
