"""Component construction and teardown for the application lifespan.

main.lifespan was a single 339-line function running fifteen numbered steps.
api/deps.py described the wiring as "a single 340-line sequence with no
natural seams"; the numbered comments were the seams. Each step below is one
of them, in the same order, reading its dependencies off `state` rather than
from the locals the original threaded through.

Order matters and is enforced by what each builder reads: persistence before
context, context before the LLM stack, the LLM stack before gameplay, and
gameplay before the chat listener that drives it.
"""

import logging
from pathlib import Path

from actions.dispatcher import ActionDispatcher
from combat.loop import CombatLoop
from config import settings
from context.loader import CampaignLoader
from context.campaign_memory import CampaignMemory
from context.reinforcement_manager import ContextReinforcementManager
from foundry.chat_listener import ChatListener
from foundry.client import FoundryClient
from llm.manager import LLMManager
from llm.usage import TokenUsage
from persistence.db import Database
from relay_proc.manager import RelayManager
from scene.awareness import SceneAwareness
from state.tracker import GameStateTracker
from tts.service import TTSService

logger = logging.getLogger("ai-gm")


def check_admin_exposure() -> None:
    """Fail closed if the admin API would be reachable without a token."""
    is_loopback = settings.admin_host in ("127.0.0.1", "localhost", "::1")
    if not is_loopback and not settings.admin_token:
        raise RuntimeError(
            f"CRITICAL: Admin API is bound to {settings.admin_host} (network-accessible) "
            f"but ADMIN_TOKEN is not set. Set ADMIN_TOKEN or change ADMIN_HOST to 127.0.0.1. "
            f"Refusing to start to prevent unauthorized access."
        )


async def build_persistence(state) -> None:
    """Steps 0-1: relay manager and database.

    The relay process and the Foundry connection are deferred until the GM
    starts a campaign, so the Admin UI works with the relay down.
    """
    state.relay_manager = RelayManager()
    logger.info("Relay and Foundry connection deferred until campaign start")

    state.db = Database(settings.sqlite_db)
    await state.db.init()
    logger.info("Database initialized")
    await state.db.apply_retention_policy()


async def build_context(state) -> None:
    """Steps 2-2b: semantic index, campaign context, NPC personalities."""
    state.semantic_indexer = None
    state.semantic_rag = None

    if settings.vault_embeddings_enabled:
        from vault.embeddings import (
            CachedEmbeddings, LocalEmbeddings, OllamaEmbeddings, OpenAIEmbeddings,
        )
        from vault.indexer import SemanticIndexer

        try:
            if settings.vault_embeddings_provider == "local":
                embeddings = LocalEmbeddings(model=settings.vault_embeddings_model)
            elif settings.vault_embeddings_provider == "openai":
                if not settings.llm_api_key:
                    raise ValueError("OpenAI embeddings require LLM_API_KEY")
                embeddings = OpenAIEmbeddings(
                    api_key=settings.llm_api_key, model=settings.vault_embeddings_model
                )
            elif settings.vault_embeddings_provider == "ollama":
                embeddings = OllamaEmbeddings(model=settings.vault_embeddings_model)
            else:
                raise ValueError(
                    f"Unknown embeddings provider: {settings.vault_embeddings_provider}"
                )

            cached_embeddings = CachedEmbeddings(
                embeddings, cache_dir=settings.vault_embeddings_cache_dir
            )
            state.semantic_indexer = SemanticIndexer(
                cached_embeddings,
                index_path=settings.vault_index_path,
                cache_enabled=settings.vault_query_cache_enabled,
                cache_size=settings.vault_query_cache_size,
                cache_ttl_seconds=settings.vault_query_cache_ttl_seconds,
            )
            logger.info(
                f"Semantic indexer initialized (provider={settings.vault_embeddings_provider}, "
                f"embedding_cache={settings.vault_embeddings_cache_dir}, "
                f"query_cache={settings.vault_query_cache_enabled})"
            )
        except Exception as e:
            logger.warning(
                f"Failed to initialize semantic indexer: {e}. Falling back to keyword search."
            )

    if state.semantic_indexer:
        from vault.vault_semantic_rag import SemanticRAG
        state.semantic_rag = SemanticRAG(state.semantic_indexer, debounce_seconds=30.0)
        logger.info("Semantic RAG initialized for context injection")

    state.campaign_loader = CampaignLoader(semantic_indexer=state.semantic_indexer)
    await state.campaign_loader.load(settings.default_campaign)
    logger.info("Campaign context loaded")

    from npc.personality import PersonalityEngine
    from npc.registry import NPCRegistry
    state.npc_registry = NPCRegistry()
    state.personality_engine = PersonalityEngine()
    logger.info("NPC personality system initialized")


