# web_search.py: the web search tool. Takes a query, asks OpenAI to answer it with its built-in web search tool
# (Responses API), and returns a short answer plus its sources (titles and URLs).
# Flow: validate the argument -> Redis cache first -> on a miss, call OpenAI with the web_search tool -> keep only
# validated, length-capped fields -> cache a SUCCESSFUL result for 3 hours.
# Rules this file keeps: the answer and every source field are capped in length and cleaned of control characters, and
# only http(s) addresses are returned (the result is data for the agent, never instructions); an answer without any
# source is not returned (no unsupported facts); failures are never cached; a 429 from OpenAI becomes the error
# "quota_exceeded" (not retried); a timeout is retried once and then becomes the error "timeout" (not "unavailable"); the key
# is never logged or returned; the tool never raises into the agent loop:
# every failure becomes {"ok": False, "error": ...}.
# TODO: the small Redis helper below (_Store) is another copy of the idea in geocode_tool.py, routes_tool.py and
# images_tool.py. Move all of them onto one shared cache helper later (noted in claudeprompts.md).

import asyncio
import hashlib
import json
import logging
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import openai
import redis.asyncio as redis
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# One attempt waits for OpenAI at most OPENAI_SEARCH_TIMEOUT_SECONDS (default 120; a web search takes a while). A call that
# times out is retried ONCE. Both attempts together must finish inside the total budget (_total_budget: the agent's call limit
# for this tool, WEB_SEARCH_CALL_TIMEOUT_SECONDS, default 150, minus SAFETY_MARGIN_SECONDS), so the tool answers "timeout"
# itself before the agent gives up on it (backend/app/agent/mcp_client.py). The retry therefore gets only the time that is
# left, and none if less than MIN_RETRY_SECONDS is left.
SAFETY_MARGIN_SECONDS = 10.0
MIN_RETRY_SECONDS = 5.0
TIMEOUT_RETRIES = 1
# A hard stop slightly after the SDK's own timeout, in case the SDK's timeout does not fire.
HARD_STOP_GRACE_SECONDS = 1.0
# Caps so the tool never hands back huge text to the agent.
MAX_ANSWER_CHARS = 2000
MAX_SOURCES = 5
MAX_TITLE_CHARS = 200
MAX_URL_CHARS = 500
# Most tokens the search model may write (about 150 words plus room for the citations).
MAX_OUTPUT_TOKENS = 800

# A successful search is cached for exactly 3 hours (a fixed constant: no environment variable for it).
CACHE_TTL_SECONDS = 3 * 60 * 60
# Cache keys start with this; bump the "v1" if the cached value's shape ever changes.
CACHE_PREFIX = "web_search:v1:"
# Redis is optional and must never slow a tool down: connect and reply timeouts, in seconds.
REDIS_TIMEOUT_SECONDS = 2.0

# Named in every result.
SOURCE = "openai_web_search"

# Tells the model to behave as a search helper, not as a chat assistant.
SEARCH_INSTRUCTION = (
    "Answer the query using web search. Reply with a short, factual summary (at most 150 words), plain text, "
    "with no links or markdown in the text (the sources are returned separately). "
    "Do not invent details; if the search finds nothing reliable, say so."
)

# Longest piece of OpenAI's own error text that is logged.
MAX_REASON_CHARS = 200
# Things that must never reach the log, even inside an error message: web addresses and key-like tokens.
_URL = re.compile(r"https?://\S+")
_KEY_LIKE = re.compile(r"sk-[0-9A-Za-z_\-]{10,}|AIza[0-9A-Za-z_\-]{10,}|(?i:key|token|authorization)\s*[=:]\s*\S+|(?i:bearer)\s+\S+")
# Control characters (newlines and tabs included) have no place in a title or an address.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Shared OpenAI client, created on first use.
_client = None


class WebSearchArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    query: str

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        """Trim the text and allow only a plausible search query (3 to 200 characters)."""
        value = value.strip()
        if not 3 <= len(value) <= 200:
            raise ValueError("query must be 3 to 200 characters")
        # Blocks control characters (newlines, tabs, etc.) that have no place in a query.
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("query contains invalid characters")
        return value


def _get_client():
    """Return the shared OpenAI client (created lazily). The key comes from settings, never code. No SDK retries."""
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(
            api_key=get_settings().openai_api_key, timeout=_attempt_timeout(_total_budget()), max_retries=0
        )
    return _client


