# config.py: loads the MCP server's settings (weather, Gemini, RailRadar and Parse.bot keys, SMTP settings, Redis URL) from the project-root .env.
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
    # RailRadar API key (env var RAILRADAR_API_KEY) for the train tool.
    # Empty means "not configured": the tool returns an error object instead of crashing.
    railradar_api_key: str = ""
    # Parse.bot API key (env var PARSEBOT_API_KEY) for the bus tool.
    # Empty means "not configured": the tool returns an error object instead of crashing.
    parsebot_api_key: str = ""
    # The email account the tool sends from (env var SMTP_EMAIL, as in .env_example). It is the "From"
    # address and, when a password is set, the SMTP login name. Empty means "not configured".
    smtp_email: str = ""
    # That account's SMTP password (env var SMTP_PASSWORD). Empty = no login (fine for Mailpit). Never logged.
    smtp_password: str = ""
    # The SMTP server. The defaults match Mailpit in docker-compose.yml (no login, no TLS), so local testing
    # sends nothing outside your machine. For a real provider set SMTP_HOST (for example smtp.gmail.com),
    # SMTP_PORT (587) and SMTP_STARTTLS=true in .env. Port 465 uses TLS from the start automatically.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_starttls: bool = False
    # Optional: the ONE address the email tool sends to (env var VERIFIED_RECIPIENT). Empty means it sends
    # to smtp_email itself. There is no login yet, so this stands in for the user's verified address.
    verified_recipient: str = ""
    # Redis used as the cache (matches docker-compose.yml).
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, built once and reused on every later call."""
    return Settings()
