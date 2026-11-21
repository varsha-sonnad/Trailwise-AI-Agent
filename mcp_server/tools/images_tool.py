# images_tool.py: the get_place_images tool. Takes a place name (plus an optional region) and returns up to 3 photos of it
# from Pexels, each with its photographer credit and links, for the app to show.
# Flow: validate -> Redis cache first -> on a miss, search Pexels for "<place> landmark", then "<place> temple", then
# "<place> landscape" (GET {PEXELS_BASE_URL}/search with query and per_page=15; the key goes ONLY in the Authorization
# header; timeout, one retry) -> keep only validated Pexels fields -> drop photos whose alt text mentions people, keep only
# photos whose alt mentions the place or a place-type word (temple, ghat, river, mountain, fort, lake, beach, palace) ->
# return up to 3 (fewer when not enough) -> cache a SUCCESSFUL result (found=true) for 24 hours.
# The searches stop as soon as 3 good photos are found. A failed call (timeout, 429, 5xx...) is never followed by another
# search: with nothing collected the tool answers "unavailable", with some photos already collected it returns those
# (not cached).
# Rules this file keeps: failures, partial results and found=false are never cached; after a 429 a one-minute marker in
# Redis (when Redis is set) makes later calls fail fast without asking Pexels; the key is never logged or returned; only
# Pexels web addresses are returned; third-party text (alt, photographer) is length-limited data; the tool never raises.
# TODO: the small Redis helper below (_Store) is a third copy of the idea in geocode_tool.py and routes_tool.py. Move all
# three tools onto one shared cache helper later (noted in claudeprompts.md).

import asyncio
import hashlib
import json
import logging
import re
import unicodedata
from urllib.parse import urlparse

import httpx
import redis.asyncio as redis
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# Photos returned: at most 3; the caller may want fewer.
MAX_IMAGES = 3
# Results asked for in every search (per_page). Many are dropped by the filters below, so the search asks for about 15.
SEARCH_RESULTS = 15
# The searches, in order: "<place> <suffix>". The tool stops as soon as it has 3 good photos.
QUERY_SUFFIXES = ("landmark", "temple", "landscape")
# Longest place or region text accepted.
MAX_TEXT_CHARS = 100
# A successful result is cached for exactly 24 hours (a fixed constant: no environment variable for it).
CACHE_TTL_SECONDS = 24 * 60 * 60
# Cache keys start with this; bump the "v2" whenever the way photos are chosen changes (v1 held photos picked by the old
# "<place> <region>" search, with people in them, so they must not be served any more).
CACHE_PREFIX = "images:v2:"
# After a 429 from Pexels this marker is set in Redis for one minute, and calls fail fast while it exists.
RATE_LIMIT_KEY = CACHE_PREFIX + "rate_limited"
RATE_LIMIT_BLOCK_SECONDS = 60
# Give up on one request after this many seconds.
HTTP_TIMEOUT_SECONDS = 10.0
# Redis is optional and must never slow a tool down: connect and reply timeouts, in seconds.
REDIS_TIMEOUT_SECONDS = 2.0
# One retry after one second, only for a timeout, a network error or a 5xx (a 429 is not retried: it sets the marker).
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 1.0
# Named in every result.
SOURCE = "Pexels"
# Field length limits for third-party text and addresses.
MAX_ALT_CHARS = 200
MAX_NAME_CHARS = 100
MAX_URL_CHARS = 500

# Words that mean a photo shows people (a person is not a picture of the place). A photo whose alt text has any of them
# is dropped. Whole words only, so "Manali" or "mandir" never match "man".
_PEOPLE_WORDS = re.compile(
    r"\b(?:man|men|woman|women|people|persons?|police|officers?|boys?|girls?|child|children|crowds?|portraits?"
    r"|tourists?|couples?|famil(?:y|ies))\b",
    re.IGNORECASE,
)
# Words that name the kind of place a good photo shows. A photo is kept when its alt text mentions the place itself OR one of these.
_PLACE_TYPE_WORDS = re.compile(r"\b(?:temples?|ghats?|rivers?|mountains?|forts?|lakes?|beach(?:es)?|palaces?)\b", re.IGNORECASE)
# Small words that are not worth matching when the place name has several words.
_NAME_STOP_WORDS = frozenset({"the", "and", "of", "in", "at", "near", "old", "new"})


