# weather.py: the weather tool. Takes a location, returns current weather from OpenWeatherMap.
# Flow: validate the argument -> call the API -> return the result (no cache yet).
# It never raises into the agent loop: every failure becomes {"ok": False, "error": ...}.

import logging

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# OpenWeatherMap "current weather" endpoint.
API_URL = "https://api.openweathermap.org/data/2.5/weather"
# Give up on the external API after this many seconds.
HTTP_TIMEOUT_SECONDS = 10.0


class WeatherArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    location: str

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str) -> str:
        """Trim the text and allow only a plausible place name (1 to 100 characters)."""
        value = value.strip()
        if not 1 <= len(value) <= 100:
            raise ValueError("location must be 1 to 100 characters")
        # Letters, digits, spaces and , . ' - only; blocks control characters and odd input.
        if not all(ch.isalnum() or ch in " ,.'-" for ch in value):
            raise ValueError("location contains invalid characters")
        return value


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _shape(data: dict) -> dict:
    """Keep only the fields we need from the API response (structured data, not raw text)."""
    weather = (data.get("weather") or [{}])[0]
    main = data.get("main") or {}
    return {
        "ok": True,
        "location": str(data.get("name", ""))[:100],
        "country": str((data.get("sys") or {}).get("country", ""))[:2],
        "temperature_c": main.get("temp"),
        "feels_like_c": main.get("feels_like"),
        "humidity_pct": main.get("humidity"),
        "wind_speed_ms": (data.get("wind") or {}).get("speed"),
        "conditions": str(weather.get("description", ""))[:100],
        "source": "openweathermap",
    }


async def get_weather(location: str) -> dict:
    """Get the current weather for a place name, e.g. "Goa" or "Paris, FR".

    Returns temperature (C), feels-like, humidity, wind speed (m/s) and conditions,
    or {"ok": False, "error": ..., "message": ...} when it cannot.
    """
    # 1. Validate the model-supplied argument.
    try:
        args = WeatherArgs(location=location)
    except ValidationError:
        return _error("invalid_arguments", "location must be a place name of 1 to 100 characters.")

    # 2. Needs an API key from the project-root .env.
    api_key = get_settings().openweather_api_key
    if not api_key:
        return _error("not_configured", "Weather service is not configured.")

    # 3. Call the API with a timeout; turn every failure into an error object.
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(
                API_URL, params={"q": args.location, "appid": api_key, "units": "metric"}
            )
    except httpx.HTTPError:
        return _error("unavailable", "Weather is unavailable right now.")

    if response.status_code == 404:
        return _error("location_not_found", f"No weather found for '{args.location}'.")
    if response.status_code != 200:
        # Covers 401 (bad key), 429 (quota) and 5xx. Details stay in the log, not in the reply.
        logger.warning("weather API returned status %s", response.status_code)
        return _error("unavailable", "Weather is unavailable right now.")

    # 4. Keep only the fields we need and return them.
    try:
        return _shape(response.json())
    except (ValueError, AttributeError, TypeError):
        return _error("unavailable", "Weather is unavailable right now.")
