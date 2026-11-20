# train_tool.py: the search_trains tool. Takes a source city, a destination city and an optional date, and finds the
# scheduled trains between them through RailRadar (its own station search and its trains-between-stations endpoint).
# Flow: validate -> look up each city's main station (RailRadar station search, cached 30 days; KNOWN_STATIONS can skip
# the lookup) -> GET /v1/trains/between/{from}/{to}?byCity=true (so every train shows its real station; all trains come
# back in one answer, no pagination) -> shape the answer.
#   no date: the trains that run EVERY DAY are returned in full, and the agent is told to ask for the date;
#   a date: the trains running on that date; if there are none, the nearby dates that have trains, worked out LOCALLY from
#           the trains' run days (labelled "based on the weekly schedule, verify before travel", never confirmed by the API).
# Rules this file keeps: at most 3 requests to RailRadar per search (no retries; the free plan has only 100 a month);
# station lookups are cached for 30 days and search results for 6 hours in Redis (optional, like the other tools), failures
# and empty results are never cached; a 429 becomes "quota_exceeded" with the message "Train data is unavailable right now.";
# only scheduled data is returned (no seats, fares or live status) with a verify-before-travel notice; the key goes only
# in the Authorization header and is never logged; the tool never raises into the agent loop.
# TODO: the small Redis helper below (_Store) is another copy of the idea in the geocode, routes, images and web search
# tools. Move all of them onto one shared cache helper later (noted in claudeprompts.md).

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import unicodedata

import httpx
import redis.asyncio as redis
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from config import get_settings

logger = logging.getLogger(__name__)

# RailRadar API host (the website railradar.in is not the API).
API_BASE_URL = "https://api.railradar.in"
# Give up on one request after this many seconds. Worst case: two lookups at the same time, then two more requests in a row.
HTTP_TIMEOUT_SECONDS = 8.0
# The most requests one search may make (station lookups included). No retries: a retry would be another request.
MAX_REQUESTS = 3
# How many stations one lookup asks for.
LOOKUP_LIMIT = 10
# Station lookups are cached for 30 days and search results for 6 hours (fixed constants: no environment variables).
STATION_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
SEARCH_CACHE_TTL_SECONDS = 6 * 60 * 60
# Cache keys start with this; bump the "v1" if a cached value's shape ever changes.
CACHE_PREFIX = "trains:v1:"
# Redis is optional and must never slow a tool down: connect and reply timeouts, in seconds.
REDIS_TIMEOUT_SECONDS = 2.0
# The date, when given, must be today or later and at most this many days ahead (India time, like the bus tool).
MAX_DAYS_AHEAD = 120
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# Nearby dates: look this many days either side, and suggest at most this many before and after the requested date.
NEARBY_WINDOW_DAYS = 7
NEARBY_BEFORE = 2
NEARBY_AFTER = 2
# A station with the same name as the best match is a "close rival" (the user is asked) when its popularity is at least
# this share of the best match's. Tiny halts with a repeated name are ignored.
CLOSE_RIVAL_SHARE = 0.2
# Words ignored when comparing station names ("Bilaspur" and "Bilaspur Jn" count as the same name).
_NAME_FILLER = frozenset({"jn", "junction", "jct", "cantt", "central", "city"})
# The days of the week as RailRadar writes them in runDays; the position is Python's weekday number (Monday is 0).
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Stations that skip the lookup: normalised city name -> (station code, station name). It starts EMPTY on purpose. Add a
# city only with a code and name that RailRadar's own station search has actually returned, never from memory, and never
# a station whose answer is ambiguous. The 30-day lookup cache does the same job for every city that was asked before.
KNOWN_STATIONS: dict[str, tuple[str, str]] = {}

# Notice attached to every result (CLAUDE.md rule 4).
DISCLAIMER = (
    "Scheduled information only, not live status. Seat availability and fares are not provided. "
    "Verify before travel on the official IRCTC or Indian Railways channels."
)
# Attached to nearby dates: they are worked out from the weekly schedule, not confirmed for those exact dates.
NEARBY_NOTE = "Based on the weekly schedule, verify before travel."
# What a 429 (quota or rate limit) says.
QUOTA_MESSAGE = "Train data is unavailable right now."
UNAVAILABLE_MESSAGE = "Train information is unavailable right now."