async def _sleep(seconds: float) -> None:
    """Wait without blocking the event loop (a separate function so tests can skip the waiting)."""
    await asyncio.sleep(seconds)


def _clean_text(value: str, what: str, minimum: int) -> str:
    """Trim a place or region and allow only plausible text: letters, marks and digits in any script, and , . ' - ( ) / &.

    Marks are allowed because many scripts (Hindi, Kannada, ...) write vowels as combining marks.
    """
    value = value.strip()
    if not minimum <= len(value) <= MAX_TEXT_CHARS:
        raise ValueError(f"{what} has the wrong length")
    if not all(unicodedata.category(ch)[0] in "LMN" or ch in " ,.'-()/&" for ch in value):
        raise ValueError(f"{what} contains invalid characters")
    return value


class ImageArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    place: str
    region: str = ""
    count: int = MAX_IMAGES

    @field_validator("place")
    @classmethod
    def clean_place(cls, value: str) -> str:
        """The place name: 2 to 100 plausible characters."""
        return _clean_text(value, "place", 2)

    @field_validator("region")
    @classmethod
    def clean_region(cls, value: str) -> str:
        """The region (for example "Karnataka, India") may be empty, but not long or odd."""
        return _clean_text(value, "region", 0)

    @field_validator("count", mode="before")
    @classmethod
    def clean_count(cls, value):
        """A whole number from 1 to 3 (a bool or a string is refused)."""
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_IMAGES:
            raise ValueError("count must be 1 to 3")
        return value


def _normalise(text: str) -> str:
    """Lower-case the text and squeeze spaces, so "Hampi" and " hampi  " share one cache entry."""
    return " ".join(text.lower().split())


def _cache_key(place: str, region: str) -> str:
    """The Redis key for one place in one region: a hash, so keys stay short and safe."""
    text = f"{_normalise(place)}|{_normalise(region)}"
    return CACHE_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _unavailable() -> dict:
    """The "lookup failed" result. It says nothing about whether photos of the place exist."""
    return _error("unavailable", "Image lookup is unavailable right now.")


def _is_pexels_url(value) -> bool:
    """True only for a short https address on pexels.com or one of its subdomains (for example images.pexels.com)."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        return False
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return parsed.scheme == "https" and (host == "pexels.com" or host.endswith(".pexels.com"))


def _text(value, limit: int) -> str:
    """A string, trimmed and cut to the limit; anything that is not a string becomes an empty string."""
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _validate_photo(raw) -> dict | None:
    """Check one photo in OUR field names (url, thumb, alt, photographer, photographer_url, pexels_url) and tidy it.

    Returns None if it is not usable. Needed: a large and a medium picture and a Pexels page, all on Pexels' own hosts,
    and a photographer name (the credit). A photographer address that is not on pexels.com becomes an empty string.
    """
    if not isinstance(raw, dict):
        return None
    url, thumb, pexels_url = raw.get("url"), raw.get("thumb"), raw.get("pexels_url")
    photographer = _text(raw.get("photographer"), MAX_NAME_CHARS)
    if not (_is_pexels_url(url) and _is_pexels_url(thumb) and _is_pexels_url(pexels_url) and photographer):
        return None
    photographer_url = raw.get("photographer_url")
    return {
        "url": url,
        "thumb": thumb,
        "alt": _text(raw.get("alt"), MAX_ALT_CHARS),
        "photographer": photographer,
        "photographer_url": photographer_url if _is_pexels_url(photographer_url) else "",
        "pexels_url": pexels_url,
    }


def _from_pexels(item) -> dict | None:
    """One photo of a Pexels reply as our fields: src.large is url, src.medium is thumb, Pexels' own "url" is pexels_url."""
    if not isinstance(item, dict):
        return None
    src = item.get("src") if isinstance(item.get("src"), dict) else {}
    return _validate_photo({
        "url": src.get("large"),
        "thumb": src.get("medium"),
        "alt": item.get("alt"),
        "photographer": item.get("photographer"),
        "photographer_url": item.get("photographer_url"),
        "pexels_url": item.get("url"),
    })


