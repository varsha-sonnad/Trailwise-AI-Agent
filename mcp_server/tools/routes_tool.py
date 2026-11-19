# routes_tool.py: the get_route tool. Takes an ordered list of 2 to 10 [lat, lng] points and returns the DRIVING route
# through them as [lat, lng] points for drawing on a map, from an OSRM-style routing service (OpenStreetMap data).
# Flow: validate -> Redis cache first -> on a miss, call GET {ROUTING_BASE_URL}/route/v1/driving/{lon,lat;lon,lat}
# with overview=simplified and geometries=geojson (timeout, one retry) -> read ONLY the route's coordinates, swap
# lon/lat to lat/lng, thin to at most MAX_ROUTE_POINTS real points -> cache the successful route for 6 hours.
# Rules this file keeps: the reply's distance, duration and every other field are never read or returned (no
# durations, distances or prices reach the model); no coordinate is ever invented; the tool never raises.
# TODO: the small Redis cache helper below (_Store) is a copy of the idea in geocode_tool.py. Move both tools onto one
# shared cache helper later (noted in claudeprompts.md).

import asyncio
import hashlib
import json
import logging
import math

import httpx
import redis.asyncio as redis
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# How many points a request may contain.
MIN_POINTS = 2
MAX_POINTS = 10
# The only travel mode supported.
MODE = "driving"
# A returned route has at most this many points (evenly spaced real points, first and last kept).
MAX_ROUTE_POINTS = 300
# Coordinates are rounded to this many decimals (5 decimals is about 1 metre) before use, in the URL and in the cache key.
COORD_DECIMALS = 5
# A successful route is cached for exactly 6 hours. A fixed constant on purpose: there is no environment variable for it.
CACHE_TTL_SECONDS = 6 * 60 * 60
# Cache keys start with this; bump the "v1" if the cached value's shape ever changes.
CACHE_PREFIX = "route:v1:"
# Give up on one request after this many seconds.
HTTP_TIMEOUT_SECONDS = 10.0
# One retry after one second, only for a timeout, a network error, 429 and 5xx (never for another 4xx).
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 1.0
# Named in every successful result (the map data is OpenStreetMap's).
SOURCE = "OpenStreetMap OSRM"
# Road-transport data is indicative: every successful result says so (project rule 4).
NOTICE = "Indicative route for the map only. Verify before travel."
# The routing service answers with these codes when it understood the request but found no route.
NO_ROUTE_CODES = ("NoRoute", "NoSegment")


async def _sleep(seconds: float) -> None:
    """Wait without blocking the event loop (a separate function so tests can skip the waiting)."""
    await asyncio.sleep(seconds)


def _valid_coordinates(lat: float, lng: float) -> bool:
    """True only for real, finite numbers inside the valid latitude and longitude ranges."""
    return math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180


