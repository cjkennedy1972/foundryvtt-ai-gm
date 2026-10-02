"""Behavioral coverage for vault/indexer.py and vault/embeddings.py (review pass)."""

import asyncio
import json
import sys
import types

import pytest

import vault.embeddings as emb
from vault import indexer as ix
from vault.indexer import QueryCache, SemanticIndexer


def run(coro):
    return asyncio.run(coro)


class Provider:
    """Embeds by a tiny deterministic scheme: [len, vowels]; '' text -> no vector."""

    def __init__(self):
        self.batches = []

    async def embed(self, texts):
        self.batches.append(list(texts))
        out = []
        for t in texts:
            if t.startswith("FAIL"):
                out.append([])
            else:
                out.append([float(len(t)), float(sum(c in "aeiou" for c in t)), 1.0])
        return out

    def get_dimension(self):
        return 3


@pytest.fixture(autouse=True)
def no_hnsw(monkeypatch):
    # Force the pure-Python path unless a test installs a fake hnswlib.
    monkeypatch.setitem(sys.modules, "hnswlib", None)


# --- QueryCache ----------------------------------------------------------------

def test_query_cache_ttl_lru_and_stats(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ix.time, "time", lambda: now[0])
    c = QueryCache(max_size=2, ttl_seconds=10)
    assert c.get("a") is None
    c.set("a", [1])
    c.set("b", [2])
    assert c.get("a") == [1]          # touching "a" makes "b" the oldest
    c.set("c", [3])
    assert c.get("b") is None and c.get("a") == [1] and c.get("c") == [3]
    c.set("a", [9])                   # overwrite keeps size at 2
    assert c.stats() == {"size": 2, "max_size": 2, "ttl_seconds": 10}
    now[0] += 11
    assert c.get("a") is None         # expired entries are dropped, not served
    assert c.stats()["size"] == 1
    c.clear()
    assert c.stats()["size"] == 0


# --- SemanticIndexer -------------------------------------------------------------

def test_normalize_query_collapses_case_punctuation_whitespace(tmp_path):
    idx = SemanticIndexer(Provider(), index_path=str(tmp_path / "i"))
    assert idx._normalize_query("  Who IS  Mara?! ") == "who is mara"


def test_add_chunks_skips_failed_embeddings_persists_and_reloads(tmp_path):
    async def go():
        p = Provider()
        idx = SemanticIndexer(p, index_path=str(tmp_path / "i"))
        await idx.add_chunks([], [])
        assert p.batches == []
        await idx.add_chunks(["alpha beta", "FAIL me", "gamma"], ["s1", "s2", "s3"])
        assert idx.chunks == ["alpha beta", "gamma"]
        assert [m["source"] for m in idx.metadata] == ["s1", "s3"]  # source stays paired with its chunk
        assert len(idx._norms) == 2
        again = SemanticIndexer(Provider(), index_path=str(tmp_path / "i"))
        assert again.chunks == idx.chunks and again.embeddings == idx.embeddings
        assert again.get_stats()["total_chunks"] == 2 and again.get_stats()["embedding_dim"] == 3
        return idx
    run(go())


def test_corrupt_cached_index_starts_fresh(tmp_path):
    d = tmp_path / "i"
    d.mkdir()
    (d / "chunks.json").write_text("{not json")
    (d / "embeddings.json").write_text("[]")
    idx = SemanticIndexer(Provider(), index_path=str(d))
    assert idx.chunks == [] and idx.embeddings == []


def test_index_settlement_builds_sourced_chunks(tmp_path):
    async def go():
        p = Provider()
        idx = SemanticIndexer(p, index_path=str(tmp_path / "i"))
        await idx.index_settlement("redmarch", {
            "character": "A river town",
            "buildings": {"inn": {"description": "Warm"}, "forge": {"services": "Smithing"}, "hut": {}},
            "npcs": {"mara": {"occupation": "smith", "description": "Stern", "goals": "Revenge"},
                     "bob": {}},
        })
        got = dict(zip([m["source"] for m in idx.metadata], idx.chunks))
        assert got == {
            "settlement:redmarch:description": "Settlement redmarch: A river town",
            "settlement:redmarch:building:inn": "Building inn in redmarch: Warm",
            "settlement:redmarch:building:forge": "Building forge in redmarch: Smithing",  # services fallback
            "settlement:redmarch:npc:mara": "NPC mara (smith) Stern Goals: Revenge",
            "settlement:redmarch:npc:bob": "NPC bob (unknown)",
        }
    run(go())