def _total_budget() -> float:
    """The time both attempts together may take: the agent's call limit for this tool minus the safety margin."""
    return max(1.0, float(get_settings().web_search_call_timeout_seconds) - SAFETY_MARGIN_SECONDS)


def _attempt_timeout(remaining: float) -> float:
    """How long one attempt may wait: the OPENAI_SEARCH_TIMEOUT_SECONDS setting, but never more than the time left."""
    return max(0.001, min(float(get_settings().openai_search_timeout_seconds), remaining))


def _failure_details(exc: Exception) -> tuple[int | None, str, str]:
    """(HTTP status code, error code, short detail) of a failed OpenAI call, safe to log.

    The status code and error code (for example 429 and insufficient_quota) come from OpenAI's error response. The
    detail is OpenAI's own error message, shortened, with web addresses, key-like text and the configured key removed.
    Headers and the request URL are never read. A non-API failure (timeout, network) has no status code: only its type
    is reported.
    """
    if not isinstance(exc, openai.APIStatusError):
        return None, type(exc).__name__, ""
    code = exc.status_code if isinstance(exc.status_code, int) else None
    reason = str(getattr(exc, "code", None) or "unknown")[:60]
    detail = str(getattr(exc, "message", "") or "")
    key = get_settings().openai_api_key
    if key:
        detail = detail.replace(key, "[redacted]")
    detail = _KEY_LIKE.sub("[redacted]", _URL.sub("[url]", detail))
    return code, reason, " ".join(detail.split())[:MAX_REASON_CHARS]


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _text(value, limit: int) -> str:
    """A string without control characters, spaces squeezed and cut to the limit; anything else becomes ''."""
    return " ".join(_CONTROL.sub(" ", value).split())[:limit] if isinstance(value, str) else ""


