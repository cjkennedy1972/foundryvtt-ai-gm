"""Semantic Vault RAG — inject context-aware lore into LLM responses.

Extracts entities from narrative context, queries vault for semantic matches,
deduplicates results, and annotates with source attribution.
"""

import logging
import re
import time
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LoreInjection:
    """Injected lore with provenance."""
    text: str
    source: str  # e.g., "settlement:redmarch:tavern"
    score: float  # 0-1 relevance


class EntityExtractor:
    """Regex-based extraction of proper nouns, places, D&D keywords."""

    D_D_KEYWORDS = {
        "dragon", "wizard", "fighter", "rogue", "cleric", "paladin",
        "ranger", "bard", "sorcerer", "monk", "tavern", "dungeon",
        "castle", "tower", "shrine", "temple", "crypt", "vault",
        "lich", "goblin", "orc", "elf", "dwarf", "halfling",
        "tiefling", "dragonborn", "gnome", "human", "magic",
        "spell", "potion", "artifact", "treasure", "gold", "silver"
    }

    def extract_entities(self, text: str) -> Set[str]:
        """Extract names, places, and D&D keywords.

        Returns: set of normalized entity strings.
        """
        entities = set()

        # Capitalized words (names, places)
        capitalized = re.findall(r'\b[A-Z][a-z]+\b', text)
        entities.update(capitalized)

        # D&D keywords (case-insensitive)
        for word in re.findall(r'\b\w+\b', text.lower()):
            if word in self.D_D_KEYWORDS:
                entities.add(word)

        return entities


class SemanticRAG:
    """Orchestrates semantic queries with debouncing and deduplication."""

    def __init__(self, indexer, debounce_seconds: float = 30.0):
        """
        Args:
            indexer: SemanticIndexer instance
            debounce_seconds: min time between queries for same entity
        """
        self.indexer = indexer
        self.debounce_seconds = debounce_seconds
        self.extractor = EntityExtractor()

        # Debounce tracking: entity -> last_query_time
        self._last_queries: Dict[str, float] = {}
        self._dedup_cache: Dict[str, List[LoreInjection]] = {}

    async def inject_lore(self, narrative: str, top_k: int = 3) -> List[LoreInjection]:
        """Extract entities and query vault for semantic matches.

        Args:
            narrative: Current scene/turn narrative
            top_k: Max results per entity

        Returns: List of LoreInjection with source attribution.
        """
        entities = self.extractor.extract_entities(narrative)
        if not entities:
            return []

        # Debounce and batch queries
        queries_to_run = []
        for entity in entities:
            last_time = self._last_queries.get(entity, 0)
            if time.time() - last_time > self.debounce_seconds:
                queries_to_run.append(entity)
                self._last_queries[entity] = time.time()

        if not queries_to_run:
            # Return cached results from recent queries
            results = []
            for entity in entities:
                if entity in self._dedup_cache:
                    results.extend(self._dedup_cache[entity][:top_k])
            return results

        # Batch query vault
        if queries_to_run:
            batch_results = await self.indexer.query_batch(queries_to_run, top_k=top_k)

            # Convert to LoreInjection and cache
            for entity, results in zip(queries_to_run, batch_results):
                injections = [
                    LoreInjection(
                        text=r.text,
                        source=r.source,
                        score=r.score
                    )
                    for r in results
                ]
                self._dedup_cache[entity] = injections

        # Gather all results
        results = []
        seen_sources: Set[str] = set()
        for entity in entities:
            if entity in self._dedup_cache:
                for injection in self._dedup_cache[entity]:
                    if injection.source not in seen_sources:
                        results.append(injection)
                        seen_sources.add(injection.source)
                        if len(results) >= top_k * 2:  # Limit total
                            break

        return results[:top_k * 2]

    def clear_cache(self):
        """Clear debounce and dedup cache."""
        self._last_queries.clear()
        self._dedup_cache.clear()
        logger.info("Semantic RAG cache cleared")