def test_replace_chunks_replaces_batches_and_invalidates_cache(tmp_path):
    async def go():
        p = Provider()
        idx = SemanticIndexer(p, index_path=str(tmp_path / "i"))
        await idx.add_chunks(["old lore"], ["old"])
        await idx.query("old lore", top_k=1)
        assert idx.cache.stats()["size"] == 1
        texts = [f"chunk number {i}" for i in range(5)] + ["FAIL x"]
        await idx.replace_chunks(texts, [f"s{i}" for i in range(6)], batch_size=4)
        assert [len(b) for b in p.batches[-2:]] == [4, 2]  # embedded in batches
        assert "old lore" not in idx.chunks and len(idx.chunks) == 5  # failed embedding dropped
        assert [m["source"] for m in idx.metadata] == [f"s{i}" for i in range(5)]
        assert idx.cache.stats()["size"] == 0  # stale answers about the old lore are gone
        await idx.replace_chunks([], [])
        assert idx.chunks == [] and idx.index is None
        assert json.loads((tmp_path / "i" / "chunks.json").read_text())["chunks"] == []
    run(go())


def test_query_ranks_by_cosine_caches_and_handles_empty(tmp_path):
    async def go():
        p = Provider()
        idx = SemanticIndexer(p, index_path=str(tmp_path / "i"))
        assert await idx.query("anything") == []   # empty index: no embed call
        assert p.batches == []
        await idx.add_chunks(["aaaa", "bbbbbbbbbbbbbbbbbbbb"], ["near", "far"])
        n = len(p.batches)
        r = await idx.query("Aaaa!", top_k=1)
        assert [x.source for x in r] == ["near"] and 0 <= r[0].score <= 1 and r[0].embedding
        await idx.query("aaaa", top_k=1)          # same normalised key + top_k: served from cache
        assert len(p.batches) == n + 1
        await idx.query("aaaa", top_k=2)          # different top_k is a different key
        assert len(p.batches) == n + 2
        assert idx.get_stats()["cache"]["size"] == 2
        idx.clear_cache()
        assert idx.cache.stats()["size"] == 0
        # a query the provider can't embed returns nothing and isn't cached
        assert await idx.query("FAIL query") == []
        assert idx.cache.stats()["size"] == 0
    run(go())


def test_cache_disabled(tmp_path):
    async def go():
        p = Provider()
        idx = SemanticIndexer(p, index_path=str(tmp_path / "i"), cache_enabled=False)
        await idx.add_chunks(["aaaa"], ["s"])
        await idx.query("aaaa")
        await idx.query("aaaa")
        assert len(p.batches) == 3 and "cache" not in idx.get_stats()
        idx.clear_cache()
        await idx.replace_chunks(["aaaa"], ["s"])
    run(go())


def test_linear_search_skips_zero_norm_and_dimension_mismatch(tmp_path):
    idx = SemanticIndexer(Provider(), index_path=str(tmp_path / "i"))
    idx.chunks = ["zero", "short", "good", "opposite"]
    idx.metadata = [{"source": "z"}, {}, {"source": "g"}, {"source": "o"}]
    idx.embeddings = [[0.0, 0.0], [1.0], [1.0, 0.0], [-1.0, 0.0]]
    idx._build_hnsw_index()
    res = idx._search_linear([1.0, 0.0], 5)
    assert [(r.source, round(r.score, 3)) for r in res] == [("g", 1.0), ("o", 0.0)]
    assert idx._search_linear([0.0, 0.0], 5) == []
    idx.chunks.append("x")
    idx.metadata.append({})
    idx.embeddings.append([0.5, 0.5])
    idx._norms.append(0.7)
    assert idx._search_linear([1.0, 0.0], 5)[1].source == "unknown"  # metadata without a source


def test_hnsw_path_with_fake_library(tmp_path, monkeypatch):
    built = {}

    class FakeIndex:
        def __init__(self, space, dim):
            built.update(space=space, dim=dim)

        def init_index(self, max_elements, ef_construction, M):
            built["max"] = max_elements

        def add_items(self, vecs, ids):
            built["ids"] = ids

        def knn_query(self, q, k):
            built["k"] = k
            return [[1, 0, 99]], [[0.0, 1.0, 0.5]]  # label 99 is stale and must be ignored

    monkeypatch.setitem(sys.modules, "hnswlib", types.SimpleNamespace(Index=FakeIndex))
    idx = SemanticIndexer(Provider(), index_path=str(tmp_path / "i"))
    idx.chunks, idx.metadata = ["a", "b"], [{"source": "A"}, {"source": "B"}]
    idx.embeddings = [[1.0, 2.0, 3.0], [3.0, 2.0, 1.0]]
    idx._build_hnsw_index()
    assert built == {"space": "cosine", "dim": 3, "max": 2, "ids": [0, 1]}
    res = idx._search_hnsw([1.0, 1.0, 1.0], top_k=10)
    assert built["k"] == 2  # never asks for more than exist
    assert [(r.source, r.score) for r in res] == [("B", 1.0), ("A", 0.5)]  # cosine distance -> [0,1]

    def boom(q, k):
        raise RuntimeError("index corrupt")
    idx.index.knn_query = boom
    assert idx._search_hnsw([1.0], 1) == []  # a broken index degrades to no results, not an exception


