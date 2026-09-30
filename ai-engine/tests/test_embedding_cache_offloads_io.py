#!/usr/bin/env python3
"""CachedEmbeddings.embed must not do its disk IO on the event loop.

The cache is one small JSON file per text. SemanticIndexer.replace_chunks()
pushes a whole vault through embed() in batches, and a re-index happens while
people are playing — so a sweep that reads inline holds the loop for its whole
duration and nothing else runs: not the chat listener, not any /api request.

These assert the property rather than the implementation: a coroutine racing
alongside embed() has to get scheduled. If the sweep is inline, embed() reaches
no await point on a warm cache and the racer never runs at all.

Run:
    cd ai-engine && python -m pytest tests/test_embedding_cache_offloads_io.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vault.embeddings import CachedEmbeddings

TEXTS = [f"a chunk of campaign lore, number {i}" for i in range(200)]


class _StubProvider:
    """Returns a deterministic vector per text and counts its calls."""

    def __init__(self):
        self.batches = []

    async def embed(self, texts):
        self.batches.append(list(texts))
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    def get_dimension(self):
        return 3


async def _count_ticks_during(awaitable):
    """Run `awaitable`, returning (result, how many times a racer ran)."""
    ticks = 0

    async def racer():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    task = asyncio.create_task(racer())
    try:
        result = await awaitable
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    return result, ticks


@pytest.mark.asyncio
async def test_a_warm_cache_sweep_yields_to_the_loop(tmp_path):
    """The reported stall: every text is a cache hit, so the read sweep is
    the only work embed() does."""
    cached = CachedEmbeddings(_StubProvider(), cache_dir=str(tmp_path))
    await cached.embed(TEXTS)  # warm it
    assert cached.provider.batches == [TEXTS], "first pass should have been a full miss"
    cached.provider.batches.clear()

    embeddings, ticks = await _count_ticks_during(cached.embed(TEXTS))

    assert cached.provider.batches == [], "second pass should be served entirely from cache"
    assert len(embeddings) == len(TEXTS)
    assert ticks > 0, "embed() never yielded: the cache sweep ran on the event loop"


@pytest.mark.asyncio
async def test_a_cold_cache_write_sweep_yields_to_the_loop(tmp_path):
    cached = CachedEmbeddings(_StubProvider(), cache_dir=str(tmp_path))

    embeddings, ticks = await _count_ticks_during(cached.embed(TEXTS))

    assert len(embeddings) == len(TEXTS)
    assert ticks > 0


@pytest.mark.asyncio
async def test_cached_and_fresh_embeddings_stay_in_query_order(tmp_path):
    """Moving the sweep off the loop must not disturb the index alignment:
    callers pair the returned list with their input positionally."""
    cached = CachedEmbeddings(_StubProvider(), cache_dir=str(tmp_path))
    await cached.embed(["second"])  # only this one is warm

    out = await cached.embed(["first", "second", "third"])

    assert out == [
        [5.0, 1.0, 0.0],   # "first"
        [6.0, 1.0, 0.0],   # "second", from cache
        [5.0, 1.0, 0.0],   # "third"
    ]
    assert cached.provider.batches[-1] == ["first", "third"], "only the misses are embedded"


@pytest.mark.asyncio
async def test_a_corrupt_cache_entry_is_treated_as_a_miss(tmp_path):
    cached = CachedEmbeddings(_StubProvider(), cache_dir=str(tmp_path))
    await cached.embed(["lore"])
    # Truncate the entry the way an interrupted write would.
    path = cached._get_cache_path(cached._hash_text("lore"))
    path.write_text("{not json")
    cached.provider.batches.clear()

    out = await cached.embed(["lore"])

    assert out == [[4.0, 1.0, 0.0]]
    assert cached.provider.batches == [["lore"]], "a corrupt entry should be re-embedded"


@pytest.mark.asyncio
async def test_an_empty_batch_does_not_call_the_provider(tmp_path):
    cached = CachedEmbeddings(_StubProvider(), cache_dir=str(tmp_path))

    assert await cached.embed([]) == []
    assert cached.provider.batches == []