def _today() -> dt.date:
    """Today's date in India (a function so tests can fix the date)."""
    return dt.datetime.now(IST).date()


def _clean_city(value: str) -> str:
    """Trim a city or station text and allow only plausible text: letters, marks and digits in any script, and , . ' - ( ) / &."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("city contains control characters")
    value = " ".join(value.split())
    if not 2 <= len(value) <= 100:
        raise ValueError("city must be 2 to 100 characters")
    if not all(unicodedata.category(ch)[0] in "LMN" or ch in " ,.'-()/&" for ch in value):
        raise ValueError("city contains invalid characters")
    return value


class SearchArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    source_city: str
    destination_city: str
    date: str = ""

    @field_validator("source_city", "destination_city")
    @classmethod
    def clean_city(cls, value: str) -> str:
        return _clean_city(value)

    @field_validator("date")
    @classmethod
    def clean_date(cls, value: str) -> str:
        """Empty (no date), or exactly YYYY-MM-DD, a real date from today up to MAX_DAYS_AHEAD days ahead."""
        value = value.strip()
        if not value:
            return ""
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")
        if parsed.isoformat() != value:  # fromisoformat accepts other ISO forms on new Pythons; insist on the exact format
            raise ValueError("date must be YYYY-MM-DD")
        today = _today()
        if parsed < today or parsed > today + dt.timedelta(days=MAX_DAYS_AHEAD):
            raise ValueError("date out of range")
        return value

    @model_validator(mode="after")
    def different_cities(self):
        """Two different places (the same text twice would search a train from a station to itself)."""
        if _normalise(self.source_city) == _normalise(self.destination_city):
            raise ValueError("source and destination must differ")
        return self


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _normalise(text: str) -> str:
    """Lower-case the text and squeeze spaces, so "Pune" and " pune " are the same city (for the cache and the list)."""
    return " ".join(text.lower().split())


def _name_key(name: str) -> str:
    """A station name for comparing: lower case, punctuation removed, filler words such as "Jn" dropped."""
    words = re.sub(r"[^\w\s]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in _NAME_FILLER)


def _text(value, limit: int = 100) -> str:
    """Turn any API value into a short plain string (third-party text is data, so keep it small)."""
    return "" if value is None else " ".join(str(value).split())[:limit]


def _number(value):
    """A real, non-negative number from the API, or None (booleans and text are not numbers)."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _code(value) -> str:
    """A station code: 1 to 10 ASCII letters or digits (so it is safe in a URL path), else an empty string."""
    text = str(value or "").strip()
    return text.upper() if 1 <= len(text) <= 10 and text.isascii() and text.isalnum() else ""


# ---------------------------------------------------------------- shaping RailRadar's answers

def _shape_train(item) -> dict | None:
    """One train of a trains-between answer as our compact fields, or None when it has no usable number or stations."""
    if not isinstance(item, dict):
        return None
    train, src, dst = item.get("train"), item.get("from"), item.get("to")
    if not all(isinstance(x, dict) for x in (train, src, dst)):
        return None
    number, from_code, to_code = _text(train.get("number"), 10), _code(src.get("code")), _code(dst.get("code"))
    if not number or not from_code or not to_code:
        return None
    given = {str(d).lower() for d in (train.get("runDays") if isinstance(train.get("runDays"), list) else [])}
    run_days = [d for d in DAYS if d in given]  # in week order, only real day names
    from_day, to_day = _number(src.get("day")), _number(dst.get("day"))
    return {
        "number": number,
        "name": _text(train.get("name")),
        "type": _text(train.get("type")),
        "run_days": run_days,
        "runs_daily": len(run_days) == 7,
        # The stations the train really uses (byCity=true can widen a city to several stations).
        "from": {"code": from_code, "name": _text(src.get("name")), "city": _text(src.get("city")), "departure": _text(src.get("departure"), 20), "day": from_day},
        "to": {"code": to_code, "name": _text(dst.get("name")), "city": _text(dst.get("city")), "arrival": _text(dst.get("arrival"), 20), "day": to_day},
        # 0 = arrives the same day it leaves, 1 = the next day, and so on.
        "arrives_after_days": int(to_day - from_day) if from_day is not None and to_day is not None and to_day >= from_day else None,
        "duration_minutes": _number(item.get("duration")),  # RailRadar's duration is in minutes (checked against the times)
        "distance_km": _number(item.get("distance")),
        "halts_between": _number(item.get("totalHaltsBetween")),
    }