def _clean_photos(raw, convert=_validate_photo, limit=MAX_IMAGES) -> list[dict]:
    """The usable photos of a list (a Pexels reply's "photos" with convert=_from_pexels, or a cached list), at most `limit`."""
    if not isinstance(raw, list):
        return []
    photos = (convert(item) for item in raw)
    return [p for p in photos if p is not None][:limit]


def _mentions_place(alt: str, place: str) -> bool:
    """True when the alt text names the place: the whole name, or (for a name of several words) any of its words."""
    alt, place = alt.lower(), " ".join(place.lower().split())
    if place and place in alt:
        return True
    words = [w for w in re.findall(r"\w+", place) if len(w) >= 3 and w not in _NAME_STOP_WORDS]
    return any(re.search(rf"\b{re.escape(w)}\b", alt) for w in words)


def _is_good_photo(photo: dict, place: str) -> bool:
    """The photo filter: no people words in the alt text, and the alt text mentions the place or a place-type word."""
    alt = photo.get("alt") or ""
    # The place's own name is taken out first, so a name such as "Man Singh Fort" does not count as "man".
    if _PEOPLE_WORDS.search(alt.lower().replace(" ".join(place.lower().split()), " ")):
        return False
    return _mentions_place(alt, place) or bool(_PLACE_TYPE_WORDS.search(alt))


def _good_photos(photos: list[dict], place: str) -> list[dict]:
    """The photos that pass the filter, in the order Pexels gave them."""
    return [p for p in photos if _is_good_photo(p, place)]


