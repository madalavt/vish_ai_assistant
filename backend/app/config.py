"""Application settings, loaded from the repo-root .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Database ----
    database_url: str = "postgresql+asyncpg://vish:vish_dev_password@localhost:5432/vish_ai"

    # ---- Ollama ----
    ollama_base_url: str = "http://localhost:11434"
    default_chat_model: str = "qwen3:8b"
    deep_chat_model: str = "qwen3:14b"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # ---- Anthropic (blank disables the cloud provider entirely) ----
    anthropic_api_key: str = ""
    cloud_chat_model: str = "claude-sonnet-5"

    # ---- Server ----
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # ---- Storage for uploads and extracted text (gitignored) ----
    data_dir: str = str(REPO_ROOT / "data")

    # ---- Ingestion ----
    chunk_target_tokens: int = 800
    chunk_overlap_tokens: int = 120
    max_upload_mb: int = 50

    # ---- Single-user mode; real auth arrives in M9 ----
    local_user_email: str = "local@localhost"
    local_user_name: str = "Vishnu"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_dir(self) -> Path:
        return Path(self.data_dir) / "uploads"

    @property
    def extracted_dir(self) -> Path:
        return Path(self.data_dir) / "extracted"

    @property
    def cloud_enabled(self) -> bool:
        return bool(self.anthropic_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