def _is_number(value) -> bool:
    """True for an int or a float, but not for a bool (True and False are ints in Python) or any other type."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class RouteArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    points: list[tuple[float, float]]
    mode: str = MODE

    @field_validator("points", mode="before")
    @classmethod
    def clean_points(cls, value):
        """2 to 10 pairs of real numbers in range; each is rounded to 5 decimals. Strings, bools and None are refused."""
        if not isinstance(value, (list, tuple)) or not MIN_POINTS <= len(value) <= MAX_POINTS:
            raise ValueError("wrong number of points")
        cleaned = []
        for point in value:
            if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(_is_number(x) for x in point):
                raise ValueError("a point must be [lat, lng] numbers")
            lat, lng = float(point[0]), float(point[1])
            if not _valid_coordinates(lat, lng):
                raise ValueError("a point is out of range")
            cleaned.append((round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS)))
        return cleaned

    @field_validator("mode")
    @classmethod
    def only_driving(cls, value: str) -> str:
        """Only "driving" is supported."""
        if value != MODE:
            raise ValueError("unsupported mode")
        return value


def _cache_key(points: list[tuple[float, float]]) -> str:
    """The Redis key for an ordered list of points: a hash of the rounded points (order matters) and the mode."""
    text = f"{MODE}|" + ";".join(f"{lat:.{COORD_DECIMALS}f},{lng:.{COORD_DECIMALS}f}" for lat, lng in points)
    return CACHE_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_route(raw, max_points: int | None = MAX_ROUTE_POINTS) -> list[list[float]] | None:
    """Check a list of [lat, lng] pairs (from the API or from the cache) and round them.

    Returns the cleaned list, or None if it is not usable: fewer than 2 points, more than max_points (when set),
    or any point that is not a pair of real numbers in range.
    """
    if not isinstance(raw, list) or len(raw) < 2 or (max_points is not None and len(raw) > max_points):
        return None
    route = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(_is_number(x) for x in point):
            return None
        lat, lng = float(point[0]), float(point[1])
        if not _valid_coordinates(lat, lng):
            return None
        route.append([round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS)])
    return route


class _Store:
    """The Redis cache for one tool call. Redis is an optimisation: any Redis problem is logged once and treated
    as a cache miss, and the tool carries on without it."""

    def __init__(self, url: str):
        self.warned = False
        try:
            self.client = redis.from_url(url)
        except Exception:  # noqa: BLE001 - a bad URL must not break the tool
            self.client = None
            self._warn()

    def _warn(self) -> None:
        """Log (once per call) that Redis cannot be used. No URL or error text, which could hold a password."""
        if not self.warned:
            logger.warning("route: Redis unavailable, continuing without the cache")
            self.warned = True

    async def get(self, key: str) -> list[list[float]] | None:
        """The cached route, or None on a miss, a Redis problem or a damaged entry."""
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
            return _clean_route(json.loads(raw)["route"])
        except (ValueError, KeyError, TypeError):
            return None  # a damaged entry counts as a miss

    async def set(self, key: str, route: list[list[float]]) -> None:
        """Cache a successful route for CACHE_TTL_SECONDS (6 hours). A Redis problem is ignored."""
        if self.client is None:
            return
        try:
            await self.client.set(key, json.dumps({"route": route}), ex=CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            self.client = None
            self._warn()

    async def close(self) -> None:
        """Close the Redis connection (errors ignored)."""
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:  # noqa: BLE001
                pass


def _thin(points: list[list[float]], limit: int = MAX_ROUTE_POINTS) -> list[list[float]]:
    """Keep at most `limit` points: the first, the last, and evenly spaced REAL points between them. None are invented."""
    count = len(points)
    if count <= limit:
        return points
    # count > limit, so the step between picked positions is more than 1 and every rounded position is different.
    return [points[round(i * (count - 1) / (limit - 1))] for i in range(limit)]


def _read_route(body) -> tuple[str, list[list[float]] | None]:
    """Read ONLY the geometry from the routing service's reply.

    Returns ("found", route as [lat, lng] points), ("not_found", None) when the service says there is no route, or
    ("unavailable", None) for a reply that is not usable. The reply's distance, duration, legs and steps are never read.
    """
    if not isinstance(body, dict):
        return "unavailable", None
    code = body.get("code")
    if code in NO_ROUTE_CODES:
        return "not_found", None
    if code != "Ok":
        return "unavailable", None
    try:
        coordinates = body["routes"][0]["geometry"]["coordinates"]
    except (KeyError, IndexError, TypeError):
        return "unavailable", None
    if not isinstance(coordinates, list):
        return "unavailable", None
    # GeoJSON order is [lon, lat] (and sometimes a third number); swap to [lat, lng] and keep only those two.
    swapped = []
    for pair in coordinates:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return "unavailable", None
        swapped.append([pair[1], pair[0]])
    route = _clean_route(swapped, max_points=None)  # the service's route may be long: it is thinned right below
    if route is None:
        return "unavailable", None
    return "found", _thin(route)


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _not_found(unavailable: bool) -> dict:
    """The "no route" result. With unavailable=True the service itself failed, which says nothing about the roads."""
    result = {"ok": True, "found": False}
    if unavailable:
        result["error"] = "unavailable"
    return result


async def _fetch(http: httpx.AsyncClient, base_url: str, points: list[tuple[float, float]]):
    """Ask the routing service for the route, with one retry. Returns the same pair as _read_route."""
    # The URL path takes lon,lat pairs separated by semicolons. Built only from validated, rounded numbers.
    coordinates = ";".join(f"{lng:.{COORD_DECIMALS}f},{lat:.{COORD_DECIMALS}f}" for lat, lng in points)
    url = f"{base_url}/route/v1/{MODE}/{coordinates}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await http.get(url, params={"overview": "simplified", "geometries": "geojson"})
        except httpx.HTTPError:
            pass  # timeout or network error: retry below
        else:
            status = response.status_code
            if status != 429 and status < 500:
                # 200, or a 4xx answer. The service puts "NoRoute" and similar codes in the JSON body (usually with a 400).
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if status == 200 or (isinstance(body, dict) and body.get("code") in NO_ROUTE_CODES):
                    return _read_route(body)
                logger.warning("routing API returned status %s", status)  # only the status code is logged
                return "unavailable", None  # any other 4xx will not get better by asking again
            logger.warning("routing API returned status %s", status)
        if attempt < MAX_RETRIES:
            await _sleep(RETRY_DELAY_SECONDS)
    return "unavailable", None


async def get_route(points: list[list[float]], mode: str = MODE) -> dict:
    """Get the driving route through 2 to 10 [lat, lng] points, as [lat, lng] points for a map.

    Returns {"ok": True, "found": True, "route": [[lat, lng], ...], "source": ..., "notice": ...}. found=False means
    no route. With "error": "unavailable" the service failed: it says nothing about whether a road route exists.
    The reply has no distances, durations or prices.
    """
    try:
        return await _get_route(points, mode)
    except Exception:  # noqa: BLE001 - a failed tool must never crash the agent loop
        logger.warning("route tool failed unexpectedly")
        return _not_found(unavailable=True)


async def _get_route(points, mode) -> dict:
    """The tool's work (get_route wraps it so that nothing can escape as an exception)."""
    # 1. Validate the model-supplied arguments.
    try:
        args = RouteArgs(points=points, mode=mode)
    except ValidationError:
        return _error(
            "invalid_arguments",
            f"points must be {MIN_POINTS} to {MAX_POINTS} [lat, lng] pairs of numbers in range, and mode must be '{MODE}'.",
        )

    # 2. Needs the routing service address from the project-root .env.
    settings = get_settings()
    base_url = settings.routing_base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return _error("not_configured", "Route lookup is not configured.")

    # 3. Cache first. Only successful routes are cached.
    key = _cache_key(args.points)
    store = _Store(settings.redis_url)
    try:
        route = await store.get(key)
        if route is None:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
                status, route = await _fetch(http, base_url, args.points)
            if status == "not_found":
                return _not_found(unavailable=False)
            if status != "found":
                return _not_found(unavailable=True)
            await store.set(key, route)
    finally:
        await store.close()

    return {"ok": True, "found": True, "route": route, "source": SOURCE, "notice": NOTICE}