class _Store:
    """The Redis cache for one tool call. Redis is an optimisation, not a requirement. If REDIS_URL is not set,
    Redis is not used at all and nothing is logged. If it is set but cannot be reached, ONE short warning is logged
    per tool call (no address, no password), it is treated as a cache miss, and the tool carries on without it."""

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
            logger.warning("images: Redis unavailable, continuing without the cache")
            self.warned = True

    async def get(self, key: str) -> list[dict] | None:
        """The cached photos, or None on a miss, a Redis problem or a damaged entry."""
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
            photos = _clean_photos(json.loads(raw)["images"])
        except (ValueError, KeyError, TypeError):
            return None  # a damaged entry counts as a miss
        return photos or None

    async def set(self, key: str, photos: list[dict]) -> None:
        """Cache successful photos for CACHE_TTL_SECONDS (24 hours). A Redis problem is ignored."""
        if self.client is None:
            return
        try:
            await self.client.set(key, json.dumps({"images": photos}), ex=CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            self.client = None
            self._warn()

    async def blocked(self) -> bool:
        """True while the one-minute marker after a 429 exists. Without Redis (or on a Redis problem) it is False."""
        if self.client is None:
            return False
        try:
            return bool(await self.client.get(RATE_LIMIT_KEY))
        except Exception:  # noqa: BLE001
            self.client = None
            self._warn()
            return False

    async def block(self) -> None:
        """Set the one-minute marker after a 429, so the next calls fail fast. A Redis problem is ignored."""
        if self.client is None:
            return
        try:
            await self.client.set(RATE_LIMIT_KEY, "1", ex=RATE_LIMIT_BLOCK_SECONDS)
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


async def _search(http: httpx.AsyncClient, base_url: str, api_key: str, query: str):
    """One Pexels search, with one retry. Returns ("ok", photos), ("rate_limited", None) or ("unavailable", None).

    "ok" with an empty list means the search worked and nothing matched. The photos are all the usable results (up to
    SEARCH_RESULTS); the caller filters them. The key travels only in the Authorization
    header; only status codes are logged.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await http.get(
                f"{base_url}/search",
                params={"query": query, "per_page": SEARCH_RESULTS},
                headers={"Authorization": api_key},
            )
        except httpx.HTTPError:
            pass  # timeout or network error: retry below
        else:
            status = response.status_code
            if status == 429:
                logger.warning("pexels API returned status 429")
                return "rate_limited", None  # not retried: the caller sets the one-minute marker
            if status == 200:
                try:
                    photos = response.json()["photos"]
                except (ValueError, KeyError, TypeError):
                    return "unavailable", None  # not a Pexels-shaped reply
                if not isinstance(photos, list):
                    return "unavailable", None
                return "ok", _clean_photos(photos, _from_pexels, SEARCH_RESULTS)
            logger.warning("pexels API returned status %s", status)
            if status < 500:
                return "unavailable", None  # another 4xx (for example a bad key) will not get better by asking again
        if attempt < MAX_RETRIES:
            await _sleep(RETRY_DELAY_SECONDS)
    return "unavailable", None


async def get_place_images(place: str, region: str = "", count: int = MAX_IMAGES) -> dict:
    """Get up to 3 photos of a place from Pexels, each with its photographer credit and links.

    Returns {"ok": True, "found": True, "images": [{url, thumb, alt, photographer, photographer_url, pexels_url}],
    "source": "Pexels"}, or found=False with an empty list when nothing matches. With error "unavailable" the lookup
    failed: it says nothing about whether photos of the place exist.
    """
    try:
        return await _get_place_images(place, region, count)
    except Exception:  # noqa: BLE001 - a failed tool must never crash the agent loop
        logger.warning("images tool failed unexpectedly")
        return _unavailable()


async def _get_place_images(place, region, count) -> dict:
    """The tool's work (get_place_images wraps it so that nothing can escape as an exception)."""
    # 1. Validate the model-supplied arguments.
    try:
        args = ImageArgs(place=place, region=region, count=count)
    except ValidationError:
        return _error(
            "invalid_arguments",
            "place must be 2 to 100 plain characters, region at most 100, and count a whole number from 1 to 3.",
        )

    # 2. Needs the Pexels address and key from the project-root .env.
    settings = get_settings()
    base_url = settings.pexels_base_url.strip().rstrip("/")
    api_key = settings.pexels_api_key.strip()
    if not base_url.startswith(("http://", "https://")) or not api_key:
        return _error("not_configured", "Image lookup is not configured.")

    # 3. Cache first. Only successful results (found=true) are cached.
    key = _cache_key(args.place, args.region)
    store = _Store(settings.redis_url)
    try:
        photos = await store.get(key)
        if photos is None:
            # Right after a 429 fail fast, without asking Pexels again.
            if await store.blocked():
                return _unavailable()
            # "<place> landmark", then "<place> temple", then "<place> landscape"; stop as soon as 3 good photos are found.
            photos, seen, failed = [], set(), False
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
                for suffix in QUERY_SUFFIXES:
                    status, found = await _search(http, base_url, api_key, f"{args.place} {suffix}")
                    if status == "rate_limited":
                        await store.block()
                        failed = True
                        break
                    if status != "ok":
                        failed = True  # a failed call is never followed by another search
                        break
                    for photo in _good_photos(found, args.place):
                        if photo["url"] not in seen:
                            seen.add(photo["url"])
                            photos.append(photo)
                    if len(photos) >= MAX_IMAGES:
                        break
            photos = photos[:MAX_IMAGES]
            if failed:
                if not photos:
                    return _unavailable()
                # A search failed after earlier ones worked: return what was found, but never cache a partial result.
                return {"ok": True, "found": True, "images": photos[: args.count], "source": SOURCE}
            if not photos:
                return {"ok": True, "found": False, "images": [], "source": SOURCE}  # not cached
            await store.set(key, photos)
    finally:
        await store.close()

    return {"ok": True, "found": True, "images": photos[: args.count], "source": SOURCE}
