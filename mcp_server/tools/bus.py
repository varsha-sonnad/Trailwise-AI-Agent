# bus.py: the bus tool. Takes a source city, a destination city and a journey date, and returns
# available buses (operator, times, price, bus type, seats) from the Parse.bot redBus API.
# Flow: validate the arguments -> look up both city IDs -> search buses -> return a shaped result (no cache yet).
# Each search costs 3 Parse.bot calls (2 city lookups + 1 search); the plan allows about 100 calls a day.
# It never raises into the agent loop: every failure becomes {"ok": False, "error": ...}.
# Prices and seats are third-party (scraped redBus) data, so every result carries a "verify" notice.

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
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
# India Standard Time, used to decide what "today" is.
IST = timezone(timedelta(hours=5, minutes=30))
# Notice attached to every result (CLAUDE.md rule 4).
DISCLAIMER = (
    "Prices and seat counts come from a third-party copy of redBus and can be out of date. "
    "Verify before travel on the operator or redBus. This tool cannot book tickets."
)


def _today() -> date:
    """Today's date in India (a function so tests can fix the date)."""
    return datetime.now(IST).date()


class BusArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    source_city: str
    destination_city: str
    journey_date: str

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
    def clean_date(cls, value: str) -> str:
        """Require YYYY-MM-DD, today or later, and not more than MAX_DAYS_AHEAD days away."""
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


async def search_buses(source_city: str, destination_city: str, journey_date: str) -> dict:
    """Find buses between two cities on a date, e.g. ("Bangalore", "Goa", "2026-11-20").

    journey_date is YYYY-MM-DD, from today up to 120 days ahead. Returns operators, departure and
    arrival times, bus types, fares in INR and available seats where the source gives them, or
    {"ok": False, "error": ..., "message": ...} when it cannot. It cannot book anything.
    """
    # 1. Validate the model-supplied arguments.
    try:
        args = BusArgs(source_city=source_city, destination_city=destination_city, journey_date=journey_date)
    except ValidationError:
        return _error(
            "invalid_arguments",
            "Give two different city names and a journey date as YYYY-MM-DD, from today up to 120 days ahead.",
        )
    if args.source_city.casefold() == args.destination_city.casefold():
        return _error("invalid_arguments", "The source and destination cities must be different.")

    # 2. Needs an API key from the project-root .env.
    api_key = get_settings().parsebot_api_key
    if not api_key:
        return _error("not_configured", "The bus service is not configured.")

    # 3. Look up both city IDs, then search; every failure becomes an error object.
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            # Both lookups run together; collect both results so neither is left running if one fails.
            lookups = await asyncio.gather(
                _city_id(client, args.source_city, api_key),
                _city_id(client, args.destination_city, api_key),
                return_exceptions=True,
            )
            for lookup in lookups:
                if isinstance(lookup, BaseException):
                    raise lookup  # a _Failure is handled below; anything else is a real bug
            from_id, to_id = lookups
            data = await _get(
                client,
                "search_buses",
                {"from_city_id": from_id, "to_city_id": to_id, "doj": args.journey_date, "limit": MAX_BUSES},
                api_key,
            )
    except _Failure as failure:
        return failure.error

    # 4. Keep only the fields we need and return them.
    inventories = data.get("inventories")
    if not isinstance(inventories, list) or not inventories:
        return _error("no_buses_found", f"No buses found from {args.source_city} to {args.destination_city} on {args.journey_date}.")
    buses = [_shape_bus(b) for b in inventories[:MAX_BUSES] if isinstance(b, dict)]
    total = _number((data.get("metaData") or {}).get("totalCount"))
    return {
        "ok": True,
        "from": args.source_city,
        "to": args.destination_city,
        "date": args.journey_date,
        "total_buses_found": total if total is not None else len(buses),
        "buses": buses,
        "buses_truncated": (total or len(inventories)) > len(buses),
        "currency": "INR",
        "source": "redbus via parse.bot",
        "disclaimer": DISCLAIMER,
    }