def build_tts(state) -> None:
    """Step 2c: optional narration, off unless TTS_ENABLED."""
    from tts.playback import configure as configure_tts

    state.tts_service = None

    if settings.tts_enabled and settings.tts_engine == "browser":
        # Browser TTS: no server. Deploy the aigm-tts Foundry module so every
        # client speaks via the Web Speech API.
        from foundry.module_deploy import deploy_aigm_tts
        deployed = deploy_aigm_tts(settings.foundry_modules_path)
        configure_tts(None, state.npc_registry, volume=settings.tts_volume, engine="browser")
        logger.info(
            f"TTS enabled — engine=browser (Web Speech API), "
            f"narrator_voice={settings.tts_narrator_voice}, "
            f"module_deployed={deployed} "
            f"{'(enable the aigm-tts module in your world)' if deployed else '(set FOUNDRY_MODULES_PATH)'}"
        )
    elif settings.tts_enabled:
        engine_host = settings.tts_engine_host or f"http://localhost:{settings.admin_port}"
        state.tts_service = TTSService(
            base_url=settings.tts_url,
            api_key=settings.tts_api_key,
            model=settings.tts_model,
            narrator_voice=settings.tts_narrator_voice,
            audio_dir=Path(__file__).resolve().parent.parent / settings.tts_audio_dir,
            engine_base_url=engine_host,
            fmt=settings.tts_format,
            max_cached_files=settings.tts_max_cached,
        )
        configure_tts(
            state.tts_service, state.npc_registry, volume=settings.tts_volume, engine="server"
        )
        logger.info(
            f"TTS enabled — engine=server model={settings.tts_model} "
            f"narrator_voice={settings.tts_narrator_voice}"
        )
    else:
        logger.info("TTS disabled (set TTS_ENABLED=true to enable)")


def build_llm(state) -> None:
    """Steps 3-3b: the narrator model, and optionally a cheaper NPC-turn model."""
    state.llm_manager = LLMManager(campaign_loader=state.campaign_loader)
    state.token_usage = TokenUsage(state.db, settings.llm_token_budget)
    state.llm_manager.set_usage_tracker(state.token_usage)
    logger.info("LLM Manager initialized")

    # npc/agent.py routes through llm/router.py's ModelRouter. Unset by
    # default: NPC turns use the narrator model until a distinct one exists.
    state.npc_llm_manager = None
    if settings.npc_agent_model:
        state.npc_llm_manager = LLMManager(
            campaign_loader=state.campaign_loader, model=settings.npc_agent_model
        )
        state.npc_llm_manager.set_usage_tracker(state.token_usage)
        logger.info(f"NPC-tier LLM Manager initialized (model={settings.npc_agent_model})")


def deploy_bundled_modules() -> None:
    """Keep the installed control panel in step with the repo.

    Only ever copies files; whether a world enables the module stays the
    operator's choice. TTS deploys itself in build_tts when it is enabled.
    """
    from foundry.module_deploy import deploy_aigm_control_panel
    deploy_aigm_control_panel(settings.foundry_modules_path)


async def build_foundry(state) -> None:
    """Steps 4-7: Foundry client, dispatcher, state tracker, stale-session sweep."""
    state.foundry_client = FoundryClient()
    # Self-heal hook: relaunch the headless Foundry session if the relay loses
    # its Foundry client (headless tab died / module dropped).
    if settings.relay_managed and settings.relay_allow_headless:
        state.foundry_client._relaunch_headless = state.relay_manager.restart_headless_session
    logger.info("FoundryVTT connection deferred until campaign start")

    state.action_dispatcher = ActionDispatcher(state.foundry_client, app_state=state)
    logger.info("Action dispatcher initialized with audit trail")

    state.state_tracker = GameStateTracker(state.db)
    await state.state_tracker.load()
    logger.info("State tracker initialized")

    # A session still marked active was never cleanly ended, so treat it as
    # stale rather than resuming it: the user must start a new one explicitly
    # so campaign selection and monitoring are correct.
    stale_session = await state.db.get_active_session()
    if stale_session:
        await state.db.close_session(stale_session)
        logger.info(f"Closed stale session from previous run: {stale_session}")


