from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_api_key: str = ""
    llm_base_url: str = "http://localhost:18800/v1"
    model: str = ""
    relay_url: str = "http://localhost:13010"
    relay_ws_url: str = "ws://localhost:13010/ws/api"
    relay_api_key: str = ""  # master key — WebSocket auth only (auto-provisioned)
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
    relay_headless_client_id: str = ""  # set at runtime after headless session launch
    admin_port: int = 18080
    sqlite_db: str = "foundryvtt-ai-gm.db"
    default_campaign: str = ""
    campaign_vault_path: str = "~/Vaults/MyStuff/games/Dungeons_and_Dragons"
    ai_name: str = "Sage"
    ai_tone: str = "mysterious, immersive, high fantasy"
    temperature: float = 0.7
    thinking_param: str = "thinking=false"
    max_context_tokens: int = 50000
    comfyui_url: str = "http://127.0.0.1:18188"
    comfyui_timeout: int = 300
    comfyui_checkpoint: str = "juggernautXL_v11.safetensors"
    campaign_max_maps: int = 6
    campaign_map_width: int = 1024
    campaign_map_height: int = 1024
    comfyui_input_dirs: list = []  # paths ComfyUI scans for LoadImage; configure via .env

    # FoundryVTT connection (used for headless Chrome session)
    foundry_url: str = ""  # e.g. http://localhost:30000
    foundry_username: str = ""  # Foundry GM username
    foundry_password: str = ""  # Foundry GM password
    foundry_world: str = ""  # world name to join (optional; joins last active if empty)

    # Combat settings
    llm_combat_timeout: int = 60  # seconds before falling back to generic NPC behavior
    pc_turn_timeout: int = 180    # seconds to wait for a PC's combat input before auto-skipping (0 = wait forever)

    # Safety: arbitrary JavaScript execution in Foundry (execute_js action).
    # Disabled by default — the action is reachable from player chat via the LLM,
    # so leaving it on exposes the world to prompt-injected destructive scripts.
    allow_execute_js: bool = False

    # Context reinforcement to prevent LLM drift
    context_reinforce_interval: int = 5
    context_summarize_interval: int = 10
    context_summarize_timer: int = 300  # seconds between periodic summarization passes

    # GM pacing — proactive narration when players are idle or scene stalls
    gm_idle_timeout: int = 45    # seconds of silence before the GM nudges the scene
    gm_pace_interval: int = 10   # player exchanges before a pacing check fires


    # TTS narration
    tts_enabled: bool = False
    # "server"  → LocalAI/OpenAI-compatible TTS server (tts_url below)
    # "browser" → Web Speech API in each player's browser via the bundled
    #             aigm-tts Foundry module (no server, free, offline)
    tts_engine: str = "server"
    # Foundry Data/modules dir for auto-deploying the aigm-tts module.
    # Empty → auto-resolve common per-OS locations at startup.
    foundry_modules_path: str = ""
    tts_url: str = "http://172.31.25.75:8080"
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
    tts_volume: float = 0.8             # Foundry playback volume (0–1)

    # oMLX Z-Image-Turbo (image generation endpoint)
    omlx_url: str = "http://localhost:8800/v1/images/generations"
    omlx_model: str = "Z-Image-Turbo"
    omlx_size: str = "1024x1024"
    omlx_quality: str = "standard"
    omlx_style: str = "fantasy_map"  # fantasy_map | dungeon | portrait | overworld

    # Image generation provider: comfyui | omlx | auto
    image_provider: str = "auto"

    class Config:
        env_file = ".env"


settings = Settings()
