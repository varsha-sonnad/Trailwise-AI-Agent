# config.py: loads all app settings (API keys, database URLs) from environment variables or a .env file.
# The app refuses to start if a required setting, such as the Gemini API key, is missing.

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The project-root .env file (two folders above this file: backend/app -> project root).
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """All app settings. Values come from the environment or the project-root .env."""

    # Read the root .env if present, and ignore variables this class does not define
    # (the file also holds keys for services that are not wired in yet).
    model_config = SettingsConfigDict(env_file=ROOT_ENV, env_file_encoding="utf-8", extra="ignore")

    # Environment name, e.g. dev or prod.
    app_env: str = "dev"
    # Frontend addresses allowed to call the API (CORS).
    cors_origins: list[str] = ["http://localhost:5173", "null"]

    # Gemini API key (env var GEMINI_API_KEY). Required: min_length=1 rejects an empty value, so startup fails if it is missing.
    gemini_api_key: str = Field(min_length=1)
    # Which Gemini model the agent uses.
    gemini_model: str = "gemini-3.5-flash"
    # Optional backup model (env var GEMINI_FALLBACK_MODEL), tried when the main model is still failing
    # with a server error (500, 503, 504) after its retries. Empty means "no fallback".
    gemini_fallback_model: str = ""

    # MongoDB connection (matches the service in docker-compose.yml).
    # Read from MONGODB_URI (the name used in .env), or MONGO_URL as a fallback.
    mongo_url: str = Field(
        default="mongodb://trailwise:trailwise123@localhost:27017/trailwise?authSource=admin",
        validation_alias=AliasChoices("MONGODB_URI", "MONGO_URL"),
    )
    mongo_db: str = "trailwise"
    # Redis connection (matches the service in docker-compose.yml).
    redis_url: str = "redis://localhost:6379/0"

    # Audit log of agent activity (see app/core/audit.py). The file sits in backend/logs/ by default.
    audit_log_enabled: bool = True
    audit_log_path: str = str(Path(__file__).resolve().parents[1] / "logs" / "audit.log")

    # Longest chat message accepted from a user, in characters.
    max_message_chars: int = 1000


@lru_cache
def get_settings() -> Settings:
    """Return the settings, built once and reused on every later call."""
    return Settings()
