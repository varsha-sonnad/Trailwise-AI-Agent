# config.py: loads the MCP server's settings (weather, OpenAI search, RailRadar and Parse.bot keys, SMTP settings, geocoding, routing and Pexels photo services, Redis URL) from the project-root .env.
# The MCP server is a separate service, so it has its own small settings class
# instead of importing backend/app/config.py.

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The project-root .env file (one folder above mcp_server/).
ROOT_ENV = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """Settings for the MCP server. Values come from the environment or the project-root .env."""

    # Read the root .env if present; ignore keys this server does not use (MongoDB, ...).
    model_config = SettingsConfigDict(env_file=ROOT_ENV, env_file_encoding="utf-8", extra="ignore")

    # OpenWeatherMap API key (env var OPENWEATHER_API_KEY). Empty means "not configured":
    # the tool then returns an error object instead of crashing the server at startup.
    openweather_api_key: str = ""
    # OpenAI API key (env var OPENAI_API_KEY, the same key the backend uses) for the web_search tool.
    # Empty means "not configured": the tool returns an error object instead of crashing. Never logged.
    openai_api_key: str = ""
    # The model the web_search tool uses with OpenAI's built-in web search (env var OPENAI_SEARCH_MODEL). No default on
    # purpose: model names change, and it must be a small model that supports web search. Empty means "not configured".
    openai_search_model: str = ""
    # How long ONE web_search attempt waits for OpenAI, in seconds (env var OPENAI_SEARCH_TIMEOUT_SECONDS, default 120). A
    # timeout is retried once, and both attempts together are held to the agent's call limit minus a safety margin (below).
    openai_search_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    # The agent's time limit for one web_search call, in seconds (env var WEB_SEARCH_CALL_TIMEOUT_SECONDS, default 150; the backend
    # reads the same variable). The tool keeps both of its attempts under this limit (minus 10 seconds), so it answers
    # "timeout" itself before the agent gives up on it.
    web_search_call_timeout_seconds: float = Field(default=150.0, gt=0, le=900)
    # RailRadar API key (env var RAILRADAR_API_KEY) for the train tool.
    # Empty means "not configured": the tool returns an error object instead of crashing.
    railradar_api_key: str = ""
    # Parse.bot API key (env var PARSEBOT_API_KEY) for the bus tool.
    # Empty means "not configured": the tool returns an error object instead of crashing.
    parsebot_api_key: str = ""
    # The email account the tool sends from (env var SMTP_EMAIL, as in .env.example). It is the "From"
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
    # The geocoding service for the geocode_places tool (a Nominatim-style API), and the User-Agent it requires
    # (env vars GEOCODE_BASE_URL and GEOCODE_USER_AGENT). The User-Agent must identify this app and give a contact.
    # Either one empty means "not configured": the tool returns an error object instead of crashing.
    geocode_base_url: str = ""
    geocode_user_agent: str = ""
    # The routing service for the get_route tool (an OSRM-style API, env var ROUTING_BASE_URL), for example
    # https://router.project-osrm.org (a public demo server for light use). Empty means "not configured":
    # the tool returns an error object instead of crashing.
    routing_base_url: str = ""
    # The Pexels photo API for the get_place_images tool (env vars PEXELS_BASE_URL, for example https://api.pexels.com/v1,
    # and PEXELS_API_KEY). Either one empty means "not configured": the tool returns an error object instead of crashing.
    # The key is sent only in the Authorization header and is never logged.
    pexels_base_url: str = ""
    pexels_api_key: str = ""
    # Redis, used by the tools as a cache (env var REDIS_URL, for example redis://localhost:6379/0, or redis://redis:6379/0
    # inside Docker). OPTIONAL: when it is not set (empty) the tools skip the cache silently and work without it.
    # When it is set but Redis cannot be reached, each tool call logs one short warning (no address, no password).
    redis_url: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the settings, built once and reused on every later call."""
    return Settings()
