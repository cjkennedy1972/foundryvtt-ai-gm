"""Semantic Vault RAG — inject campaign lore relevant to what a player said.

The player's whole message is the query. This used to pull capitalised words
and a fixed list of D&D keywords out of the message and search for each word
alone, so "I look for the innkeeper" searched for nothing, and a lone word
carries none of the meaning an embedding model is there to match. A similarity
floor keeps table chatter ("ok", "brb") from dragging in whichever notes
happen to be nearest.
"""

import logging
import math
from dataclasses import dataclass, field
from operator import mul
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LoreInjection:
    """Injected lore with provenance."""
    text: str
    source: str  # e.g., "Locations/The Void's Maw"
    score: float  # 0-1 relevance
    # Other notes that state the same thing: a fact several notes or sources
    # agree on is corroborated, and usually load-bearing for the setting.
    also_in: List[str] = field(default_factory=list)


class SemanticRAG:
    def __init__(self, indexer, min_similarity: float = 0.6, duplicate_similarity: float = 0.8):
        """
        Args:
            indexer: SemanticIndexer instance
            min_similarity: cosine similarity a chunk needs to be injected.
                Model-specific: 0.6 separates play from chatter for
                qwen3-embedding-4b on a real campaign vault.
            duplicate_similarity: cosine at which two chunks say the same
                thing. For qwen3-embedding-4b one fact reworded scored
                0.90-0.95 and related-but-different facts 0.51-0.62.
        """
        self.indexer = indexer
        self.min_similarity = min_similarity
        self.duplicate_similarity = duplicate_similarity

    async def inject_lore(self, narrative: str, top_k: int = 3) -> List[LoreInjection]:
        """Up to top_k lore chunks relevant to `narrative`, one per note and
        one per meaning."""
        query = (narrative or "").strip()
        if not query:
            return []
        # Over-fetch so dropping repeats still leaves top_k.
        results = await self.indexer.query(query, top_k=top_k * 3)
        kept: List[tuple] = []  # (injection, unit vector)
        for r in results:
            # The indexer reports (cosine + 1) / 2; the floor is in cosine.
            if r.score * 2 - 1 < self.min_similarity:
                continue
            vec = _unit(r.embedding)
            # Same note, or the same meaning in another note (enrichment
            # writes a source's lore into Worldbuilding, its own notes and
            # the NPC or location note): keep the best, credit the rest.
            twin = next((inj for inj, v in kept if inj.source == r.source
                         or (vec and v and _dot(vec, v) >= self.duplicate_similarity)), None)
            if twin:
                if r.source != twin.source and r.source not in twin.also_in:
                    twin.also_in.append(r.source)
                continue
            if len(kept) < top_k:
                kept.append((LoreInjection(text=r.text, source=r.source, score=r.score), vec))
        return [inj for inj, _ in kept]


def _unit(vec: Optional[List[float]]) -> Optional[List[float]]:
    if not vec:
        return None
    norm = math.sqrt(sum(map(mul, vec, vec)))
    return [x / norm for x in vec] if norm else None


def _dot(a: List[float], b: List[float]) -> float:
    return sum(map(mul, a, b))