def build_gameplay(state, on_state_update) -> None:
    """Steps 8-10: scene awareness, immersion, combat."""
    state.scene_awareness = SceneAwareness(
        foundry=state.foundry_client,
        state_tracker=state.state_tracker,
        campaign_loader=state.campaign_loader,
        llm_manager=state.llm_manager,
    )
    logger.info("Scene awareness initialized")

    from immersion.ambient import AmbientManager
    from immersion.effects import EffectsManager
    from immersion.items import ItemManager
    from immersion.macros import MacroManager
    from immersion.particles import ParticleManager
    from immersion.vision import VisionManager
    state.ambient_manager = AmbientManager()
    state.effects_manager = EffectsManager()
    state.vision_manager = VisionManager()
    state.macro_manager = MacroManager()
    state.item_manager = ItemManager()
    state.particle_manager = ParticleManager()
    logger.info("Immersion managers initialized")

    state.combat_loop = CombatLoop(
        foundry=state.foundry_client,
        llm=state.llm_manager,
        dispatcher=state.action_dispatcher,
        state_tracker=state.state_tracker,
        db=state.db,
        campaign_loader=state.campaign_loader,
        npc_registry=state.npc_registry,
        token_usage=state.token_usage,
    )
    state.combat_loop.set_turn_start_callback(on_state_update)
    state.combat_loop.set_turn_complete_callback(on_state_update)
    logger.info("Combat loop initialized")

    # actions/executors.py reads app_state.map_generator, and ACTION_SCHEMAS
    # advertises generate_map to the model every turn — but nothing ever set
    # the attribute, so every call the LLM made came back "Map generator not
    # available". Construction is local (a URL and an httpx client); ComfyUI
    # is contacted at generate time, not here, so an absent ComfyUI still
    # starts cleanly and fails at the action with a real error.
    from campaign.map_generator import MapGenerator
    state.map_generator = MapGenerator(
        comfyui_url=settings.comfyui_url,
        timeout=settings.comfyui_timeout,
        checkpoint_name=settings.comfyui_checkpoint,
        comfyui_input_dirs=[Path(d) for d in settings.comfyui_input_dirs],
    )
    logger.info("Map generator initialized (ComfyUI at %s)", settings.comfyui_url)


async def build_chat(state, on_results) -> None:
    """Steps 11-12: reinforcement manager, chat listener, and their callbacks."""
    state.reinforcement_mgr = ContextReinforcementManager(
        llm_manager=state.llm_manager,
        state_tracker=state.state_tracker,
        foundry_client=state.foundry_client,
        scene_awareness=state.scene_awareness,
        campaign_loader=state.campaign_loader,
        reinforce_interval=settings.context_reinforce_interval or 5,
    )
    await state.reinforcement_mgr.start()
    logger.info("Context reinforcement manager initialized")

    state.campaign_memory = CampaignMemory(
        state.db, state.llm_manager,
        every_n_turns=settings.context_summarize_interval or 10,
    )

    state.chat_listener = ChatListener(
        foundry=state.foundry_client,
        llm=state.llm_manager,
        dispatcher=state.action_dispatcher,
        state_tracker=state.state_tracker,
        db=state.db,
        campaign_loader=state.campaign_loader,
        combat_loop=state.combat_loop,
        scene_awareness=state.scene_awareness,
        reinforcement_mgr=state.reinforcement_mgr,
        npc_registry=state.npc_registry,
        personality_engine=state.personality_engine,
        ambient_manager=state.ambient_manager,
        effects_manager=state.effects_manager,
        vision_manager=state.vision_manager,
        npc_llm=state.npc_llm_manager,
        semantic_rag=state.semantic_rag,
        token_usage=state.token_usage,
        campaign_memory=state.campaign_memory,
    )

    async def _budget_pause(error):
        await state.chat_listener.handle_budget_exhausted(error)

    state.token_usage.on_exhausted = _budget_pause

    async def on_combat_start_event(tokens):
        await state.reinforcement_mgr.on_combat_start(tokens)

    async def on_combat_end_event():
        await state.reinforcement_mgr.on_combat_end()

    state.combat_loop.set_combat_start_callback(on_combat_start_event)
    state.combat_loop.set_combat_end_callback(on_combat_end_event)

    # Registered before the listener starts, so the first player message can
    # already reach the admin panel.
    state.chat_listener.set_results_callback(on_results)

    from tts.playback import set_chat_listener
    set_chat_listener(state.chat_listener)


async def shutdown(state) -> None:
    """Close everything build_* opened, in reverse dependency order.

    Each close is guarded independently: a failure partway through must not
    strand the components after it, which is how the original left the relay
    subprocess running when an earlier close raised.
    """
    logger.info("Shutting down AI Gamemaster Engine...")

    async def _close(label, closer):
        try:
            await closer()
        except Exception:
            logger.exception("Error closing %s during shutdown", label)

    if getattr(state, "chat_listener", None):
        await _close("chat listener", state.chat_listener.stop)
    if getattr(state, "combat_loop", None):
        await _close("combat loop", state.combat_loop.stop)
    if getattr(state, "reinforcement_mgr", None):
        await _close("reinforcement manager", state.reinforcement_mgr.stop)
    if getattr(state, "foundry_client", None):
        await _close("Foundry client", state.foundry_client.disconnect)
    if getattr(state, "db", None):
        await _close("database", state.db.close)
    if getattr(state, "llm_manager", None):
        await _close("LLM manager", state.llm_manager.close)
    if getattr(state, "npc_llm_manager", None):
        await _close("NPC LLM manager", state.npc_llm_manager.close)
    if getattr(state, "tts_service", None):
        await _close("TTS service", state.tts_service.close)
    if getattr(state, "map_generator", None):
        await _close("map generator", state.map_generator.close)
    if getattr(state, "relay_manager", None) and settings.relay_managed:
        await _close("relay manager", state.relay_manager.stop)

    logger.info("Shutdown complete")
