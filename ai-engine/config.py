from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator
import logging

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    llm_api_key: str = ""
    llm_base_url: str = "http://localhost:8800/v1"
    model: str = ""

    relay_url: str = "http://localhost:13010"
    relay_ws_url: str = "ws://localhost:13010/ws/api"
    relay_api_key: str = ""  # master key — WebSocket auth only (auto-provisioned)
    # relay_scoped_key: set to a non-master key (created via /api/admin/keys in
    # the relay's admin UI) for HTTP endpoints. Using the master key for HTTP
    # would let anyone with a web request exfiltrate world data.
    relay_scoped_key: str = ""  # scoped REST key for HTTP endpoints (auto-provisioned)
    # Default RPC reply timeout (s). Headless software-rendered Chrome on a
    # heavy world can take well over 15s for canvas ops (scene switch, walls,
    # lights), so the default is generous; data-only ops still return fast.
    relay_rpc_timeout: float = 45.0
    relay_rpc_timeout_canvas: float = 90.0  # canvas/scene-building ops

    # Embedded relay (spawned as a managed subprocess; see relay_proc/manager.py)
    relay_managed: bool = True  # false = connect to an externally run relay
    relay_binary_path: str = ""  # default: <repo>/bin/relay
    relay_data_dir: str = ""  # default: <repo>/data/relay
    relay_admin_email: str = "aigm@local.host"
    relay_admin_password: str = ""  # auto-generated and persisted if empty
    relay_log_level: str = "info"
    relay_allow_headless: bool = True
    relay_chrome_path: str = ""  # default: auto-resolve Google Chrome (never Chromium)
    # Foundry Virtual Tabletop lifecycle. The AI-GM starts Foundry when it is
    # absent and only shuts it down when this process started it.
    foundry_auto_start: bool = True
    foundry_shutdown_on_exit: bool = True
    foundry_app_path: str = "/Applications/Foundry Virtual Tabletop.app"
    # Optional bootstrap world for a campaign that has not yet been linked to a
    # Foundry world. Normal operation resolves the world from the campaign the
    # user selects; keep this empty unless an initial local setup needs it.
    foundry_world: str = ""
    relay_headless_client_id: str = ""  # set at runtime after headless session launch
    # Foundry display name of the human GM account. Used as a fallback when
    # authorizing /gm chat commands before the GM-role user list has loaded
    # (see chat_listener._is_gm_author). Leave empty to rely on role>=3 only.
    foundry_username: str = ""
    admin_port: int = Field(default=18080, ge=1024, le=65535)
    # Bind address. The default keeps the whole API loopback-only — the OS is
    # the auth boundary. Set ADMIN_HOST=0.0.0.0 to expose on the LAN, and set
    # ADMIN_TOKEN when you do: if set, /api/* and the admin WebSocket require
    # `Authorization: Bearer <token>`. /audio stays public (unguessable names).
    admin_host: str = "127.0.0.1"
    admin_token: str = ""
    cors_origins: str = "http://localhost:18080,http://127.0.0.1:18080"
    max_request_body_bytes: int = Field(default=1_048_576, ge=16_384, le=10_485_760)
    api_requests_per_minute: int = Field(default=120, ge=10, le=10_000)
    ws_max_message_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    ws_max_connections: int = Field(default=16, ge=1, le=256)
    sqlite_db: str = "foundryvtt-ai-gm.db"
    default_campaign: str = ""
    campaign_vault_path: str = Field(default="~/Vaults/MyStuff/Dungeons_and_Dragons")
    # Semantic indexing (Vault RAG) — enable live campaign context retrieval
    vault_embeddings_enabled: bool = True
    vault_embeddings_provider: str = "local"  # "local", "openai", or "ollama"
    vault_embeddings_model: str = "all-MiniLM-L6-v2"  # local model name
    vault_embeddings_cache_dir: str = ".vault_embeddings_cache"
    vault_index_path: str = ".vault_index"
    # Query result caching for semantic indexer
    vault_query_cache_enabled: bool = True
    vault_query_cache_size: int = 100  # max entries (LRU)
    vault_query_cache_ttl_seconds: int = 300  # 5 minutes
    ai_name: str = "Sage"
    ai_tone: str = "mysterious, immersive, high fantasy"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # Temperature for the campaign STRUCTURE pass (scene/NPC/quest JSON generation).
    # Lower than conversational temperature: schema-filling on a small quantized
    # model is more reliable and less prone to early-stops / malformed JSON at
    # low temperature. Prose vividness comes from field content, not the sampler.
    campaign_gen_temperature: float = 0.5
    thinking_param: str = "thinking=false"
    max_context_tokens: int = Field(default=50000, gt=0)
    comfyui_url: str = "http://127.0.0.1:18188"
    comfyui_timeout: int = 300
    comfyui_checkpoint: str = "juggernautXL_v11.safetensors"
    campaign_max_maps: int = 6
    campaign_map_width: int = 1024
    campaign_map_height: int = 1024
    comfyui_input_dirs: list[str] = Field(default_factory=list)  # paths ComfyUI scans for LoadImage; configure via .env

    # FoundryVTT connection (used for headless Chrome session)
    # Combat settings
    llm_combat_timeout: int = 60  # seconds before falling back to generic NPC behavior
    pc_turn_timeout: int = 180    # seconds to wait for a PC's combat input before auto-skipping (0 = use default 180s)
    combat_round_cap: int = 50    # max rounds before combat ends in a stalemate

    # Safety: arbitrary JavaScript execution in Foundry (execute_js action).
    # Disabled by default — the action is reachable from player chat via the LLM,
    # so leaving it on exposes the world to prompt-injected destructive scripts.
    allow_execute_js: bool = False

    # Rate limiting — minimum seconds between LLM calls (0 = no rate limit, still serialized)
    llm_min_call_interval: float = Field(default=0.5, ge=0)
    # Max chars for NPC/world context injected via set_npc_context / set_world_context
    # Defaults to 50k chars (~12.5k tokens at 4 chars/token)
    context_max_chars: int = 50_000
    # Max chars accepted from a single player chat message before truncation.
    # Guards against LLM context exhaustion and excessive token billing from
    # oversized/abusive messages.
    chat_message_max_length: int = 4096
    # Hard cap for one session. Zero disables the cap (not recommended when
    # autonomous/off-session ticks are enabled).
    llm_token_budget: int = Field(default=100_000, ge=0)

    # Context reinforcement to prevent LLM drift
    context_reinforce_interval: int = 5
    context_summarize_interval: int = 10
    context_summarize_timer: int = 300  # seconds between periodic summarization passes

    # GM pacing — proactive narration when players are idle or scene stalls.
    # gm_idle_timeout is the baseline for the FIRST nudge; consecutive
    # unanswered nudges back off up to 4x this (see chat_listener's
    # _reset_idle_timer) so a genuine lull gets a fast first nudge without
    # nagging a table that's stepped away.
    gm_idle_timeout: int = Field(default=30, ge=0)    # seconds of silence before the GM's first nudge
    gm_pace_interval: int = 10   # player exchanges before a pacing check fires
    players_roll_own: bool = True  # PCs roll their own dice; the GM only rolls for NPCs/monsters
    # None selects the solo-safe default from party size; explicit values opt
    # any party in or out of the setback model.
    solo_death_setback: bool | None = None
    # World-clock advance applied on "/gm end session" — models "time passes
    # until the table next sits down." Default: 8 in-game hours.
    world_clock_session_end_advance_seconds: int = 8 * 60 * 60
    # Optional cheaper/smaller model for NPC self-initiated turns (llm/router.py
    # ModelRouter). Empty (default) routes NPC turns through the same model as
    # the narrator — set this once a second model is actually available.
    npc_agent_model: str = ""

    # Multi-player input batching — debounce simultaneous player messages
    # into one combined GM turn instead of one turn per message. Only
    # applies outside combat and when more than one player is currently
    # active (see chat_listener's _track_active_speaker); 0 disables it.
    input_batch_debounce_seconds: float = Field(default=2.5, ge=0)
    llm_max_output_tokens: int = 2048  # output reservation; large values overflow small context windows (400)


    # TTS narration
    tts_enabled: bool = False
    # "server"  → LocalAI/OpenAI-compatible TTS server (tts_url below)
    # "browser" → Web Speech API in each player's browser via the bundled
    #             aigm-tts Foundry module (no server, free, offline)
    tts_engine: str = "server"
    # Foundry Data/modules dir for auto-deploying the aigm-tts module.
    # Empty → auto-resolve common per-OS locations at startup.
    foundry_modules_path: str = ""
    tts_url: str = "http://localhost:8800"
    tts_api_key: str = ""
    tts_model: str = "Voxtral-4B-TTS-2603-mlx-4bit"
    tts_narrator_voice: str = "fable"   # GM narrator voice
    # Some models expose only a few fixed voices (e.g. marvis: conversational_a/b)
    # and 500 on any other name. Map the archetype voices the VoiceAssigner
    # emits onto the model's real voices by gender, and restrict to a whitelist
    # so an unmapped voice never reaches the model. Leave empty to pass voices
    # through unchanged (e.g. for OpenAI-style multi-voice models).
    tts_voice_map: str = ""             # "archetype:voice,..." most granular map
    tts_voice_male: str = ""            # model voice used for male NPCs/narrator
    tts_voice_female: str = ""          # model voice used for female NPCs
    tts_allowed_voices: str = ""        # comma-separated whitelist; others fall back
    tts_format: str = "mp3"
    tts_audio_dir: str = "tts_audio"    # relative to ai-engine working dir
    tts_max_cached: int = 50            # max audio files before pruning
    tts_engine_host: str = ""           # public host:port for audio URLs (default: localhost:admin_port)
    tts_volume: float = Field(default=0.8, ge=0.0, le=1.0)  # Foundry playback volume (0-1)

    # oMLX Z-Image-Turbo (image generation endpoint)
    omlx_url: str = "http://localhost:8800/v1/images/generations"
    omlx_model: str = "Z-Image-Turbo"
    omlx_size: str = "1024x1024"
    omlx_quality: str = "standard"
    omlx_style: str = "fantasy_map"  # fantasy_map | dungeon | portrait | overworld

    # Image generation provider: comfyui | omlx | auto
    image_provider: str = "auto"

    # --- Validators (declared after all fields; Pydantic v2 binds them by
    # field name regardless of textual order) ---

    @field_validator("model")
    @classmethod
    def validate_model(cls, v):
        if not v or not v.strip():
            raise ValueError("model cannot be empty — set MODEL env var (e.g. claude-3-5-sonnet-20241022)")
        return v.strip()

    @field_validator("campaign_vault_path", mode="after")
    @classmethod
    def expand_campaign_path(cls, v):
        """Expand ~ and ensure the path is absolute."""
        from pathlib import Path
        if not v:
            return v
        return str(Path(v).expanduser().resolve())

    @model_validator(mode="after")
    def validate_settings(self):
        """Validate critical settings at startup."""
        # Check required URL formats
        if not self.relay_url.startswith(("http://", "https://", "ws://", "wss://")):
            raise ValueError(f"relay_url must be a valid URL, got: {self.relay_url}")

        if not self.relay_ws_url.startswith(("ws://", "wss://")):
            raise ValueError(f"relay_ws_url must be a WebSocket URL (ws:// or wss://), got: {self.relay_ws_url}")

        # Warn about potentially unsafe settings (but allow them)
        if self.allow_execute_js:
            logger.warning("[Config] WARNING: allow_execute_js=true — arbitrary JavaScript execution is enabled!")

        if not self.llm_api_key:
            logger.warning("[Config] WARNING: llm_api_key is not set — LLM features will fail at runtime")

        if not self.relay_scoped_key:
            logger.warning(
                "[Config] WARNING: relay_scoped_key not set — HTTP endpoints will use "
                "the master key. Create a scoped key in the relay admin UI.",
            )

        if self.admin_host not in ("127.0.0.1", "localhost", "::1") and not self.admin_token:
            logger.warning(
                "[Config] WARNING: ADMIN_HOST=%s exposes the admin API to the network "
                "with no ADMIN_TOKEN set — any device that can reach this host has "
                "full admin access.", self.admin_host,
            )

        return self

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
