from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_api_key: str = ""
    llm_base_url: str = "http://localhost:18800/v1"
    model: str = ""
    relay_url: str = "http://localhost:13010"
    relay_ws_url: str = "ws://localhost:13010/ws/api"
    relay_api_key: str = ""
    admin_port: int = 18000
    sqlite_db: str = "foundryvtt-ai-gm.db"
    campaign_vault_path: str = "~/Vaults/MyStuff/games/Dungeons_and_Dragons"
    ai_name: str = "Aethelwyrd GM"
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

    # Context reinforcement to prevent LLM drift
    context_reinforce_interval: int = 5
    context_summarize_interval: int = 10

    class Config:
        env_file = ".env"


settings = Settings()
