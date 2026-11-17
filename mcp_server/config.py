# config.py: loads the MCP server's settings (weather and Gemini keys, Redis URL) from the project-root .env.
# The MCP server is a separate service, so it has its own small settings class
# instead of importing backend/app/config.py.

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The project-root .env file (one folder above mcp_server/).
ROOT_ENV = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """Settings for the MCP server. Values come from the environment or the project-root .env."""

    # Read the root .env if present; ignore keys this server does not use (Gemini, MongoDB, ...).
    model_config = SettingsConfigDict(env_file=ROOT_ENV, env_file_encoding="utf-8", extra="ignore")

    # OpenWeatherMap API key (env var OPENWEATHER_API_KEY). Empty means "not configured":
    # the tool then returns an error object instead of crashing the server at startup.
    openweather_api_key: str = ""
    # Gemini API key (env var GEMINI_API_KEY, the same key the backend uses) for the web_search tool.
    # Empty means "not configured": the tool returns an error object instead of crashing.
    gemini_api_key: str = ""
    # Model the web_search tool uses for Google Search grounding (env var GEMINI_SEARCH_MODEL).
    # Change it if the chat model cannot do grounding on your key.
    gemini_search_model: str = "gemini-3.5-flash"
    # Redis used as the cache (matches docker-compose.yml).
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, built once and reused on every later call."""
    return Settings()
