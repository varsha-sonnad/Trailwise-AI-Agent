# geocode_tool.py: the geocode_places tool. Takes up to 12 place names and a context such as "Uttarakhand, India",
# and returns the coordinates of each place from a Nominatim-style search API (OpenStreetMap data).
# Flow: validate -> for each name: Redis cache first -> on a miss, wait for the shared 1-request-per-second slot,
# call GET {GEOCODE_BASE_URL}/search (timeout, limited retries) -> read lat and lon from the first result -> cache it.
# Rules this file keeps: coordinates come only from the API (never invented); only the two numbers are read from the
# reply, so no third-party text reaches the model; the tool never raises into the agent loop.

import asyncio
import hashlib
import json
import logging
import math
import time
import unicodedata

import httpx
import redis.asyncio as redis
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# The most place names one call accepts (a full journey fits in one call). More is rejected, not cut.
MAX_PLACES = 12
# Longest place name and context, in characters.
MAX_TEXT_CHARS = 100
# Matches are cached for exactly 30 days. A fixed constant on purpose: there is no environment variable for it.
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
# Cache keys start with this; bump the "v1" if the cached value's shape ever changes.
CACHE_PREFIX = "geocode:v1:"
# The Redis key that hands out the shared request slot (see _Store.acquire_slot).
THROTTLE_KEY = "geocode:throttle"
# At most one request per second to the geocoding service, across every process (Nominatim's usage rule).
MIN_INTERVAL_SECONDS = 1.0
# How often to look again while another process holds the slot.
THROTTLE_POLL_SECONDS = 0.1
# Give up on one request after this many seconds.
HTTP_TIMEOUT_SECONDS = 10.0
# Redis is optional and must never slow a tool down: connect and reply timeouts, in seconds.
REDIS_TIMEOUT_SECONDS = 2.0
# Retries after the first attempt, with the waits before them (seconds). Only for timeouts, network errors, 429 and 5xx.
MAX_RETRIES = 2
RETRY_DELAYS_SECONDS = (1.0, 2.0)
# Named in every result, as the OpenStreetMap data licence asks for attribution.
SOURCE = "OpenStreetMap Nominatim"
# The map zoom hint for ONE found place: city level (the usual range is 10 to 12). With several places a map should fit
# them all instead, so no hint is given. There is no map in the app yet; this is the value a future map view should use.
CITY_ZOOM = 11


async def _sleep(seconds: float) -> None:
    """Wait without blocking the event loop (a separate function so tests can skip the waiting)."""
    await asyncio.sleep(seconds)


def _now() -> float:
    """A clock for the in-process throttle fallback (a separate function so tests can control it)."""
    return time.monotonic()


def _clean_text(value: str, what: str) -> str:
    """Trim a name or context and allow only plausible place text: letters, marks and digits in any script, and , . ' - ( ) / &.

    Marks are allowed because many scripts (Hindi, Kannada, ...) write vowels as combining marks, which Python's
    isalnum() does not count. Control characters and other odd symbols are still refused.
    """
    value = value.strip()
    if len(value) > MAX_TEXT_CHARS:
        raise ValueError(f"{what} is too long")
    if not all(unicodedata.category(ch)[0] in "LMN" or ch in " ,.'-()/&" for ch in value):
        raise ValueError(f"{what} contains invalid characters")
    return value


class GeocodeArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    names: list[str]
    context: str = ""

    @field_validator("names")
    @classmethod
    def clean_names(cls, value: list[str]) -> list[str]:
        """1 to MAX_PLACES names, each 1 to 100 plausible characters."""
        if not 1 <= len(value) <= MAX_PLACES:
            raise ValueError("wrong number of names")
        cleaned = [_clean_text(name, "name") for name in value]
        if any(not name for name in cleaned):
            raise ValueError("a name is empty")
        return cleaned

    @field_validator("context")
    @classmethod
    def clean_context(cls, value: str) -> str:
        """The context (for example "Uttarakhand, India") may be empty, but not long or odd."""
        return _clean_text(value, "context")


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _normalise(text: str) -> str:
    """Lower-case the text and squeeze spaces, so "Haridwar" and " haridwar  " share one cache entry."""
    return " ".join(text.casefold().split())


def _cache_key(name: str, context: str) -> str:
    """The Redis key for one place in one context: a hash, so keys stay short and safe."""
    digest = hashlib.sha256(f"{_normalise(context)}|{_normalise(name)}".encode("utf-8")).hexdigest()
    return CACHE_PREFIX + digest


