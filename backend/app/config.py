# config.py: loads all app settings (API keys, database URLs) from environment variables or a .env file.
# The app refuses to start if a required setting is missing: the Gemini API key, or MONGODB_URI (the MongoDB Atlas
# connection string), which gets its own plain-language error below.

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

    # Gemini API key (env var GEMINI_API_KEY). Required: min_length=1 rejects an empty value, so startup fails if it is missing.
    gemini_api_key: str = Field(min_length=1)
    # Which Gemini model the agent uses.
    gemini_model: str = "gemini-3.5-flash"
    # Optional backup model (env var GEMINI_FALLBACK_MODEL), tried when the main model is still failing
    # with a server error (500, 503, 504) after its retries. Empty means "no fallback".
    gemini_fallback_model: str = ""

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
            raise  # some other setting (for example the Gemini key): keep pydantic's own message
        wrong_scheme = any(e["type"] == "value_error" for e in mongo_errors)
        # "from None" drops the original error from the traceback, because it would print the secret value.
        raise RuntimeError(MONGO_BAD_SCHEME_MESSAGE if wrong_scheme else MONGO_NOT_SET_MESSAGE) from None
