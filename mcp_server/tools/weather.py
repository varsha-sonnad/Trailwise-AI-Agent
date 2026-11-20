# weather.py: the weather tool. Takes a place (latitude and longitude, or a place name) and returns current weather
# from OpenWeatherMap.
# Flow: validate the arguments -> try, in order: the coordinates (when given), the location as given, the part before the
# first comma, that part plus ",IN" -> return the first answer (no cache yet).
# OpenWeatherMap only knows city names in its own list and wants "city,country-code", so a name such as
# "Kedarnath, Uttarakhand, India" is a 404 there; the shorter names and the coordinates (from geocode_places) fix that.
# It never raises into the agent loop: every failure becomes {"ok": False, "error": ...}.

import logging
import math

import httpx
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from config import get_settings

logger = logging.getLogger(__name__)

# OpenWeatherMap "current weather" endpoint.
API_URL = "https://api.openweathermap.org/data/2.5/weather"
# Give up on the external API after this many seconds (per attempt).
HTTP_TIMEOUT_SECONDS = 10.0
# Appended to the short city name for the last try (OpenWeatherMap's "city,country-code" form; every place here is in India).
COUNTRY_SUFFIX = ",IN"


class WeatherArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here.

    Either a location (a place name) or both lat and lng must be given. Coordinates are real numbers inside
    the valid ranges (a boolean, text, NaN and infinity are refused).
    """

    location: str = ""
    lat: float | None = None
    lng: float | None = None

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str) -> str:
        """Trim the text and allow only a plausible place name (up to 100 characters, empty means "no name")."""
        value = value.strip()
        if len(value) > 100:
            raise ValueError("location must be at most 100 characters")
        # Letters, digits, spaces and , . ' - only; blocks control characters and odd input.
        if not all(ch.isalnum() or ch in " ,.'-" for ch in value):
            raise ValueError("location contains invalid characters")
        return value

    @field_validator("lat", "lng", mode="before")
    @classmethod
    def clean_number(cls, value):
        """A real number (int or float): not a boolean, not text, not NaN or infinity."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("coordinates must be real numbers")
        return float(value)

    @model_validator(mode="after")
    def check_what_was_given(self):
        """lat and lng go together and stay in range; and there must be a name or coordinates to look up."""
        if (self.lat is None) != (self.lng is None):
            raise ValueError("give both lat and lng, or neither")
        if self.lat is not None and not (-90 <= self.lat <= 90 and -180 <= self.lng <= 180):
            raise ValueError("lat must be -90 to 90 and lng -180 to 180")
        if self.lat is None and not self.location:
            raise ValueError("give a location, or lat and lng")
        return self


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


def candidate_names(location: str) -> list[str]:
    """The names to try, in order: the location as given, the part before the first comma, that part plus ",IN".

    Repeats are dropped (ignoring case), so "Goa" gives ["Goa", "Goa,IN"] and "Kedarnath, Uttarakhand, India" gives
    ["Kedarnath, Uttarakhand, India", "Kedarnath", "Kedarnath,IN"]. No location gives no names.
    """
    if not location:
        return []
    short = location.split(",", 1)[0].strip()
    names = [location]
    if short:
        names += [short, short + COUNTRY_SUFFIX]
    unique, seen = [], set()
    for name in names:
        if name.lower() not in seen:
            seen.add(name.lower())
            unique.append(name)
    return unique


async def get_weather(location: str = "", lat: float | None = None, lng: float | None = None) -> dict:
    """Get the current weather. Prefer lat and lng (from geocode_places); otherwise give a plain city name, e.g. "Goa".

    Returns temperature (C), feels-like, humidity, wind speed (m/s) and conditions,
    or {"ok": False, "error": ..., "message": ...} when it cannot.
    """
    # 1. Validate the model-supplied arguments.
    try:
        args = WeatherArgs(location=location, lat=lat, lng=lng)
    except ValidationError:
        return _error(
            "invalid_arguments",
            "give lat and lng (numbers, from geocode_places) or a location of up to 100 plain characters.",
        )

    # 2. Needs an API key from the project-root .env.
    api_key = get_settings().openweather_api_key
    if not api_key:
        return _error("not_configured", "Weather service is not configured.")

    # 3. The things to try, in order: the coordinates first, then the names. Each is the query for one API call.
    names = candidate_names(args.location)
    attempts: list[tuple[str, dict]] = []
    if args.lat is not None:
        attempts.append(("coordinates", {"lat": args.lat, "lon": args.lng}))
    attempts += [(name, {"q": name}) for name in names]

    # 4. Call the API with a timeout. Only a 404 ("not found") moves on to the next try; any other failure stops at once.
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            for _label, query in attempts:
                response = await client.get(API_URL, params={**query, "appid": api_key, "units": "metric"})
                if response.status_code == 404:
                    continue
                if response.status_code != 200:
                    # Covers 401 (bad key), 429 (quota) and 5xx. Details stay in the log, not in the reply.
                    logger.warning("weather API returned status %s", response.status_code)
                    return _error("unavailable", "Weather is unavailable right now.")
                try:
                    return _shape(response.json())
                except (ValueError, AttributeError, TypeError):
                    return _error("unavailable", "Weather is unavailable right now.")
    except httpx.HTTPError:
        return _error("unavailable", "Weather is unavailable right now.")

    # 5. Every try said "not found". Log the location string and the names tried (never the user's message or any key).
    logger.warning(
        "weather location not found: location=%r coordinates_tried=%s names_tried=%s",
        args.location, args.lat is not None, names,
    )
    shown = args.location or "those coordinates"
    return _error("location_not_found", f"No weather found for '{shown}'.")