def _shape_trains(body) -> list[dict] | None:
    """All usable trains of a trains-between answer, in the order RailRadar gave them, or None when the body is not that shape."""
    data = body.get("data") if isinstance(body, dict) else None
    trains = data.get("trains") if isinstance(data, dict) else None
    if not isinstance(trains, list):
        return None
    return [t for t in (_shape_train(x) for x in trains) if t is not None]


def _valid_cached_train(train) -> bool:
    """True when a cached train still has exactly the shape this tool writes (a damaged entry is a cache miss)."""
    try:
        return (
            isinstance(train, dict) and isinstance(train["number"], str) and train["number"]
            and isinstance(train["from"], dict) and isinstance(train["to"], dict)
            and bool(_code(train["from"]["code"])) and bool(_code(train["to"]["code"]))
            and isinstance(train["run_days"], list) and set(train["run_days"]) <= set(DAYS)
            and isinstance(train["runs_daily"], bool)
        )
    except (KeyError, TypeError):
        return False


def _candidates(body) -> list[dict]:
    """The active stations of a station-search answer as {code, name, popularity}, in the order given."""
    data = body.get("data") if isinstance(body, dict) else None
    stations = []
    for s in data if isinstance(data, list) else []:
        if not isinstance(s, dict) or s.get("isActive") is False:
            continue
        code, name = _code(s.get("code")), _text(s.get("name"))
        if code and name:
            stations.append({"code": code, "name": name, "popularity": _number(s.get("popularity")) or 0})
    return stations


def _choose(candidates: list[dict], asked: str) -> tuple[str, object]:
    """Pick the station for a city. Returns ("ok", station), ("ambiguous", [options]) or ("not_found", None).

    A station whose code is exactly what was typed is taken as the user's own choice. Otherwise the most popular active
    station (the main junction) wins, unless another station with the same name is nearly as popular: then the user is
    asked (the options are returned).
    """
    if not candidates:
        return "not_found", None
    typed = asked.strip().upper()
    for c in candidates:
        if c["code"] == typed:
            return "ok", c
    ranked = sorted(candidates, key=lambda c: c["popularity"], reverse=True)
    top = ranked[0]
    rivals = [
        c for c in ranked[1:]
        if _name_key(c["name"]) == _name_key(top["name"]) and c["popularity"] >= CLOSE_RIVAL_SHARE * top["popularity"]
    ]
    if rivals:
        return "ambiguous", [top, *rivals]
    return "ok", top


# ---------------------------------------------------------------- the Redis cache

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
            logger.warning("trains: Redis unavailable, continuing without the cache")
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

    async def set(self, key: str, value, ttl: int) -> None:
        """Cache a JSON value for `ttl` seconds. A Redis problem is ignored."""
        if self.client is None:
            return
        try:
            await self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
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


def _station_key(city: str) -> str:
    return CACHE_PREFIX + "station:" + hashlib.sha256(_normalise(city).encode("utf-8")).hexdigest()


def _search_key(from_code: str, to_code: str, date: str) -> str:
    return f"{CACHE_PREFIX}search:{from_code}:{to_code}:{date or 'any'}"


# ---------------------------------------------------------------- the RailRadar calls (at most MAX_REQUESTS per search)