class _Store:
    """The Redis connection for one tool call: a cache and the shared rate-limit slot.

    Redis is an optimisation, not a requirement. If REDIS_URL is not set, Redis is not used at all and nothing is
    logged. If it is set but cannot be reached, ONE short warning is logged per tool call (no address, no password),
    the problem is treated as a cache miss and a missing shared slot, and the tool carries on (with an in-process
    timer instead of the shared slot).
    """

    def __init__(self, url: str):
        self.warned = False
        self.client = None
        if not url or not url.strip():
            # REDIS_URL is not set: Redis is simply not used. Skip the cache silently, with no warning at all.
            self.warned = True
            return
        try:
            self.client = redis.from_url(
                url.strip(), socket_connect_timeout=REDIS_TIMEOUT_SECONDS, socket_timeout=REDIS_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001 - a bad URL must not break the tool
            self.client = None
            self._warn()

    def _warn(self) -> None:
        """Log ONE short warning per tool call that Redis is set but cannot be used. No URL, password or error text."""
        if not self.warned:
            logger.warning("geocode: Redis unavailable, continuing without the cache")
            self.warned = True

    async def get(self, key: str) -> dict | None:
        """Read a cached {lat, lng}, or None on a miss, a Redis problem or a damaged entry."""
        if self.client is None:
            return None
        try:
            raw = await self.client.get(key)
        except Exception:  # noqa: BLE001
            self.client = None
            self._warn()
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            lat, lng = float(data["lat"]), float(data["lng"])
        except (ValueError, KeyError, TypeError):
            return None  # a damaged entry counts as a miss
        return {"lat": lat, "lng": lng} if _valid_coordinates(lat, lng) else None

    async def set(self, key: str, lat: float, lng: float) -> None:
        """Cache a successful match for CACHE_TTL_SECONDS (30 days). A Redis problem is ignored."""
        if self.client is None:
            return
        try:
            await self.client.set(key, json.dumps({"lat": lat, "lng": lng}), ex=CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            self.client = None
            self._warn()

    async def acquire_slot(self) -> bool | None:
        """Try to take the shared request slot: True = taken, False = someone else has it, None = Redis unusable.

        The slot is a key that expires after one second (SET NX PX), so two requests, even from two different
        processes, always start at least a second apart.
        """
        if self.client is None:
            return None
        try:
            return bool(await self.client.set(THROTTLE_KEY, "1", nx=True, px=int(MIN_INTERVAL_SECONDS * 1000)))
        except Exception:  # noqa: BLE001
            self.client = None
            self._warn()
            return None

    async def close(self) -> None:
        """Close the Redis connection (errors ignored)."""
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:  # noqa: BLE001
                pass


class _Limiter:
    """Makes sure requests to the geocoding service start at least MIN_INTERVAL_SECONDS apart."""

    def __init__(self, store: _Store):
        self.store = store
        self.last_request = None  # when this call last sent one (used only when Redis is unusable)

    async def wait(self) -> None:
        """Wait for our turn. Uses the shared Redis slot, or an in-process timer if Redis cannot be used."""
        while True:
            taken = await self.store.acquire_slot()
            if taken is None:
                break  # Redis is unusable: fall back to the in-process timer below
            if taken:
                return
            await _sleep(THROTTLE_POLL_SECONDS)  # another process has the slot; look again shortly
        if self.last_request is not None:
            gap = MIN_INTERVAL_SECONDS - (_now() - self.last_request)
            if gap > 0:
                await _sleep(gap)
        self.last_request = _now()


def _valid_coordinates(lat: float, lng: float) -> bool:
    """True only for real, finite numbers inside the valid latitude and longitude ranges."""
    return math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180


def _read_first_result(body) -> tuple[str, float | None, float | None]:
    """Read lat and lon (strings in the API reply) from the first result.

    Returns ("found", lat, lng), ("not_found", None, None) for an empty list, or ("unavailable", None, None) for a
    reply that is not usable. Nothing else from the reply is read, and nothing is ever filled in.
    """
    if not isinstance(body, list):
        return "unavailable", None, None
    if not body:
        return "not_found", None, None
    first = body[0]
    try:
        lat, lng = float(first["lat"]), float(first["lon"])
    except (ValueError, KeyError, TypeError):
        return "unavailable", None, None
    if not _valid_coordinates(lat, lng):
        return "unavailable", None, None
    return "found", lat, lng


async def _lookup(http: httpx.AsyncClient, limiter: _Limiter, base_url: str, user_agent: str, query: str):
    """Look one place up, with limited retries. Returns the same triple as _read_first_result."""
    for attempt in range(MAX_RETRIES + 1):
        await limiter.wait()  # every attempt, including a retry, counts as a request
        try:
            response = await http.get(
                f"{base_url}/search",
                params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "in"},
                headers={"User-Agent": user_agent},
            )
        except httpx.HTTPError:
            pass  # timeout or network error: retry below
        else:
            if response.status_code == 200:
                try:
                    return _read_first_result(response.json())
                except ValueError:
                    return "unavailable", None, None  # the reply was not JSON
            if response.status_code != 429 and response.status_code < 500:
                # 403 (blocked), 400 and the like will not get better by asking again. Log only the status code.
                logger.warning("geocode API returned status %s", response.status_code)
                return "unavailable", None, None
            logger.warning("geocode API returned status %s", response.status_code)
        if attempt < MAX_RETRIES:
            await _sleep(RETRY_DELAYS_SECONDS[attempt])
    return "unavailable", None, None


async def geocode_places(names: list[str], context: str = "") -> dict:
    """Find the coordinates of up to 12 places in India, e.g. names ["Haridwar", "Rishikesh"] with context "Uttarakhand, India".

    Returns {"ok": True, "places": [{"name", "lat", "lng", "found"}, ...], "source": ...}, one entry per name in the
    same order, plus "zoom": 11 (city level) when exactly one place was asked for and found. found=False means no match (lat and lng are null). An entry with "error": "unavailable" means the
    lookup failed: it says nothing about whether the place exists. Coordinates are never guessed.
    """
    try:
        return await _geocode(names, context)
    except Exception:  # noqa: BLE001 - a failed tool must never crash the agent loop
        logger.warning("geocode tool failed unexpectedly")
        return _error("unavailable", "Place lookup is unavailable right now.")


async def _geocode(names: list[str], context: str) -> dict:
    """The tool's work (geocode_places wraps it so that nothing can escape as an exception)."""
    # 1. Validate the model-supplied arguments.
    try:
        args = GeocodeArgs(names=names, context=context)
    except ValidationError:
        return _error(
            "invalid_arguments",
            f"names must be 1 to {MAX_PLACES} place names of up to {MAX_TEXT_CHARS} characters, "
            f"and context at most {MAX_TEXT_CHARS} characters.",
        )

    # 2. Needs the service address and the identifying User-Agent from the project-root .env.
    settings = get_settings()
    base_url = settings.geocode_base_url.strip().rstrip("/")
    user_agent = settings.geocode_user_agent.strip()
    if not base_url.startswith(("http://", "https://")) or not user_agent:
        return _error("not_configured", "Place lookup is not configured.")

    # 3. One result per distinct place (the same name twice is looked up once).
    results: dict[str, dict] = {}
    store = _Store(settings.redis_url)
    limiter = _Limiter(store)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
            for name in args.names:
                key = _cache_key(name, args.context)
                if key in results:
                    continue
                # Cache first: a cached match makes no request at all.
                cached = await store.get(key)
                if cached is not None:
                    results[key] = {"name": name, "lat": cached["lat"], "lng": cached["lng"], "found": True}
                    continue
                # Uncached names are looked up one at a time, at most one request per second.
                query = f"{name}, {args.context}" if args.context else name
                status, lat, lng = await _lookup(http, limiter, base_url, user_agent, query)
                if status == "found":
                    await store.set(key, lat, lng)  # only successful matches are cached
                    results[key] = {"name": name, "lat": lat, "lng": lng, "found": True}
                elif status == "not_found":
                    results[key] = {"name": name, "lat": None, "lng": None, "found": False}
                else:
                    # The service failed: not the same as "no such place", so the entry says so.
                    results[key] = {"name": name, "lat": None, "lng": None, "found": False, "error": "unavailable"}
    finally:
        await store.close()

    # 4. One entry per requested name, in the order asked (a repeated name gets its own copy of the entry).
    places = [{**results[_cache_key(name, args.context)], "name": name} for name in args.names]
    result = {"ok": True, "places": places, "source": SOURCE}
    # Exactly one distinct place, and it was found: add the city-level zoom hint for a map centred on it.
    if len({_cache_key(name, args.context) for name in args.names}) == 1 and places[0]["found"]:
        result["zoom"] = CITY_ZOOM
    return result
