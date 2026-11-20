# config.py: loads all app settings (API keys, database URLs) from environment variables or a .env file.
# The app refuses to start if MONGODB_URI (the MongoDB Atlas connection string) is missing; that gets its own
# plain-language error below. OPENAI_API_KEY is required too (it keeps pydantic's own message).

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, ValidationError, field_validator
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

    # OpenAI (the agent uses these settings).
    # OPENAI_API_KEY is REQUIRED: the agent uses OpenAI, and min_length=1 rejects an empty value, so startup fails without it.
    openai_api_key: str = Field(min_length=1)
    # OPENAI_MODEL and OPENAI_FALLBACK_MODEL have no defaults on purpose: model names change, so you choose them in
    # .env. Empty means "not set"; the fallback is an optional backup for when the main model keeps failing.
    openai_model: str = ""
    openai_fallback_model: str = ""
    # How long to wait for one OpenAI call, in seconds (env var OPENAI_TIMEOUT_SECONDS). Must be above 0 and at most 600.
    openai_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    # The agent's time limit for one web_search call to the MCP tool server, in seconds (env var WEB_SEARCH_CALL_TIMEOUT_SECONDS;
    # the MCP server reads the same variable). It must stay above the search's own timeout (OPENAI_SEARCH_TIMEOUT_SECONDS,
    # default 120): the tool answers "timeout" itself before this limit cuts it off. Other tools keep a 30-second limit.
    web_search_call_timeout_seconds: float = Field(default=150.0, gt=0, le=900)
    # One overall cap for a whole chat request, in seconds (env var CHAT_REQUEST_TIMEOUT_SECONDS). When it runs out the user gets a
    # friendly "that took too long" reply. Keep it above the web_search call limit plus a couple of model calls.
    chat_request_timeout_seconds: float = Field(default=180.0, gt=0, le=900)

    # MongoDB Atlas connection string. REQUIRED, there is no default: put it in MONGODB_URI in .env (or use
    # MONGO_URL as a fallback name). Both mongodb+srv:// and mongodb:// strings are accepted (see the check below).
    # min_length=1 rejects an empty value.
    mongo_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("MONGODB_URI", "MONGO_URL"),
    )
    mongo_db: str = "trailwise"
    # Redis connection (matches the service in docker-compose.yml).
    redis_url: str = "redis://localhost:6379/0"

    # Keycloak (authentication). KEYCLOAK_URL is the public address the browser uses; it is also the base of the
    # token issuer, so it must match what Keycloak puts in tokens. KEYCLOAK_INTERNAL_URL is where THIS server
    # fetches Keycloak's public keys when that differs (in Docker: http://keycloak:8080). Empty = use KEYCLOAK_URL.
    keycloak_url: str = "http://localhost:8080"
    keycloak_internal_url: str = ""
    keycloak_realm: str = "trailwise"
    # The "aud" value a token must carry (the realm file adds it to tokens through an audience mapper).
    keycloak_audience: str = "trailwise-api"

    # Audit log of agent activity (see app/core/audit.py). The file sits in backend/logs/ by default.
    audit_log_enabled: bool = True
    audit_log_path: str = str(Path(__file__).resolve().parents[1] / "logs" / "audit.log")

    # Optional plain-text debug log for LOCAL TESTING (see app/core/debug_log.py); both are off by default.
    # DEBUG_LOG_FILE=true writes a rotating debug.log (REQUEST, TOOL, RESULT and ERROR lines). LOG_MESSAGES=true, only together
    # with it, also writes the start of each question and answer (emails and phone numbers masked). The JSON audit log is unchanged.
    # The file is <project root>/logs/debug.log, which in Docker is /app/logs/debug.log (mounted by docker-compose.override.yml).
    debug_log_file: bool = False
    log_messages: bool = False
    debug_log_path: str = str(Path(__file__).resolve().parents[2] / "logs" / "debug.log")

    # Longest chat message accepted from a user, in characters.
    max_message_chars: int = 1000

    @field_validator("mongo_url")
    @classmethod
    def _check_mongo_scheme(cls, value: str) -> str:
        """Accept only mongodb+srv:// or mongodb:// strings. The message never contains the value (it holds a password)."""
        if not value.startswith(("mongodb+srv://", "mongodb://")):
            raise ValueError("wrong scheme")
        return value


# What to tell the user when MONGODB_URI is missing, empty or malformed. No value is ever included.
MONGO_NOT_SET_MESSAGE = (
    "MONGODB_URI is not set. Put your MongoDB Atlas connection string (it starts with mongodb+srv://) in the "
    "project-root .env file as MONGODB_URI=... (see .env.example), then start the app again."
)
MONGO_BAD_SCHEME_MESSAGE = (
    "MONGODB_URI is not a valid MongoDB connection string: it must start with mongodb+srv:// or mongodb:// "
    "(copy it from MongoDB Atlas: Connect > Drivers). The value is not shown here because it contains a password."
)


@lru_cache
def get_settings() -> Settings:
    """Return the settings, built once and reused on every later call.

    A missing, empty or malformed MONGODB_URI stops startup with a clear one-line error that never shows the value.
    """
    try:
        return Settings()
    except ValidationError as exc:
        # Look only at the error kinds and field names, never at the input values (they can hold secrets).
        mongo_errors = [e for e in exc.errors() if "MONGODB_URI" in map(str, e["loc"]) or "MONGO_URL" in map(str, e["loc"])]
        if not mongo_errors:
            raise  # some other setting (for example the OpenAI key): keep pydantic's own message
        wrong_scheme = any(e["type"] == "value_error" for e in mongo_errors)
        # "from None" drops the original error from the traceback, because it would print the secret value.
        raise RuntimeError(MONGO_BAD_SCHEME_MESSAGE if wrong_scheme else MONGO_NOT_SET_MESSAGE) from None
