"""Semantic Vault RAG — inject campaign lore relevant to what a player said.

The player's whole message is the query. This used to pull capitalised words
and a fixed list of D&D keywords out of the message and search for each word
alone, so "I look for the innkeeper" searched for nothing, and a lone word
carries none of the meaning an embedding model is there to match. A similarity
floor keeps table chatter ("ok", "brb") from dragging in whichever notes
happen to be nearest.
"""

import logging
import re
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
        # Over-fetch so dropping repeats still leaves top_k.
        results = await self.indexer.query(query, top_k=top_k * 3)
        injections: List[LoreInjection] = []
        seen: Set[str] = set()
        kept_words: List[Set[str]] = []
        for r in results:
            # The indexer reports (cosine + 1) / 2; the floor is in cosine.
            if r.score * 2 - 1 < self.min_similarity or r.source in seen:
                continue
            # The same fact in another note: enrichment writes a source's lore
            # into Worldbuilding, its own notes and the NPC or location note.
            words = _words(r.text)
            if any(_overlap(words, k) >= 0.6 for k in kept_words):
                continue
            seen.add(r.source)
            kept_words.append(words)
            injections.append(LoreInjection(text=r.text, source=r.source, score=r.score))
            if len(injections) == top_k:
                break
        return injections


def _words(text: str) -> Set[str]:
    return set(re.findall(r"[a-z']{4,}", text.lower()))


def _overlap(a: Set[str], b: Set[str]) -> float:
    """Shared words as a share of the smaller text's words."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0