def _clean_url(value) -> str:
    """A short http(s) address without control characters or spaces, and without OpenAI's utm_source=openai marker.

    Anything else (javascript:, data:, a relative path, an over-long value) becomes an empty string.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS or _CONTROL.search(value) or " " in value:
        return ""
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not (k == "utm_source" and v == "openai")]
        return urlunparse(parsed._replace(query=urlencode(query)))
    except ValueError:
        return ""


def _clean_sources(raw) -> list[dict]:
    """The usable sources of a list of {title, url}: valid address, de-duplicated by address, capped, fields length-limited."""
    sources, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        url = _clean_url(item.get("url")) if isinstance(item, dict) else ""
        if url and url not in seen:
            seen.add(url)
            sources.append({"title": _text(item.get("title"), MAX_TITLE_CHARS), "url": url})
        if len(sources) >= MAX_SOURCES:
            break
    return sources


def _shape(response) -> dict | None:
    """Keep only the answer text and the url_citation sources (structured data, never the raw response).

    Returns None when OpenAI gave no usable answer, or an answer with no source to back it.
    """
    answer = _text(getattr(response, "output_text", None) or "", MAX_ANSWER_CHARS)
    if not answer:
        return None

    # The sources are the url_citation annotations on the message text.
    citations = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            for note in getattr(part, "annotations", None) or []:
                if getattr(note, "type", None) == "url_citation":
                    citations.append({"title": getattr(note, "title", ""), "url": getattr(note, "url", "")})
    sources = _clean_sources(citations)
    if not sources:
        return None  # no source, no facts: an answer nobody can check is not returned
    return {"ok": True, "answer": answer, "sources": sources, "source": SOURCE}


def _cache_key(query: str, model: str) -> str:
    """The Redis key for one query on one search model: a hash, so keys stay short and safe."""
    text = f"{model}|{' '.join(query.lower().split())}"
    return CACHE_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


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
            logger.warning("web_search: Redis unavailable, continuing without the cache")
            self.warned = True

    async def get(self, key: str) -> dict | None:
        """The cached result, or None on a miss, a Redis problem or a damaged entry (re-checked like a fresh result)."""
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
            answer = _text(data["answer"], MAX_ANSWER_CHARS)
            sources = _clean_sources(data["sources"])
        except (ValueError, KeyError, TypeError):
            return None  # a damaged entry counts as a miss
        if not answer or not sources:
            return None
        return {"ok": True, "answer": answer, "sources": sources, "source": SOURCE}

    async def set(self, key: str, result: dict) -> None:
        """Cache a successful result for CACHE_TTL_SECONDS (3 hours). A Redis problem is ignored."""
        if self.client is None:
            return
        try:
            await self.client.set(key, json.dumps(result, ensure_ascii=False), ex=CACHE_TTL_SECONDS)
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


async def web_search(query: str) -> dict:
    """Search the web for current information, e.g. "events in Lisbon this month".

    Returns a short answer plus its sources (title and URL), or
    {"ok": False, "error": ..., "message": ...} when it cannot ("quota_exceeded" after a 429).
    """
    try:
        return await _web_search(query)
    except Exception:  # noqa: BLE001 - a failed tool must never crash the agent loop
        logger.warning("web_search failed unexpectedly")
        return _error("unavailable", "Web search is unavailable right now.")


async def _web_search(query: str) -> dict:
    """The tool's work (web_search wraps it so that nothing can escape as an exception)."""
    # 1. Validate the model-supplied argument.
    try:
        args = WebSearchArgs(query=query)
    except ValidationError:
        return _error("invalid_arguments", "query must be 3 to 200 characters of plain text.")

    # 2. Needs the OpenAI key and a search model (no default model name) from the project-root .env.
    settings = get_settings()
    model = settings.openai_search_model.strip()
    if not settings.openai_api_key or not model:
        return _error("not_configured", "Web search is not configured.")

    # 3. Cache first (Redis is optional): a hit costs no OpenAI call.
    store = _Store(settings.redis_url)
    try:
        key = _cache_key(args.query, model)
        cached = await store.get(key)
        if cached is not None:
            return cached

        # 4. The OpenAI call: only the built-in web_search tool, nothing stored at OpenAI, no SDK retries. A timeout is
        #    retried once (within the total time budget); the answer after that is the error "timeout".
        started = time.perf_counter()
        budget = _total_budget()  # read once, so a retry cannot stretch it
        attempt = 0
        while True:
            attempt += 1
            timeout = _attempt_timeout(budget - (time.perf_counter() - started))
            attempt_started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    _get_client().responses.create(
                        model=model,
                        instructions=SEARCH_INSTRUCTION,
                        input=args.query,
                        tools=[{"type": "web_search"}],
                        tool_choice="required",  # a search must really happen: no answers from the model's memory
                        store=False,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                        timeout=timeout,
                    ),
                    timeout=timeout + HARD_STOP_GRACE_SECONDS,
                )
                break
            except (openai.APITimeoutError, asyncio.TimeoutError, TimeoutError):
                # Log only the model, the attempt number, the limit and the durations: no key, query text or URL.
                total_ms = round((time.perf_counter() - started) * 1000)
                logger.warning(
                    "web_search timed out: model=%s attempt=%d timeout_s=%.1f attempt_ms=%d total_ms=%d",
                    model, attempt, timeout, round((time.perf_counter() - attempt_started) * 1000), total_ms,
                )
                time_left = budget - (time.perf_counter() - started)
                if attempt <= TIMEOUT_RETRIES and time_left >= MIN_RETRY_SECONDS:
                    continue  # the one retry (it only gets the time that is left)
                logger.warning("web_search gave up after a timeout: attempts=%d total_ms=%d", attempt, total_ms)
                return _error("timeout", "The web search took too long. Please try again.")
            except Exception as exc:
                # Log the HTTP status, OpenAI's error code and the model, to see why it failed. Never the key, headers or URL.
                status, reason, detail = _failure_details(exc)
                logger.warning(
                    "web_search OpenAI call failed: model=%s http_status=%s reason=%s error_type=%s duration_ms=%d detail=%s",
                    model, status, reason, type(exc).__name__, round((time.perf_counter() - started) * 1000), detail,
                )
                if status == 429:
                    # Rate limit or used-up quota: not retried and not cached; the agent is told in one clear word.
                    return _error("quota_exceeded", "Web search is over its quota or rate limit right now.")
                return _error("unavailable", "Web search is unavailable right now.")

        try:
            result = _shape(response)
        except (AttributeError, TypeError, IndexError):
            result = None
        if result is None:
            return _error("no_results", "The search returned no usable answer.")

        # 5. Cache the success (failures and empty results never are) and return it.
        await store.set(key, result)
        return result
    finally:
        await store.close()