class _Api:
    """RailRadar calls for one search. It counts the requests and refuses the 4th: the search stays within its budget."""

    def __init__(self, client: httpx.AsyncClient, key: str):
        self.client, self.key, self.used = client, key, 0

    @property
    def left(self) -> int:
        return MAX_REQUESTS - self.used

    async def get(self, path: str, params: dict):
        """One GET. Returns (status, body): status is "ok", "not_found" (404), "quota" (429), "unavailable" or "budget"."""
        if self.left <= 0:
            return "budget", None
        self.used += 1
        try:
            response = await self.client.get(API_BASE_URL + path, params=params, headers={"Authorization": f"Bearer {self.key}"})
        except httpx.HTTPError:
            return "unavailable", None
        if response.status_code == 404:
            return "not_found", None
        if response.status_code == 429:
            logger.warning("train API returned status 429")
            return "quota", None
        if response.status_code != 200:
            # Covers 400, 401 (bad key), 403 and 5xx. Details stay in the log, not in the reply.
            logger.warning("train API returned status %s", response.status_code)
            return "unavailable", None
        try:
            body = response.json()
        except ValueError:
            return "unavailable", None
        return ("ok", body) if isinstance(body, dict) else ("unavailable", None)


async def _find_station(api: _Api, store: _Store, city: str) -> tuple[str, object]:
    """The station for one city: ("ok", {code, name}), ("ambiguous", [options]), ("not_found", None), ("quota", None) or ("unavailable", None)."""
    known = KNOWN_STATIONS.get(_normalise(city))
    if known:
        return "ok", {"code": known[0], "name": known[1]}  # no lookup, no request
    cached = await store.get(_station_key(city))
    candidates = [c for c in cached if isinstance(c, dict) and _code(c.get("code")) and isinstance(c.get("name"), str)] if isinstance(cached, list) else []
    if not candidates:
        status, body = await api.get("/v1/lookup/search/stations", {"q": city, "limit": LOOKUP_LIMIT})
        if status == "not_found":
            return "not_found", None
        if status != "ok":
            return status, None
        candidates = _candidates(body)
        if candidates:
            await store.set(_station_key(city), candidates, STATION_CACHE_TTL_SECONDS)  # only a real answer is cached
    status, picked = _choose(candidates, city)
    return (status, {"code": picked["code"], "name": picked["name"]}) if status == "ok" else (status, picked)


async def _between(api: _Api, store: _Store, from_code: str, to_code: str, date: str):
    """All trains between two stations (with byCity=true), for a date or ("" ) without one.

    Returns (status, trains): "ok" with the list (it may be empty), "not_found" (an unknown station), "quota",
    "unavailable", or "budget" (no request left and nothing cached).
    """
    cached = await store.get(_search_key(from_code, to_code, date))
    if isinstance(cached, list) and cached and all(_valid_cached_train(t) for t in cached):
        return "ok", cached
    params = {"byCity": "true"}
    if date:
        params["date"] = date
    status, body = await api.get(f"/v1/trains/between/{from_code}/{to_code}", params)
    if status != "ok":
        return status, None
    trains = _shape_trains(body)
    if trains is None:
        return "unavailable", None
    if trains:
        await store.set(_search_key(from_code, to_code, date), trains, SEARCH_CACHE_TTL_SECONDS)  # empty results are not cached
    return "ok", trains


# ---------------------------------------------------------------- working out dates locally

def runs_on(train: dict, day: dt.date) -> bool:
    """True when the train is at its starting station (for this search) on that date, by its weekly run days.

    runDays are the days the train starts its journey. A train that is at this station on the 2nd day of its journey is
    here one day later than it started, so each run day is shifted by (day - 1). (Checked against RailRadar: a train
    running Tuesday and Thursday that is here on day 2 is returned for a Friday.)
    """
    shift = int(train["from"]["day"]) - 1 if isinstance(train["from"].get("day"), (int, float)) and train["from"]["day"] >= 1 else 0
    weekdays = {(DAYS.index(d) + shift) % 7 for d in train["run_days"] if d in DAYS}
    return day.weekday() in weekdays


