"""Backend settings — read from env / .env file."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Agent Platform"
    debug: bool = True

    database_url: str = (
        "postgresql+asyncpg://aiagent:aiagent_local_password@localhost:5433/aiagent"
    )
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    # LLM defaults — agents may override per-config.yaml.
    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-4-7"
    anthropic_api_key: str | None = None

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
