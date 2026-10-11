"""Embedding provider selection at startup: primary first, configured fallback second, else raise."""

import asyncio

import pytest

from api import startup
from config import settings


def _set(monkeypatch, primary, fallback):
    monkeypatch.setattr(settings, "vault_embeddings_provider", primary)
    monkeypatch.setattr(settings, "vault_embeddings_model", "Qwen3-VL-Embedding-2B-GGUF")
    monkeypatch.setattr(settings, "vault_embeddings_fallback_provider", fallback)
    monkeypatch.setattr(settings, "vault_embeddings_fallback_model", "all-MiniLM-L6-v2")


def _probe(failing=(), dims=None):
    calls = []

    async def fake(provider, model):
        calls.append((provider, model))
        if provider in failing:
            raise ConnectionError(f"{provider} unreachable")
        return f"emb-{provider}", (dims or {}).get(provider, 2048)

    return fake, calls


def test_primary_used_when_it_works(monkeypatch):
    _set(monkeypatch, "openai", "local")
    fake, calls = _probe()
    monkeypatch.setattr(startup, "_probe_embeddings", fake)
    emb, provider, model, dim = asyncio.run(startup._select_embeddings())
    assert (emb, provider, model, dim) == ("emb-openai", "openai", "Qwen3-VL-Embedding-2B-GGUF", 2048)
    assert calls == [("openai", "Qwen3-VL-Embedding-2B-GGUF")]


def test_fallback_used_when_primary_is_down(monkeypatch):
    _set(monkeypatch, "openai", "local")
    fake, calls = _probe(failing={"openai"}, dims={"local": 384})
    monkeypatch.setattr(startup, "_probe_embeddings", fake)
    emb, provider, model, dim = asyncio.run(startup._select_embeddings())
    assert (provider, model, dim) == ("local", "all-MiniLM-L6-v2", 384)
    assert calls[-1] == ("local", "all-MiniLM-L6-v2")


def test_no_fallback_configured_raises_the_primary_error(monkeypatch):
    _set(monkeypatch, "openai", "")
    fake, _ = _probe(failing={"openai"})
    monkeypatch.setattr(startup, "_probe_embeddings", fake)
    with pytest.raises(ConnectionError, match="openai unreachable"):
        asyncio.run(startup._select_embeddings())


def test_fallback_also_failing_raises(monkeypatch):
    _set(monkeypatch, "openai", "local")
    fake, _ = _probe(failing={"openai", "local"})
    monkeypatch.setattr(startup, "_probe_embeddings", fake)
    with pytest.raises(ConnectionError, match="local unreachable"):
        asyncio.run(startup._select_embeddings())


def test_fallback_equal_to_primary_is_not_retried(monkeypatch):
    _set(monkeypatch, "local", "local")
    fake, calls = _probe(failing={"local"})
    monkeypatch.setattr(startup, "_probe_embeddings", fake)
    with pytest.raises(ConnectionError):
        asyncio.run(startup._select_embeddings())
    assert len(calls) == 1