def _nearby_dates(trains: list[dict], wanted: dt.date) -> list[dict]:
    """The nearest dates (at most NEARBY_BEFORE before and NEARBY_AFTER after the wanted one, within NEARBY_WINDOW_DAYS)
    on which at least one train runs, by the trains' weekly run days. Only dates that can still be asked for are used."""
    today = _today()
    earliest, latest = today, today + dt.timedelta(days=MAX_DAYS_AHEAD)
    before, after = [], []
    for delta in range(1, NEARBY_WINDOW_DAYS + 1):
        for sign, bucket, cap in ((-1, before, NEARBY_BEFORE), (1, after, NEARBY_AFTER)):
            day = wanted + dt.timedelta(days=sign * delta)
            if len(bucket) >= cap or not (earliest <= day <= latest):
                continue
            running = [t for t in trains if runs_on(t, day)]
            if running:
                bucket.append({
                    "date": day.isoformat(), "weekday": DAYS[day.weekday()], "days_from_requested": sign * delta,
                    "train_count": len(running), "trains": running,
                })
    # Nearest first; for the same distance the later date first (people more often want to travel a day later).
    return sorted(before + after, key=lambda d: (abs(d["days_from_requested"]), -d["days_from_requested"]))


# ---------------------------------------------------------------- the tool

def _stations_info(source: dict, destination: dict, args: SearchArgs) -> dict:
    """Which station was used for each city, so the agent can tell the user."""
    return {
        "from": {"searched_as": args.source_city, "code": source["code"], "name": source["name"]},
        "to": {"searched_as": args.destination_city, "code": destination["code"], "name": destination["name"]},
        "note": (
            f"Used {source['name']} ({source['code']}) for '{args.source_city}' and {destination['name']} ({destination['code']}) "
            f"for '{args.destination_city}'. The search covers all stations in each city, and each train shows its real stations. "
            "Tell the user which stations were used."
        ),
    }


def _failure(status: str, args: SearchArgs, which: str | None = None) -> dict:
    """The error object for a failed step."""
    if status == "quota":
        return _error("quota_exceeded", QUOTA_MESSAGE)
    if status == "not_found":
        city = {"source": args.source_city, "destination": args.destination_city}.get(which or "", "")
        shown = f" for '{city}'" if city else ""
        return _error("station_not_found", f"No station found{shown} in this data source. Check the spelling or try a nearby bigger city.")
    return _error("unavailable", UNAVAILABLE_MESSAGE)


async def search_trains(source_city: str, destination_city: str, date: str = "") -> dict:
    """Find the scheduled trains between two Indian cities, e.g. "Pune" and "Mumbai", optionally for a date (YYYY-MM-DD).

    Without a date it returns the trains that run every day and asks for the date; with a date it returns the trains
    running that day, or (when there are none) nearby dates from the weekly schedule. It says which station was used
    for each city, and returns the options when several stations match. Scheduled data only: no seats, fares or live
    status. Returns {"ok": False, "error": ..., "message": ...} when it cannot.
    """
    try:
        return await _search(source_city, destination_city, date)
    except Exception:  # noqa: BLE001 - a failed tool must never crash the agent loop
        logger.warning("train search failed unexpectedly")
        return _error("unavailable", UNAVAILABLE_MESSAGE)


