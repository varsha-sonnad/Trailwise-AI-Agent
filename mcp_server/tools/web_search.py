# web_search.py: the web search tool. Takes a query, asks Gemini to answer it using Google Search
# grounding, and returns a short answer plus its sources.
# Flow: validate the argument -> call Gemini -> return the result (no cache yet).
# It never raises into the agent loop: every failure becomes {"ok": False, "error": ...}.

import logging

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# Give up on Gemini after this many milliseconds.
REQUEST_TIMEOUT_MS = 20_000
# Caps so the tool never hands back huge text to the agent.
MAX_ANSWER_CHARS = 2000
MAX_SOURCES = 5

# Tells the grounded model to behave as a search helper, not as a chat assistant.
SEARCH_INSTRUCTION = (
    "Answer the query using Google Search. Reply with a short, factual summary (at most 150 words). "
    "Do not invent details; if the search finds nothing reliable, say so."
)

# Shared Gemini client, created on first use.
_genai_client = None


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
    """Return the shared Gemini client (created lazily). The key comes from settings, never code."""
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(
            api_key=get_settings().gemini_api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
    return _genai_client


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _shape(response) -> dict | None:
    """Keep only answer text, source titles/URLs and the searches run (structured data).

    Returns None when Gemini gave no usable answer.
    """
    answer = (response.text or "").strip()
    if not answer:
        return None

    candidates = getattr(response, "candidates", None) or []
    meta = getattr(candidates[0], "grounding_metadata", None) if candidates else None

    # Sources: de-duplicated by URL, capped, with every field length-limited.
    sources, seen = [], set()
    for chunk in (getattr(meta, "grounding_chunks", None) or []):
        web = getattr(chunk, "web", None)
        url = str(getattr(web, "uri", "") or "")[:500]
        if url and url not in seen:
            seen.add(url)
            sources.append({"title": str(getattr(web, "title", "") or "")[:200], "url": url})
        if len(sources) >= MAX_SOURCES:
            break

    queries = [str(q)[:200] for q in (getattr(meta, "web_search_queries", None) or [])][:5]
    return {
        "ok": True,
        "answer": answer[:MAX_ANSWER_CHARS],
        "sources": sources,
        "queries": queries,
        "source": "google_search_grounding",
    }


async def web_search(query: str) -> dict:
    """Search the web for current information, e.g. "events in Lisbon this month".

    Returns a short answer plus its sources (title and URL), or
    {"ok": False, "error": ..., "message": ...} when it cannot.
    """
    # 1. Validate the model-supplied argument.
    try:
        args = WebSearchArgs(query=query)
    except ValidationError:
        return _error("invalid_arguments", "query must be 3 to 200 characters of plain text.")

    # 2. Needs the Gemini API key from the project-root .env.
    if not get_settings().gemini_api_key:
        return _error("not_configured", "Web search is not configured.")

    # 3. Grounded Gemini call (only google_search, no function tools); every failure becomes an error.
    try:
        response = await _get_client().aio.models.generate_content(
            model=get_settings().gemini_search_model,
            contents=args.query,
            config=types.GenerateContentConfig(
                system_instruction=SEARCH_INSTRUCTION,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
    except Exception as exc:
        # Log only the error type: SDK messages can carry request details. Never the key.
        logger.warning("web_search Gemini call failed: %s", type(exc).__name__)
        return _error("unavailable", "Web search is unavailable right now.")

    try:
        result = _shape(response)
    except (AttributeError, TypeError, IndexError):
        result = None
    if result is None:
        return _error("no_results", "The search returned no usable answer.")

    # 4. Return the shaped result.
    return result