def test_save_failure_is_logged_not_raised(tmp_path, monkeypatch):
    idx = SemanticIndexer(Provider(), index_path=str(tmp_path / "i"))
    monkeypatch.setattr(idx, "index_path", tmp_path / "does" / "not" / "exist")
    idx._save_index()  # must not raise
    assert not (tmp_path / "does").exists()  # nothing was written anywhere


# --- embeddings ------------------------------------------------------------------

def test_openai_embeddings_learns_dimension_and_defaults(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
            self.embeddings = types.SimpleNamespace(create=self.create)

        async def create(self, input, model):
            captured["input"], captured["model"] = input, model
            return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.1] * 7) for _ in input])

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=FakeClient))
    e = emb.OpenAIEmbeddings(api_key="", model="custom", base_url="")
    assert captured["api_key"] == "unused" and captured["base_url"] is None and captured["max_retries"] == 1
    assert e.get_dimension() == 0
    out = run(e.embed(["a", "b"]))
    assert len(out) == 2 and e.get_dimension() == 7 and captured["model"] == "custom"
    assert emb.OpenAIEmbeddings("k").get_dimension() == 1536
    assert emb.OpenAIEmbeddings("k", model="text-embedding-3-large").get_dimension() == 3072


def test_ollama_embeddings_http_failures_become_empty_vectors(monkeypatch):
    import httpx

    def handler(request):
        body = json.loads(request.content)
        if body["prompt"] == "boom":
            raise httpx.ConnectError("down")
        if body["prompt"] == "bad":
            return httpx.Response(500)
        return httpx.Response(200, json={"embedding": [1.0, 2.0]})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    e = emb.OllamaEmbeddings(base_url="http://o.test", model="m")
    assert run(e.embed(["ok", "boom", "bad", "ok"])) == [[1.0, 2.0], [], [], [1.0, 2.0]]
    assert e.get_dimension() == 768


def test_local_embeddings_missing_dependency_raises_directive_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    e = emb.LocalEmbeddings()
    with pytest.raises(ImportError, match="requirements-embeddings.txt"):
        run(e.embed(["x"]))
    with pytest.raises(ImportError):
        e.get_dimension()


def test_local_embeddings_opt_in_fallback_is_deterministic_and_normalised(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    e = emb.LocalEmbeddings(allow_fallback=True)
    a1, a2, b = run(e.embed(["hello", "hello", "world"]))
    assert a1 == a2 != b and len(a1) == 384
    assert abs(sum(v * v for v in a1) - 1.0) < 1e-9
    assert e.get_dimension() == 384 and e._use_fallback is True
    run(e.embed(["again"]))  # second call must not re-warn/reload; still works


def test_local_embeddings_uses_model_off_the_loop(monkeypatch):
    class Arr:
        def __init__(self, v):
            self.v = v

        def tolist(self):
            return self.v

    class Model:
        def __init__(self, name):
            self.name = name

        def get_sentence_embedding_dimension(self):
            return 2

        def encode(self, texts, convert_to_numpy):
            assert convert_to_numpy is True
            return [Arr([float(len(t)), 0.0]) for t in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=Model))
    e = emb.LocalEmbeddings(model="m")
    assert e.get_dimension() == 2
    e2 = emb.LocalEmbeddings(model="m")
    assert run(e2.embed(["abc", "de"])) == [[3.0, 0.0], [2.0, 0.0]] and e2.get_dimension() == 2


def test_cached_embeddings_only_embeds_misses_keeps_order_and_survives_corruption(tmp_path):
    class Inner(emb.EmbeddingProvider):
        def __init__(self):
            self.seen = []

        async def embed(self, texts):
            self.seen.append(list(texts))
            return [[] if t == "bad" else [float(len(t))] for t in texts]

        def get_dimension(self):
            return 1

    async def go():
        inner = Inner()
        c = emb.CachedEmbeddings(inner, cache_dir=str(tmp_path / "c"))
        assert await c.embed(["aa", "b"]) == [[2.0], [1.0]]
        assert await c.embed(["b", "ccc", "aa"]) == [[1.0], [3.0], [2.0]]
        assert inner.seen == [["aa", "b"], ["ccc"]]              # warm entries never re-embedded
        assert await c.embed(["bad", "aa"]) == [[], [2.0]]       # failed embed -> [] and not cached
        assert await c.embed(["bad"]) == [[]] and inner.seen[-1] == ["bad"]
        c._get_cache_path(c._hash_text("aa")).write_text("{corrupt")
        assert await c.embed(["aa"]) == [[2.0]] and inner.seen[-1] == ["aa"]  # corrupt entry = miss
        assert c.get_dimension() == 1
        assert c._hash_text("x") == c._hash_text("x") and len(c._hash_text("x")) == 16
        c.cache_dir = tmp_path / "gone"  # unwritable cache must not fail the embed
        assert await c.embed(["zz"]) == [[2.0]]
    run(go())
