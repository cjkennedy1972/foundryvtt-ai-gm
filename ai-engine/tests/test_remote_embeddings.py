"""Embeddings from an OpenAI-compatible server (LocalAI on a second host).

Semantic RAG could not work on a machine without sentence-transformers, yet
the indexer reported itself initialized and then failed inside every player
turn. And both the embedding cache and the index were keyed by chunk text
alone, so changing models would have mixed incompatible vectors.

Run:
    cd ai-engine && python -m pytest tests/test_remote_embeddings.py -v
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.startup as startup
import vault.embeddings as emb
from config import settings
from vault.indexer import SemanticIndexer


class FakeProvider:
    """Declares the wrong dimension, like an unknown model on a compatible server."""

    def __init__(self, dim=5, fail=False):
        self.dim, self.fail, self.calls = dim, fail, 0

    async def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise ConnectionError("embedding host down")
        return [[float(len(t) % 7 + i) for i in range(self.dim)] for t in texts]

    def get_dimension(self):
        return 1536


def test_the_index_takes_its_dimension_from_the_vectors(tmp_path):
    async def scenario():
        indexer = SemanticIndexer(FakeProvider(dim=5), index_path=str(tmp_path), cache_enabled=False)
        await indexer.add_chunks(["The Black Tower", "Mira's inn"], ["a", "b"])
        results = await indexer.query("tower", top_k=1)
        assert len(results) == 1

        reloaded = SemanticIndexer(FakeProvider(dim=5), index_path=str(tmp_path), cache_enabled=False)
        assert len(reloaded.chunks) == 2
    asyncio.run(scenario())


def test_a_compatible_server_gets_the_base_url_and_its_dimension_is_learned(monkeypatch):
    seen = {}

    async def scenario():
        provider = emb.OpenAIEmbeddings(api_key="", model="qwen3-embedding-4b",
                                        base_url="http://embed-host:8080/v1")
        seen["base_url"] = str(provider._client.base_url)
        assert provider.get_dimension() == 0  # unknown until the server answers

        async def create(input, model):
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 2560) for _ in input])

        monkeypatch.setattr(provider._client.embeddings, "create", create)
        vectors = await provider.embed(["a", "b"])
        assert len(vectors) == 2 and provider.get_dimension() == 2560
    asyncio.run(scenario())
    assert seen["base_url"].startswith("http://embed-host:8080/v1")


class _StubLoader:
    def __init__(self, semantic_indexer=None):
        self.semantic_indexer = semantic_indexer

    async def load(self, _campaign):
        return {}


def _build(monkeypatch, tmp_path, provider, model="qwen3-embedding-4b"):
    monkeypatch.setattr(settings, "vault_embeddings_enabled", True)
    monkeypatch.setattr(settings, "vault_embeddings_provider", "openai")
    monkeypatch.setattr(settings, "vault_embeddings_model", model)
    monkeypatch.setattr(settings, "vault_embeddings_base_url", "http://embed-host:8080/v1")
    monkeypatch.setattr(settings, "vault_embeddings_api_key", "")
    monkeypatch.setattr(settings, "vault_embeddings_cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "vault_index_path", str(tmp_path / "index"))
    monkeypatch.setattr(emb, "OpenAIEmbeddings", lambda **_kw: provider)
    monkeypatch.setattr(startup, "CampaignLoader", _StubLoader)
    state = SimpleNamespace()
    asyncio.run(startup.build_context(state))
    return state


def test_an_unreachable_provider_falls_back_once_at_startup(monkeypatch, tmp_path):
    state = _build(monkeypatch, tmp_path, FakeProvider(fail=True))
    assert state.semantic_indexer is None and state.semantic_rag is None


def test_each_model_gets_its_own_cache_and_index(monkeypatch, tmp_path):
    a = _build(monkeypatch, tmp_path, FakeProvider(), model="qwen3-embedding-4b")
    b = _build(monkeypatch, tmp_path, FakeProvider(), model="all-MiniLM-L6-v2")
    assert a.semantic_indexer.index_path != b.semantic_indexer.index_path
    assert a.semantic_indexer.provider.cache_dir != b.semantic_indexer.provider.cache_dir
    assert Path(a.semantic_indexer.index_path).name == "openai-qwen3-embedding-4b"


def test_the_llm_key_is_not_sent_to_a_different_embeddings_host(monkeypatch, tmp_path):
    captured = {}

    def fake_openai(**kw):
        captured.update(kw)
        return FakeProvider()

    monkeypatch.setattr(settings, "llm_api_key", "sk-narrator-secret")
    _build(monkeypatch, tmp_path, FakeProvider())
    monkeypatch.setattr(emb, "OpenAIEmbeddings", fake_openai)
    asyncio.run(startup.build_context(SimpleNamespace()))
    assert captured["api_key"] == "" and captured["base_url"] == "http://embed-host:8080/v1"
