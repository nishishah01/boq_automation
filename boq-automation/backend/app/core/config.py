"""Application settings. backend/.env wins; environment variables fill in what it leaves out.

.env is read first on purpose: a machine-wide variable with the same name (another tool's
GEMINI_API_KEY, for example) must never silently replace this project's own setting.
"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Found next to the code, whichever folder the server is started from. Tests point this elsewhere.
ENV_FILE = os.environ.get("BOQ_ENV_FILE", str(Path(__file__).resolve().parents[2] / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings,
                                   file_secret_settings):
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    database_url: str = "sqlite:///./data/boq.db"
    storage_dir: Path = Path("./data/storage")

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    ai_mode: str = "auto"  # auto | claude | openai | gemini | mock
    ai_provider: str = "anthropic"  # anthropic | openai | gemini: which key "auto" looks for

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.5"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"

    embedding_backend: str = "lsa"  # lsa | sentence-transformers
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    enabled_cost_sheet_types: str = "Highside Supply,Highside Installation"
    cors_origins: str = "http://localhost:5173"

    default_confidence_threshold: int = 75
    default_max_candidates: int = 5

    @property
    def enabled_types(self) -> list[str]:
        return [t.strip() for t in self.enabled_cost_sheet_types.split(",") if t.strip()]

    @property
    def resolved_ai_mode(self) -> str:
        if self.ai_mode != "auto":
            return self.ai_mode
        if self.ai_provider == "openai":
            return "openai" if self.openai_api_key else "mock"
        if self.ai_provider == "gemini":
            return "gemini" if self.gemini_api_key else "mock"
        return "claude" if self.anthropic_api_key else "mock"

    @property
    def active_model(self) -> str:
        mode = self.resolved_ai_mode
        return {"claude": self.anthropic_model, "openai": self.openai_model, "gemini": self.gemini_model}.get(mode, "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
