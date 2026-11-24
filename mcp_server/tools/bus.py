# bus.py: the bus tool. Takes a source city, a destination city and an OPTIONAL journey date, and returns available buses
# (operator, times, price, bus type, seats) from the Parse.bot redBus API.
# Flow: validate the arguments -> work out the date -> Redis cache first -> look up both city IDs -> search buses -> return
# a shaped result that says which date was used.
# No date given: the tool searches TOMORROW. The date is worked out here, in code (India time), never by the model. The
# Parse.bot search always takes an explicit date (`doj`); no "no date" mode is used. The result says the date was assumed
# (date_assumed) so the agent replies "showing results for <date>" and offers to check another date.
# Each search costs 3 Parse.bot calls (2 city lookups + 1 search); the plan allows about 100 calls a day, so a successful
# result is cached for 30 minutes (failures and "no buses" are never cached).
# It never raises into the agent loop: every failure becomes {"ok": False, "error": ...}.
# Prices and seats are third-party (scraped redBus) data and belong to the date searched, so every result says so.
# TODO: the small Redis helper below (_Store) is another copy of the idea in the geocode, routes, images, web search and train
# tools. Move all of them onto one shared cache helper later (noted in claudeprompts.md).

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
import redis.asyncio as redis
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# Parse.bot scraper for redbus.in, pinned to snapshot version 11 so the API contract does not change under us.
SCRAPER_ID = "35a9b6fe-4009-43dd-89e2-993f11b60ad0"
SNAPSHOT_VERSION = "11"
API_BASE_URL = f"https://api.parse.bot/scraper/{SCRAPER_ID}"
# Give up on the external API after this many seconds.
HTTP_TIMEOUT_SECONDS = 30.0
# Most buses returned to the agent, so one busy route cannot flood the model's context.
MAX_BUSES = 20
# Furthest ahead a journey can be searched, in days (redBus does not open sales much earlier).
MAX_DAYS_AHEAD = 120
# India Standard Time, used to decide what "today" and "tomorrow" are.
IST = timezone(timedelta(hours=5, minutes=30))
# A successful result is cached for exactly 30 minutes (a fixed constant: no environment variable for it).
CACHE_TTL_SECONDS = 30 * 60
# Cache keys start with this; bump the "v1" if the cached value's shape ever changes.
CACHE_PREFIX = "buses:v1:"
# Redis is optional and must never slow a tool down: connect and reply timeouts, in seconds.
REDIS_TIMEOUT_SECONDS = 2.0
# Notice attached to every result (CLAUDE.md rule 4).
DISCLAIMER = (
    "Prices and seat counts come from a third-party copy of redBus and can be out of date. "
    "Verify before travel on the operator or redBus. This tool cannot book tickets."
)


def _today() -> date:
    """Today's date in India (a function so tests can fix the date)."""
    return datetime.now(IST).date()


def _tomorrow() -> date:
    """Tomorrow's date in India: the date searched when none was given. Worked out in code, never taken from the model."""
    return _today() + timedelta(days=1)


class BusArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    source_city: str
    destination_city: str
    journey_date: str | None = None  # optional: missing, null or blank means "no date given" (tomorrow is searched)

    @field_validator("source_city", "destination_city")
    @classmethod
    def clean_city(cls, value: str) -> str:
        """Trim the text and allow only a plausible city name (1 to 60 characters)."""
        value = value.strip()
        if not 1 <= len(value) <= 60:
            raise ValueError("city must be 1 to 60 characters")
        # Letters, digits, spaces and , . ' - only; blocks control characters and odd input.
        if not all(ch.isalnum() or ch in " ,.'-" for ch in value):
            raise ValueError("city contains invalid characters")
        return value

    @field_validator("journey_date")
    @classmethod
    def clean_date(cls, value: str | None) -> str | None:
        """No date (None or blank) stays None. A date must be YYYY-MM-DD, today or later, at most MAX_DAYS_AHEAD days away."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("journey_date must be YYYY-MM-DD")
        # fromisoformat also accepts other ISO forms on new Pythons; insist on the exact format.
        if parsed.isoformat() != value:
            raise ValueError("journey_date must be YYYY-MM-DD")
        today = _today()
        if parsed < today or parsed > today + timedelta(days=MAX_DAYS_AHEAD):
            raise ValueError("journey_date out of range")
        return value


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _text(value, limit: int = 100) -> str:
    """Turn any API value into a short plain string (third-party text is data, so keep it small)."""
    return "" if value is None else str(value)[:limit]


def _number(value):
    """Return the value only if it is a real number (not a bool or string), else None."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class _Store:
    """The Redis cache for one search. Redis is an optimisation, not a requirement. If REDIS_URL is not set, Redis is not
    used at all and nothing is logged. If it is set but cannot be reached, ONE short warning is logged per search (no
    address, no password), it is treated as a cache miss, and the tool carries on without it."""

    def __init__(self, url: str):
        self.warned = False
        self.client = None
        if not url or not url.strip():
            self.warned = True  # REDIS_URL is not set: skip the cache silently, with no warning at all
            return
        try:
            self.client = redis.from_url(url.strip(), socket_connect_timeout=REDIS_TIMEOUT_SECONDS, socket_timeout=REDIS_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - a bad URL must not break the tool
            self.client = None
            self._warn()

    def _warn(self) -> None:
        """Log ONE short warning per search that Redis is set but cannot be used. No URL, password or error text."""
        if not self.warned:
            logger.warning("buses: Redis unavailable, continuing without the cache")
            self.warned = True

    async def get(self, key: str):
        """The cached JSON value, or None on a miss, a Redis problem or a damaged entry."""
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
            return json.loads(raw)
        except ValueError:
            return None

    async def set(self, key: str, value) -> None:
        """Cache a JSON value for CACHE_TTL_SECONDS (30 minutes). A Redis problem is ignored."""
        if self.client is None:
            return
        try:
            await self.client.set(key, json.dumps(value, ensure_ascii=False), ex=CACHE_TTL_SECONDS)
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


def _cache_key(source: str, destination: str, day: str) -> str:
    """The Redis key for one search: a hash of the two cities (ignoring case and extra spaces) and the date."""
    text = "|".join(" ".join(part.casefold().split()) for part in (source, destination, day))
    return CACHE_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_cached(value, day: str) -> bool:
    """True when a cached value still has the shape this tool writes, for the date asked (a damaged entry is a miss)."""
    return (
        isinstance(value, dict) and value.get("ok") is True and value.get("date") == day
        and isinstance(value.get("buses"), list) and bool(value["buses"]) and all(isinstance(b, dict) and "operator" in b for b in value["buses"])
        and isinstance(value.get("from"), str) and isinstance(value.get("to"), str)
    )


def _with_date_info(core: dict, assumed: bool) -> dict:
    """The result for the agent: the searched data plus which date was used and whether the tool chose it."""
    day = core["date"]
    result = dict(core)
    result["date_assumed"] = assumed
    result["fares_note"] = (
        f"Fares and seat counts are third-party (redBus copy) data for {day} only; they change by date and can be out of date."
    )
    result["message"] = (
        f"No date was given, so this shows results for {day} (tomorrow). Tell the user \"Showing results for {day}\" and offer to check another date."
        if assumed else f"Showing results for {day}."
    )
    return result


def _headers(api_key: str) -> dict:
    """Request headers. The key travels in a header, never in the URL, so it cannot be logged."""
    return {"X-API-Key": api_key, "API-Snapshot-Version": SNAPSHOT_VERSION}


class _Failure(Exception):
    """Internal: carries an error object out of a helper (never leaves this module)."""

    def __init__(self, error: dict) -> None:
        self.error = error


async def _get(client, path: str, params: dict, api_key: str) -> dict:
    """One GET to Parse.bot. Returns the `data` object, or raises _Failure with an error object."""
    try:
        response = await client.get(f"{API_BASE_URL}/{path}", params=params, headers=_headers(api_key))
    except httpx.HTTPError:
        raise _Failure(_error("unavailable", "Bus information is unavailable right now."))
    if response.status_code == 429:
        # Parse.bot quota (calls per day or credits) used up; details stay out of the reply.
        logger.warning("bus API rate limited (%s)", path)
        raise _Failure(_error("rate_limited", "The bus service has reached its limit for now. Try again later."))
    if response.status_code != 200:
        # Covers 401 (bad key), 422, 5xx. Only the status code is logged.
        logger.warning("bus API returned status %s (%s)", response.status_code, path)
        raise _Failure(_error("unavailable", "Bus information is unavailable right now."))
    try:
        body = response.json()
        data = body["data"] if body.get("status") == "success" else None
    except (ValueError, AttributeError, TypeError, KeyError):
        data = None
    if not isinstance(data, dict):
        raise _Failure(_error("unavailable", "Bus information is unavailable right now."))
    return data


async def _city_id(client, name: str, api_key: str) -> str:
    """Look up a city's redBus ID from its name. Raises _Failure if it cannot."""
    data = await _get(client, "get_city_suggestions", {"query": name, "limit": 5}, api_key)
    docs = [d for d in (data.get("docs") or []) if isinstance(d, dict) and d.get("ID") is not None]
    # Prefer a real city over an airport, station or area.
    cities = [d for d in docs if d.get("locationType") == "CITY"] or docs
    if not cities:
        raise _Failure(_error("city_not_found", f"No city found for '{name}'."))
    return str(cities[0]["ID"])


def _shape_bus(bus: dict) -> dict:
    """Keep only the fields we need for one bus (structured data, not raw text)."""
    # Fares for the seat types on this bus, cleaned and sorted, lowest first.
    fares = sorted({f for f in (_number(x) for x in (bus.get("fareList") or [])) if f is not None})[:8]
    return {
        "operator": _text(bus.get("travelsName")),
        "bus_type": _text(bus.get("busType")),
        "departure_time": _text(bus.get("departureTime"), 20),
        "arrival_time": _text(bus.get("arrivalTime"), 20),
        "duration_minutes": _number(bus.get("journeyDurationMin")),
        "boarding_point": _text(bus.get("standardBpName")),
        "dropping_point": _text(bus.get("standardDpName")),
        "price_from_inr": fares[0] if fares else None,
        "fares_inr": fares,
        "available_seats": _number(bus.get("availableSeats")),
        "total_seats": _number(bus.get("totalSeats")),
        "ac": bus.get("isAc") if isinstance(bus.get("isAc"), bool) else None,
        "sleeper": bus.get("isSleeper") if isinstance(bus.get("isSleeper"), bool) else None,
        "rating": _number(bus.get("totalRatings")),
    }


async def search_buses(source_city: str, destination_city: str, journey_date: str | None = None) -> dict:
    """Find buses between two cities on a date, e.g. ("Bangalore", "Goa", "2026-11-20"). The date is optional.

    journey_date is YYYY-MM-DD, from today up to 120 days ahead. With no date the tool searches TOMORROW (it works the
    date out itself) and says so in the result (date_assumed). Returns operators, departure and arrival times, bus types,
    fares in INR and available seats where the source gives them, or {"ok": False, "error": ..., "message": ...} when it
    cannot. It cannot book anything.
    """
    # 1. Validate the model-supplied arguments.
    try:
        args = BusArgs(source_city=source_city, destination_city=destination_city, journey_date=journey_date)
    except ValidationError:
        return _error(
            "invalid_arguments",
            "Give two different city names and, optionally, a journey date as YYYY-MM-DD, from today up to 120 days ahead.",
        )
    if args.source_city.casefold() == args.destination_city.casefold():
        return _error("invalid_arguments", "The source and destination cities must be different.")

    # 2. The date: the one given, or tomorrow worked out here (never by the model). Searching tomorrow is always in range.
    assumed = args.journey_date is None
    day = _tomorrow().isoformat() if assumed else args.journey_date

    # 3. Needs an API key from the project-root .env.
    settings = get_settings()
    api_key = settings.parsebot_api_key
    if not api_key:
        return _error("not_configured", "The bus service is not configured.")

    # 4. Cache first (Redis is optional): a hit costs no Parse.bot call at all.
    store = _Store(settings.redis_url)
    try:
        key = _cache_key(args.source_city, args.destination_city, day)
        cached = await store.get(key)
        if _valid_cached(cached, day):
            return _with_date_info(cached, assumed)

        # 5. Look up both city IDs, then search; every failure becomes an error object.
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                # Both lookups run together; collect both results so neither is left running if one fails.
                lookups = await asyncio.gather(
                    _city_id(client, args.source_city, api_key),
                    _city_id(client, args.destination_city, api_key),
                    return_exceptions=True,
                )
                for end, lookup in zip(("source", "destination"), lookups):
                    if isinstance(lookup, BaseException):
                        # Say which end had no city (a missing destination is the signal for the gateway flow).
                        if isinstance(lookup, _Failure) and lookup.error.get("error") == "city_not_found":
                            lookup.error["failed_end"] = end
                        raise lookup  # a _Failure is handled below; anything else is a real bug
                from_id, to_id = lookups
                data = await _get(
                    client,
                    "search_buses",
                    {"from_city_id": from_id, "to_city_id": to_id, "doj": day, "limit": MAX_BUSES},
                    api_key,
                )
        except _Failure as failure:
            return failure.error

        # 6. Keep only the fields we need and return them.
        inventories = data.get("inventories")
        if not isinstance(inventories, list) or not inventories:
            error = _error(
                "no_buses_found",
                f"No buses found from {args.source_city} to {args.destination_city} on {day}"
                + (" (tomorrow, used because no date was given). Ask the user for another date." if assumed else "."),
            )
            error.update(date=day, date_assumed=assumed)  # not cached
            return error
        buses = [_shape_bus(b) for b in inventories[:MAX_BUSES] if isinstance(b, dict)]
        total = _number((data.get("metaData") or {}).get("totalCount"))
        core = {
            "ok": True,
            "from": args.source_city,
            "to": args.destination_city,
            "date": day,
            "total_buses_found": total if total is not None else len(buses),
            "buses": buses,
            "buses_truncated": (total or len(inventories)) > len(buses),
            "currency": "INR",
            "source": "redbus via parse.bot",
            "disclaimer": DISCLAIMER,
        }
        if buses:
            await store.set(key, core)  # only a real success is cached
        return _with_date_info(core, assumed)
    finally:
        await store.close()
