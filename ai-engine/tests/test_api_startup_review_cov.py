"""Behavioural coverage for the builders and teardown in api/startup.py."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import startup
from config import settings


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    # Never reach real embedding providers, Foundry, the relay or the network.
    monkeypatch.setattr(settings, "vault_embeddings_enabled", False)


# ------------------------------------------------------- check_admin_exposure

class TestAdminExposure:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_without_token_is_allowed(self, monkeypatch, host):
        monkeypatch.setattr(settings, "admin_host", host)
        monkeypatch.setattr(settings, "admin_token", "")
        assert startup.check_admin_exposure() is None

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::", "", "example.com"])
    def test_network_bind_without_token_refuses_to_start(self, monkeypatch, host):
        monkeypatch.setattr(settings, "admin_host", host)
        monkeypatch.setattr(settings, "admin_token", "")
        with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
            startup.check_admin_exposure()

    def test_network_bind_with_token_is_allowed(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_host", "0.0.0.0")
        monkeypatch.setattr(settings, "admin_token", "tok")
        assert startup.check_admin_exposure() is None


# ----------------------------------------------------------- build_context

class _FakeEmbeddings:
    created = []

    def __init__(self, *a, **k):
        self.args, self.kwargs = a, k
        _FakeEmbeddings.created.append(self)

    async def embed(self, texts):
        return self.probe


class _Good(_FakeEmbeddings):
    probe = [[0.1, 0.2, 0.3]]


class _Empty(_FakeEmbeddings):
    probe = [[]]


class _Cached:
    def __init__(self, inner, cache_dir):
        self.inner, self.cache_dir = inner, cache_dir


class _Indexer:
    def __init__(self, provider, index_path, **kw):
        self.provider, self.index_path, self.kw = provider, index_path, kw


@pytest.fixture
def embeds(monkeypatch, tmp_path):
    import vault.embeddings as emb
    import vault.indexer as idx
    import vault.vault_semantic_rag as rag

    _FakeEmbeddings.created.clear()
    for name in ("LocalEmbeddings", "OpenAIEmbeddings", "OllamaEmbeddings"):
        monkeypatch.setattr(emb, name, _Good)
    monkeypatch.setattr(emb, "CachedEmbeddings", _Cached)
    monkeypatch.setattr(idx, "SemanticIndexer", _Indexer)
    monkeypatch.setattr(rag, "SemanticRAG", lambda indexer, **kw: SimpleNamespace(indexer=indexer, **kw))
    monkeypatch.setattr(startup, "CampaignLoader", lambda semantic_indexer=None: _Loader(semantic_indexer))
    monkeypatch.setattr(settings, "vault_embeddings_enabled", True)
    monkeypatch.setattr(settings, "vault_embeddings_cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "vault_index_path", str(tmp_path / "index"))
    monkeypatch.setattr(settings, "vault_embeddings_model", "org/My Model:v1")
    return emb


class _Loader:
    def __init__(self, indexer):
        self.indexer = indexer
        self.loaded = None

    async def load(self, name):
        self.loaded = name


class TestBuildContext:
    @pytest.mark.asyncio
    async def test_disabled_embeddings_fall_back_to_keyword_search(self, monkeypatch):
        monkeypatch.setattr(startup, "CampaignLoader", lambda semantic_indexer=None: _Loader(semantic_indexer))
        monkeypatch.setattr(settings, "default_campaign", "Krynn")
        state = SimpleNamespace(npc_registry=None)
        await startup.build_context(state)
        assert state.semantic_indexer is None and state.semantic_rag is None
        assert state.campaign_loader.loaded == "Krynn"
        assert state.npc_registry is not None and state.personality_engine is not None

    @pytest.mark.parametrize("provider,cls", [
        ("local", "LocalEmbeddings"), ("ollama", "OllamaEmbeddings"), ("openai", "OpenAIEmbeddings")])
    @pytest.mark.asyncio
    async def test_each_provider_builds_a_per_model_index(self, embeds, monkeypatch, provider, cls):
        monkeypatch.setattr(settings, "vault_embeddings_provider", provider)
        monkeypatch.setattr(settings, "vault_embeddings_api_key", "k")
        state = SimpleNamespace()
        await startup.build_context(state)
        assert state.semantic_indexer is not None and state.semantic_rag.indexer is state.semantic_indexer
        # per-model directories: a model change must not reuse another model's vectors
        expected = f"{provider}-org_My_Model_v1"
        assert Path(state.semantic_indexer.index_path).name == expected
        assert Path(state.semantic_indexer.provider.cache_dir).name == expected
        assert state.campaign_loader.indexer is state.semantic_indexer

    @pytest.mark.asyncio

    async def test_openai_key_not_sent_to_custom_base_url(self, embeds, monkeypatch):
        monkeypatch.setattr(settings, "vault_embeddings_provider", "openai")
        monkeypatch.setattr(settings, "vault_embeddings_api_key", "")
        monkeypatch.setattr(settings, "llm_api_key", "sk-llm-secret")
        monkeypatch.setattr(settings, "vault_embeddings_base_url", "http://other.example/v1")
        await startup.build_context(SimpleNamespace())
        assert _FakeEmbeddings.created[-1].kwargs["api_key"] == ""

    @pytest.mark.asyncio

    async def test_openai_falls_back_to_llm_key_without_base_url(self, embeds, monkeypatch):
        monkeypatch.setattr(settings, "vault_embeddings_provider", "openai")
        monkeypatch.setattr(settings, "vault_embeddings_api_key", "")
        monkeypatch.setattr(settings, "vault_embeddings_base_url", "")
        monkeypatch.setattr(settings, "llm_api_key", "sk-llm")
        await startup.build_context(SimpleNamespace())
        assert _FakeEmbeddings.created[-1].kwargs["api_key"] == "sk-llm"

    @pytest.mark.parametrize("setup", ["no_key", "unknown_provider", "empty_probe"])
    @pytest.mark.asyncio
    async def test_misconfiguration_degrades_to_keyword_search(self, embeds, monkeypatch, caplog, setup):
        if setup == "no_key":
            monkeypatch.setattr(settings, "vault_embeddings_provider", "openai")
            monkeypatch.setattr(settings, "vault_embeddings_api_key", "")
            monkeypatch.setattr(settings, "vault_embeddings_base_url", "")
            monkeypatch.setattr(settings, "llm_api_key", "")
        elif setup == "unknown_provider":
            monkeypatch.setattr(settings, "vault_embeddings_provider", "carrier-pigeon")
        else:
            monkeypatch.setattr(settings, "vault_embeddings_provider", "local")
            monkeypatch.setattr(embeds, "LocalEmbeddings", _Empty)
        state = SimpleNamespace()
        with caplog.at_level(logging.WARNING, logger="ai-gm"):
            await startup.build_context(state)
        assert state.semantic_indexer is None and state.semantic_rag is None
        assert "Falling back to keyword search" in caplog.text
        assert state.campaign_loader is not None  # startup still completes


# --------------------------------------------------------------- build_tts

class TestBuildTts:
    def _patch(self, monkeypatch):
        import tts.playback as pb
        calls = []
        monkeypatch.setattr(pb, "configure", lambda *a, **k: calls.append((a, k)))
        return calls

    def test_disabled(self, monkeypatch):
        calls = self._patch(monkeypatch)
        monkeypatch.setattr(settings, "tts_enabled", False)
        state = SimpleNamespace(npc_registry=None)
        startup.build_tts(state)
        assert state.tts_service is None and calls == []

    def test_browser_engine_deploys_module_and_has_no_server(self, monkeypatch):
        import foundry.module_deploy as md
        calls = self._patch(monkeypatch)
        deployed = []
        monkeypatch.setattr(md, "deploy_aigm_tts", lambda path: deployed.append(path) or True)
        monkeypatch.setattr(settings, "tts_enabled", True)
        monkeypatch.setattr(settings, "tts_engine", "browser")
        monkeypatch.setattr(settings, "foundry_modules_path", "/mods")
        reg = object()
        state = SimpleNamespace(npc_registry=reg)
        startup.build_tts(state)
        assert state.tts_service is None and deployed == ["/mods"]
        assert calls[0][0] == (None, reg) and calls[0][1]["engine"] == "browser"

    def test_server_engine_builds_service_with_default_or_custom_host(self, monkeypatch):
        calls = self._patch(monkeypatch)
        made = []
        monkeypatch.setattr(startup, "TTSService", lambda **kw: made.append(kw) or SimpleNamespace(**kw))
        monkeypatch.setattr(settings, "tts_enabled", True)
        monkeypatch.setattr(settings, "tts_engine", "server")
        monkeypatch.setattr(settings, "admin_port", 18080)
        monkeypatch.setattr(settings, "tts_engine_host", "")
        state = SimpleNamespace(npc_registry=None)
        startup.build_tts(state)
        assert made[0]["engine_base_url"] == "http://localhost:18080"
        assert state.tts_service is not None and calls[0][1]["engine"] == "server"
        monkeypatch.setattr(settings, "tts_engine_host", "http://pub:9")
        startup.build_tts(SimpleNamespace(npc_registry=None))
        assert made[1]["engine_base_url"] == "http://pub:9"


# --------------------------------------------------------------- build_llm

class TestBuildLlm:
    def _patch(self, monkeypatch):
        made = []

        class _LLM:
            def __init__(self, campaign_loader=None, model=None):
                self.model, self.usage = model, None
                made.append(self)

            def set_usage_tracker(self, u):
                self.usage = u

        monkeypatch.setattr(startup, "LLMManager", _LLM)
        monkeypatch.setattr(startup, "TokenUsage", lambda db, budget: SimpleNamespace(db=db, budget=budget))
        return made

    def test_without_npc_model_only_the_narrator_exists(self, monkeypatch):
        made = self._patch(monkeypatch)
        monkeypatch.setattr(settings, "npc_agent_model", "")
        state = SimpleNamespace(campaign_loader=object(), db="DB")
        startup.build_llm(state)
        assert len(made) == 1 and state.npc_llm_manager is None
        assert state.llm_manager.usage is state.token_usage

    def test_npc_model_gets_its_own_manager_sharing_the_usage_tracker(self, monkeypatch):
        made = self._patch(monkeypatch)
        monkeypatch.setattr(settings, "npc_agent_model", "cheap-1")
        state = SimpleNamespace(campaign_loader=object(), db="DB")
        startup.build_llm(state)
        assert len(made) == 2 and state.npc_llm_manager.model == "cheap-1"
        assert state.npc_llm_manager.usage is state.token_usage  # one shared budget


# ------------------------------------------------------------ build_foundry

class TestBuildFoundry:
    def _state(self, monkeypatch, stale="old-session"):
        client = SimpleNamespace()
        monkeypatch.setattr(startup, "FoundryClient", lambda: client)
        monkeypatch.setattr(startup, "ActionDispatcher", lambda c, app_state: SimpleNamespace(client=c))
        tracker = SimpleNamespace(load=AsyncMock())
        monkeypatch.setattr(startup, "GameStateTracker", lambda db: tracker)
        db = AsyncMock()
        db.get_active_session.return_value = stale
        monkeypatch.setattr(settings, "cinema_enabled", False)
        monkeypatch.setattr(settings, "relay_managed", False)
        monkeypatch.setattr(settings, "world_cli_enabled", False)
        return SimpleNamespace(db=db, relay_manager=SimpleNamespace(restart_headless_session="HEAL")), client

    @pytest.mark.asyncio

    async def test_stale_session_is_closed_not_resumed(self, monkeypatch):
        state, _ = self._state(monkeypatch)
        await startup.build_foundry(state)
        state.db.close_session.assert_awaited_once_with("old-session")
        state.state_tracker.load.assert_awaited_once()

    @pytest.mark.asyncio

    async def test_no_stale_session_closes_nothing(self, monkeypatch):
        state, _ = self._state(monkeypatch, stale=None)
        await startup.build_foundry(state)
        state.db.close_session.assert_not_awaited()

    @pytest.mark.asyncio

    async def test_headless_self_heal_hook_only_when_managed_and_allowed(self, monkeypatch):
        state, client = self._state(monkeypatch)
        monkeypatch.setattr(settings, "relay_managed", True)
        monkeypatch.setattr(settings, "relay_allow_headless", False)
        await startup.build_foundry(state)
        assert not hasattr(client, "_relaunch_headless")
        monkeypatch.setattr(settings, "relay_allow_headless", True)
        await startup.build_foundry(state)
        assert client._relaunch_headless == "HEAL"

    @pytest.mark.asyncio

    async def test_cinema_director_attached_when_enabled(self, monkeypatch):
        import immersion.cinema as cinema
        state, client = self._state(monkeypatch)
        monkeypatch.setattr(settings, "cinema_enabled", True)
        monkeypatch.setattr(cinema, "CinemaDirector", lambda c: ("cinema", c))
        await startup.build_foundry(state)
        assert client.cinema == ("cinema", client)

    @pytest.mark.parametrize("reads,writes,router", [(False, False, False), (True, False, True), (False, True, True)])
    @pytest.mark.asyncio
    async def test_world_cli_router_only_when_a_mode_is_enabled(self, monkeypatch, reads, writes, router):
        import foundry.world_cli as wc
        import foundry.world_cli_router as wr
        state, client = self._state(monkeypatch)
        monkeypatch.setattr(settings, "world_cli_enabled", True)
        monkeypatch.setattr(settings, "world_cli_reads_enabled", reads)
        monkeypatch.setattr(settings, "world_cli_writes_enabled", writes)
        monkeypatch.setattr(wc, "WorldCLI", lambda url, ver, cfg: SimpleNamespace(url=url))
        monkeypatch.setattr(wr, "WorldCLIRouter", lambda cli, reads, writes: SimpleNamespace(reads=reads, writes=writes))
        await startup.build_foundry(state)
        assert state.world_cli is not None
        assert hasattr(client, "world_cli_router") is router
        if router:
            assert (client.world_cli_router.reads, client.world_cli_router.writes) == (reads, writes)


def test_deploy_bundled_modules_passes_configured_path(monkeypatch):
    import foundry.module_deploy as md
    seen = []
    monkeypatch.setattr(md, "deploy_aigm_control_panel", lambda p: seen.append(p))
    monkeypatch.setattr(settings, "foundry_modules_path", "/m")
    startup.deploy_bundled_modules()
    assert seen == ["/m"]


# ---------------------------------------------------------------- shutdown

def _closer(order, name, fail=False):
    async def _c():
        order.append(name)
        if fail:
            raise RuntimeError(name)
    return _c


class TestShutdown:
    def _state(self, order, failing=()):
        mk = lambda n, meth: SimpleNamespace(**{meth: _closer(order, n, n in failing)})  # noqa: E731
        return SimpleNamespace(
            chat_listener=mk("chat", "stop"), combat_loop=mk("combat", "stop"),
            reinforcement_mgr=mk("reinforce", "stop"), foundry_client=mk("foundry", "disconnect"),
            db=mk("db", "close"), llm_manager=mk("llm", "close"), npc_llm_manager=mk("npcllm", "close"),
            tts_service=mk("tts", "close"), map_generator=mk("map", "close"),
            world_cli=mk("cli", "close"), relay_manager=mk("relay", "stop"))

    @pytest.mark.asyncio

    async def test_closes_everything_in_reverse_dependency_order(self, monkeypatch):
        monkeypatch.setattr(settings, "relay_managed", True)
        order = []
        await startup.shutdown(self._state(order))
        assert order == ["chat", "combat", "reinforce", "foundry", "db", "llm", "npcllm", "tts", "map", "cli", "relay"]

    @pytest.mark.asyncio

    async def test_one_failure_does_not_strand_later_components(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "relay_managed", True)
        order = []
        with caplog.at_level(logging.ERROR, logger="ai-gm"):
            await startup.shutdown(self._state(order, failing={"chat", "db", "tts"}))
        assert order[-1] == "relay" and len(order) == 11
        assert "Error closing database" in caplog.text

    @pytest.mark.asyncio

    async def test_unmanaged_relay_is_left_running_and_absent_parts_skipped(self, monkeypatch):
        monkeypatch.setattr(settings, "relay_managed", False)
        order = []
        st = self._state(order)
        await startup.shutdown(st)
        assert "relay" not in order
        await startup.shutdown(SimpleNamespace())  # nothing built: must not raise


# ------------------------------------------------------------- the rest

@pytest.mark.asyncio

async def test_build_persistence_initialises_db_and_retention(monkeypatch, tmp_path):
    calls = []

    class _DB:
        def __init__(self, path):
            calls.append(("path", path))

        async def init(self):
            calls.append("init")

        async def apply_retention_policy(self):
            calls.append("retention")

    monkeypatch.setattr(startup, "Database", _DB)
    monkeypatch.setattr(startup, "RelayManager", lambda: "relay")
    monkeypatch.setattr(settings, "sqlite_db", str(tmp_path / "x.db"))
    state = SimpleNamespace()
    await startup.build_persistence(state)
    assert state.relay_manager == "relay"
    assert calls == [("path", str(tmp_path / "x.db")), "init", "retention"]