async def _search(source_city, destination_city, date) -> dict:
    """The tool's work (search_trains wraps it so that nothing can escape as an exception)."""
    # 1. Validate the model-supplied arguments.
    try:
        args = SearchArgs(source_city=source_city, destination_city=destination_city, date=date)
    except ValidationError:
        return _error(
            "invalid_arguments",
            "Give two different city names (2 to 100 plain characters) and, optionally, a date as YYYY-MM-DD from today up to 120 days ahead.",
        )

    # 2. Needs an API key from the project-root .env.
    settings = get_settings()
    if not settings.railradar_api_key:
        return _error("not_configured", "The train service is not configured.")

    store = _Store(settings.redis_url)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            api = _Api(client, settings.railradar_api_key)

            # 3. The main station of each city: from the list, the cache or RailRadar's station search (both at once).
            (src_status, src), (dst_status, dst) = await asyncio.gather(
                _find_station(api, store, args.source_city), _find_station(api, store, args.destination_city)
            )
            # A real problem comes first (quota, a failed lookup, an unknown city); then a question to the user, source first.
            for status in (src_status, dst_status):
                if status in ("quota", "unavailable"):
                    return _failure(status, args)
            if src_status == "not_found":
                return _failure("not_found", args, "source")
            if dst_status == "not_found":
                return _failure("not_found", args, "destination")
            for status, picked, which, city in (
                (src_status, src, "source", args.source_city), (dst_status, dst, "destination", args.destination_city),
            ):
                if status == "ambiguous":
                    return {
                        "ok": True, "needs_choice": True, "which": which, "city": city,
                        "options": [{"code": o["code"], "name": o["name"]} for o in picked],
                        "message": (
                            f"Several stations match '{city}'. Ask the user which one they mean, then call again with that "
                            f"station's code as the {which} city."
                        ),
                    }
            if src["code"] == dst["code"]:
                return _error("invalid_arguments", "Both cities resolve to the same station. Give two different cities.")
            stations = _stations_info(src, dst, args)

            # 4. With a date: the trains running that day come first.
            if args.date:
                status, trains = await _between(api, store, src["code"], dst["code"], args.date)
                if status != "ok":
                    return _failure(status, args)
                if trains:
                    return {
                        "ok": True, "found": True, "searched_date": args.date, "needs_date": False, "stations": stations,
                        "train_count": len(trains), "trains": trains, "source": "railradar", "disclaimer": DISCLAIMER,
                        "message": f"{len(trains)} trains run on {args.date}. Tell the user which stations were used.",
                    }
                # No train that day. The nearby dates come from the run days of ALL trains on the route (the undated list):
                # it is read from the cache, or fetched if the 3-request budget still allows it.
                status, everything = await _between(api, store, src["code"], dst["code"], "")
                if status == "quota":
                    return _failure(status, args)
                result = {
                    "ok": True, "found": False, "no_trains_on_date": True, "searched_date": args.date, "needs_date": False,
                    "stations": stations, "train_count": 0, "trains": [], "source": "railradar", "disclaimer": DISCLAIMER,
                }
                if status == "ok" and everything:
                    nearby = _nearby_dates(everything, dt.date.fromisoformat(args.date))
                    result.update(nearby_dates=nearby, nearby_note=NEARBY_NOTE, nearby_checked=True)
                    result["message"] = (
                        f"No trains were found on {args.date} in this data source (this does not mean none run). "
                        + (f"The weekly schedule shows trains on nearby dates ({NEARBY_NOTE}) " if nearby else
                           f"The weekly schedule shows no trains within {NEARBY_WINDOW_DAYS} days either. ")
                        + "Offer the nearby dates, or ask for another date."
                    )
                else:
                    # Fully cold search: the budget is spent, so nearby dates could not be worked out.
                    result.update(nearby_dates=[], nearby_checked=False)
                    result["message"] = (
                        f"No trains were found on {args.date} in this data source (this does not mean none run). "
                        "Ask the user for another date."
                    )
                return result

            # 5. No date: the trains that run every day, in full, and a request for the date.
            status, trains = await _between(api, store, src["code"], dst["code"], "")
            if status != "ok":
                return _failure(status, args)
            if not trains:
                return {
                    "ok": True, "found": False, "searched_date": None, "needs_date": False, "stations": stations, "train_count": 0,
                    "trains": [], "source": "railradar", "disclaimer": DISCLAIMER,
                    "message": "No direct trains between these stations were found in this data source (this does not mean none run).",
                }
            daily = [t for t in trains if t["runs_daily"]]
            shown = daily or trains
            others = len(trains) - len(daily) if daily else 0
            return {
                "ok": True, "found": True, "searched_date": None, "needs_date": True, "stations": stations,
                "daily_trains_only": bool(daily), "train_count": len(shown), "other_trains_count": others, "trains": shown,
                "source": "railradar", "disclaimer": DISCLAIMER,
                "message": (
                    (f"These trains run every day. {others} more run only on some days. " if daily else
                     "No train runs every day on this route; these run on certain days (see run_days). ")
                    + "Ask the user for their travel date to check which trains run on it. Tell the user which stations were used."
                ),
            }
    finally:
        await store.close()
