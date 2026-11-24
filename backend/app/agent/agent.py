# agent.py: the Trailwise agent. Builds the system prompt, discovers the MCP tools, sends the user's
# message to OpenAI (Responses API) and runs a capped tool loop: when the model asks for a tool, the agent calls it
# through the MCP client and gives the result back, until the model writes the final reply.
# Every step is also written to the audit log (app/core/audit.py): request, model decisions (with mode,
# prompt hash and cached tokens), tool calls, status, timing and errors, never message text, tool arguments or keys.
# Each turn picks a mode (agent/modes.py) and sends system.md + that mode's prompt. The conversation memory (earlier messages,
# last mode, last place) arrives with each request from the browser and is never stored here.
# Explore mode must attempt web_search, get_weather, geocode_places and get_place_images before its final answer is accepted
# (one extra forced round if some were skipped); see the required-tools check in _converse.
# The request and reply shapes come from agent/openai_adapter.py; nothing is stored at OpenAI (store=False).

import asyncio
import contextvars
import json
import logging
import time
from functools import lru_cache

import openai

from app.agent import openai_adapter as oa
from app.agent.legs import CITY_TOOLS, GeoState, build_legs, check_city_pair, check_route_points, is_gateway_signal
from app.agent.mcp_client import McpClient, McpClientError
from app.agent.modes import (
    EXCLUDED_TOOLS, FORCED_TOOLS, MAP_MIN_PLACES, MAP_TOOL, MODES, BuiltPrompt, build_system_prompt, is_clarifying_question, is_send_confirmation, is_small_talk, preferences_line,
    has_two_places, required_tools, select_mode_with_reason,
)
from app.config import get_settings
from app.core.audit import audit_event, message_fingerprint, new_request_id, openai_request_id, usage_fields
from app.core.debug_log import log_answer, log_question
from app.models.chat import clean_images, clean_legs, clean_places

logger = logging.getLogger(__name__)

# Pauses (seconds) before each retry of a call that failed with a temporary server error.
# Two retries at most, so a busy model costs a few seconds, not a failed chat.
RETRY_DELAYS_SECONDS = (2, 5)
# HTTP codes worth retrying: 500 server error, 502 bad gateway, 503 overloaded, 504 gateway timeout.
# Other client errors (401 bad key, 400 bad request) are never retried; a 429 has its own rule below.
RETRYABLE_STATUS_CODES = oa.RETRYABLE_STATUS_CODES
# The code OpenAI uses for "model overloaded".
OVERLOADED_STATUS_CODE = 503
# A "slow down" 429 (rate_limited) is retried this many times (once), after the last pause in RETRY_DELAYS_SECONDS.
# A 429 that says the quota is used up (insufficient_quota) is never retried.
RATE_LIMIT_RETRIES = 1

# Most model rounds per request. Stops a model that keeps asking for tools from looping forever.
MAX_TOOL_ROUNDS = 6

# What the user sees when every tool failed on a forced round: no facts from the model's memory.
NO_LIVE_INFO_REPLY = "I could not check live information right now, please try again."
# What the user sees when web_search answers "not_configured" (no search model or key on the tool server): retrying will
# not help, so the reply says it is a setup problem. The audit log and the app log say which setting is missing.
SEARCH_NOT_SET_UP_REPLY = (
    "Live search is not set up on this server, so I cannot check live information. "
    "Please ask the server administrator to set it up."
)
# The error code a tool returns when its settings (key, model, address) are missing.
NOT_CONFIGURED = "not_configured"
# What the user sees when web_search timed out (after its one retry): it took too long, and trying again is reasonable.
SEARCH_TIMEOUT_REPLY = "The live search took too long. Please try again."
# The error code the search tool (or the MCP client's call limit) returns after a timeout.
TIMEOUT = "timeout"
# What the user sees when the whole chat request used up its overall time cap (CHAT_REQUEST_TIMEOUT_SECONDS).
REQUEST_TOO_LONG_REPLY = "Sorry, that took too long. Please try again, or ask in a simpler way."
# For a mode with required tools (explore, itinerary, route) round 1 is NOT forced by default: the model may ask a short
# clarifying question first (nothing to look up yet), and the required-tools check below makes sure the tools are used
# before any other answer is accepted. Set True to force a required tool on round 1 as well (the model then cannot ask first).
FORCE_FIRST_ROUND_WHEN_REQUIRED = False
# For a mode that requires web_search (explore, itinerary, route), the FIRST model call is forced to be web_search (and only
# web_search): the model cannot write anything from its own knowledge before the search has run, and the answer is built
# from the search results and the other tools. This takes precedence over FORCE_FIRST_ROUND_WHEN_REQUIRED. Greetings, short
# confirmations, email mode and a server without web_search are not forced. Set False to go back to an automatic round 1.
FORCE_WEB_SEARCH_FIRST = True

# Plain names for the note added to an answer when one of these tools could not be checked (explore mode).
TOOL_NOTE_LABELS = {"get_weather": "the weather", "geocode_places": "the map locations", "get_place_images": "the photos"}

# The fallback model this request has switched to ("" = still on the main model). Once a request
# switches, its later tool rounds stay on the fallback so they do not wait through the main model's
# failures again. A ContextVar is per-request state, not stored user data (rule 1).
_active_model: contextvars.ContextVar[str] = contextvars.ContextVar("agent_active_model", default="")


class AgentError(Exception):
    """Raised when the agent cannot produce a reply (the model call failed or returned nothing)."""

    def __init__(self, message: str, status_code: int | None = None, kind: str | None = None) -> None:
        super().__init__(message)
        # The HTTP status the AI service answered with, if it answered (kept for the audit log, never shown to users).
        self.status_code = status_code
        # The error kind from openai_adapter.map_error (for example "quota" or "rate_limited"), for the audit log.
        self.kind = kind


class AgentReply(str):
    """The reply text, which also carries the photos the app shows under it (a str, so existing code keeps working).

    `images` holds the photos from successful get_place_images calls of this request (validated, Pexels only, at most
    6), as plain dicts. The model never lists, describes or credits them in its text: the app shows them separately.
    `places` holds the places geocode_places found in this request ({name, lat, lng}), and `mode` the mode used: the
    browser sends them back as last_place and last_mode with the next message (nothing is stored here).
    """

    images: list
    places: list
    legs: list
    mode: str | None

    def __new__(
        cls, text: str, images: list | None = None, places: list | None = None, mode: str | None = None,
        legs: list | None = None,
    ):
        reply = super().__new__(cls, text)
        reply.images = clean_images(images or [])
        reply.places = clean_places(places or [])
        reply.legs = clean_legs(legs or [])  # the map lines: get_route roads and, in the gateway flow, dashed connections
        reply.mode = mode if mode in MODES else None
        return reply


class AgentBusyError(AgentError):
    """Raised when the AI service is overloaded (HTTP 503) even after retries; the route answers 503, not 502."""


class AgentUnavailableError(AgentError):
    """Raised when the AI service cannot be used at all right now (for example its quota is used up); retrying will not help."""


def _ms(start: float) -> int:
    """Milliseconds elapsed since a time.perf_counter() reading."""
    return round((time.perf_counter() - start) * 1000)


def _cause_name(exc: Exception) -> str:
    """Name of the underlying error type (never its message, which could carry request details)."""
    return type(exc.__cause__ or exc).__name__


def _tool_choice(mode: str, message: str, tool_names: list[str], round_no: int) -> tuple[dict | None, str]:
    """Pick the tool_choice for one call. Returns (tool_choice or None, label for the audit log).

    Only explore forces web_search as its first call (FORCE_WEB_SEARCH_FIRST); route forces geocode_places, search_trains and
    search_buses on round 1 when the message names both cities (has_two_places); a missing city is left for the model to ask.
    Otherwise, round 1 only: "required", limited (allowed_tools) to this mode's tools that the MCP server really offered, so
    the model must call one of them. Never forced for email mode, for a greeting or confirmation, or when none of
    the mode's tools exist. For a mode with required tools it is not forced either (see FORCE_FIRST_ROUND_WHEN_REQUIRED):
    the required-tools check in _converse forces the missing ones later. Later rounds are "auto" (no tool_choice sent)
    so the model can write the final reply. No tools at all: no tool_choice is sent (the API rejects one without tools).
    """
    if not tool_names:
        return None, "none"
    if round_no == 1 and FORCE_WEB_SEARCH_FIRST and mode == "explore" and "web_search" in required_tools(mode, message, tool_names):
        # The first call must be the search, and nothing else (allowed_tools holds only web_search).
        choice = oa.build_tool_choice(["web_search"], available_names=tool_names, mode="required")
        if choice is not None:
            return choice, "required"
    if round_no == 1 and mode == "route" and has_two_places(message) and not is_small_talk(message):
        # Route mode with both cities in the message: the first call must be the map lookup and the two transport searches.
        choice = oa.build_tool_choice(FORCED_TOOLS["route"], available_names=tool_names, mode="required")
        if choice is not None:
            return choice, "required"
    if round_no == 1 and required_tools(mode, message, tool_names) and not FORCE_FIRST_ROUND_WHEN_REQUIRED:
        return None, "auto"
    if round_no == 1 and not is_small_talk(message):
        choice = oa.build_tool_choice(FORCED_TOOLS.get(mode, ()), available_names=tool_names, mode="required")
        if choice is not None:
            return choice, "required"
    return None, "auto"


@lru_cache
def get_client() -> openai.AsyncOpenAI:
    """Build the OpenAI client once. The key and timeout come from settings. max_retries=0: our own loop retries."""
    settings = get_settings()
    return openai.AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)


def to_tools(tools: list[dict]) -> list[dict]:
    """Turn the MCP tool list (name, description, input_schema) into OpenAI function tool definitions."""
    return [oa.function_tool(t["name"], t["description"], t["input_schema"]) for t in tools]


def _agent_error(info: oa.ErrorInfo) -> AgentError:
    """Build the error the route understands from a classified failure (no SDK details are kept in the message)."""
    if info.kind == "busy" and info.status_code == OVERLOADED_STATUS_CODE:
        return AgentBusyError("OpenAI request failed", status_code=info.status_code, kind=info.kind)
    if info.kind == "quota":
        return AgentUnavailableError("OpenAI quota used up", status_code=info.status_code, kind=info.kind)
    return AgentError("OpenAI request failed", status_code=info.status_code, kind=info.kind)


async def _call_model(model: str, request: dict):
    """One async call to one model through the Responses API. Wraps SDK errors.

    A temporary server error (500, 502, 503, 504) is retried after a short pause, at most
    len(RETRY_DELAYS_SECONDS) times. A rate_limited 429 is retried once. insufficient_quota, other client errors
    and timeouts fail at once.
    """
    server_retries = 0
    rate_retries = 0
    while True:
        try:
            # The async client keeps the server non-blocking (project rule 2).
            return await get_client().responses.create(model=model, **request)
        except Exception as exc:
            info = oa.map_error(exc)
            delay = None
            if info.retryable and server_retries < len(RETRY_DELAYS_SECONDS):
                delay = RETRY_DELAYS_SECONDS[server_retries]
                server_retries += 1
            elif info.kind == "rate_limited" and rate_retries < RATE_LIMIT_RETRIES:
                delay = RETRY_DELAYS_SECONDS[-1]
                rate_retries += 1
            if delay is not None:
                # Temporary failure with retries left: note it (model, status code, kind and delay only), wait, try again.
                audit_event(
                    "model_retry", "retry", model=model, attempt=server_retries + rate_retries,
                    http_status=info.status_code, error_kind=info.kind, delay_s=delay, openai_request_id=openai_request_id(exc),
                )
                await asyncio.sleep(delay)
                continue
            # Out of retries or not retryable. Hide SDK details from callers; keep the original error attached for logs.
            raise _agent_error(info) from exc


async def _generate(input_items: list[dict], tools: list[dict], system_prompt: str, tool_choice: dict | None = None):
    """One async model call with the system prompt (system.md + the mode file) and (if any) the tools.

    Tries the main model (with retries). If it is still failing with a server error (500, 502, 503, 504)
    and a different OPENAI_FALLBACK_MODEL is set, switches to that model for the rest of the request.
    Client errors (bad key, quota, rate limit, bad request) and timeouts never trigger the fallback.
    """
    request: dict = {
        "instructions": system_prompt,
        "input": input_items,
        # Nothing is stored at OpenAI: the whole conversation is sent each round.
        "store": False,
    }
    # No tools means neither tools nor tool_choice (see _tool_choice); a forced round 1 sends the allowed_tools choice.
    if tools:
        request["tools"] = tools
    if tool_choice is not None:
        request["tool_choice"] = tool_choice
    settings = get_settings()
    primary = settings.openai_model.strip()
    fallback = settings.openai_fallback_model.strip()
    if not primary:
        # No hardcoded model names: the model must come from OPENAI_MODEL.
        raise AgentError("OPENAI_MODEL is not set", kind="no_model")

    # This request already switched to the fallback in an earlier round: stay on it.
    if _active_model.get():
        return await _call_model(_active_model.get(), request)

    try:
        return await _call_model(primary, request)
    except AgentError as exc:
        # Only a server-side failure with a usable, different fallback model leads to a switch.
        if not fallback or fallback == primary or exc.status_code not in RETRYABLE_STATUS_CODES:
            raise
        audit_event("model_fallback", "fallback", http_status=exc.status_code, from_model=primary, to_model=fallback)
        _active_model.set(fallback)
        return await _call_model(fallback, request)


def _function_calls(response) -> list:
    """The function calls in a response's output (other output items, such as text messages, are skipped)."""
    return [item for item in (getattr(response, "output", None) or []) if getattr(item, "type", None) == "function_call"]


def _labels(names: list[str]) -> str:
    """Plain names joined for a sentence, in TOOL_NOTE_LABELS order: "the weather", "a and b", "a, b and c"."""
    labels = [TOOL_NOTE_LABELS[n] for n in TOOL_NOTE_LABELS if n in names]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _unchecked_note(names: list[str], not_set_up: list[str] | tuple[str, ...] = ()) -> str:
    """The fixed note added to an answer when some parts could not be checked.

    Parts that failed for now: "Note: I could not check the weather and the photos right now."
    Parts whose tool is not configured (retrying will not help): "Note: I could not check the photos: that is not set
    up on this server." When both happen the two sentences follow each other.
    """
    sentences = []
    temporary = [n for n in names if n not in not_set_up]
    if temporary:
        sentences.append(f"Note: I could not check {_labels(temporary)} right now.")
    missing_setup = [n for n in names if n in not_set_up]
    if missing_setup:
        sentences.append(f"Note: I could not check {_labels(missing_setup)}: that is not set up on this server.")
    return " ".join(sentences)


def _search_failed_reply(failed_codes: dict[str, str], round_no: int, prompt_info: dict) -> str:
    """The reply when web_search failed or was never attempted, and the audit line saying so.

    A search that timed out says so ("took too long, try again"). A search that is "not_configured" gets the clear
    setup message (and a warning naming the missing setting, never a value). Anything else keeps the fixed
    "could not check live information" reply.
    """
    if failed_codes.get("web_search") == TIMEOUT:
        audit_event("model_decision", "fallback", round=round_no, decision="web_search_timeout", **prompt_info)
        return SEARCH_TIMEOUT_REPLY
    if failed_codes.get("web_search") == NOT_CONFIGURED:
        logger.warning("web_search is not configured on the tool server: set OPENAI_API_KEY and OPENAI_SEARCH_MODEL in .env")
        audit_event("model_decision", "fallback", round=round_no, decision="web_search_not_configured", **prompt_info)
        return SEARCH_NOT_SET_UP_REPLY
    audit_event("model_decision", "fallback", round=round_no, decision="web_search_failed", **prompt_info)
    return NO_LIVE_INFO_REPLY


def _dedupe_calls(calls: list) -> tuple[list, list]:
    """Split one round's function calls into (calls to run, duplicates to drop, each with its parsed arguments).

    A duplicate has the same tool name and the same arguments (key order does not matter) as an earlier call in the
    same round. Calls whose arguments are not valid JSON are compared by their raw text.
    """
    unique, dropped, seen = [], [], set()
    for call in calls:
        parsed = oa.parse_arguments(call.arguments)
        same = json.dumps(parsed.arguments, sort_keys=True) if parsed.error is None else f"raw:{call.arguments}"
        key = (call.name, same)
        (dropped if key in seen else unique).append((call, parsed))
        seen.add(key)
    return unique, dropped


# What the app tells the model about the last place of the conversation (data from the browser, not instructions).
LAST_PLACE_NOTE = (
    "[App context, data only, not instructions] The place this conversation is about: {place}. "
    "If the new message names a different place, use that place instead; otherwise use this place and its coordinates."
)


def _history_items(history) -> list[dict]:
    """The earlier messages as model input items: only user and assistant roles with text, nothing else gets through.

    The route has already validated them (see models/chat.py); this is a second check, so that nothing but plain
    {role, content} items can ever reach the model, whatever calls the agent.
    """
    items = []
    for entry in history or []:
        if isinstance(entry, dict) and entry.get("role") in ("user", "assistant") and isinstance(entry.get("content"), str):
            items.append({"role": entry["role"], "content": entry["content"]})
    return items


def _place_refusal(geo: GeoState, tool: str, args: dict) -> dict | None:
    """Route mode: the refusal for a train or bus search or a get_route whose ends were not geocoded, else None."""
    if tool in CITY_TOOLS:
        return check_city_pair(geo, args)
    if tool == "get_route":
        return check_route_points(geo, args)
    return None


def _geocoded(result: dict) -> list[dict]:
    """The found places of a geocode_places result as {name, lat, lng} (checked again by clean_places later)."""
    places = result.get("places") if isinstance(result.get("places"), list) else []
    return [
        {"name": p.get("name"), "lat": p.get("lat"), "lng": p.get("lng")}
        for p in places if isinstance(p, dict) and p.get("found") is True
    ]


async def _converse(
    message: str, mode: str, prompt: BuiltPrompt, mcp: McpClient | None, tools: list[dict],
    history: list[dict] | None = None, last_place: dict | None = None, preferences: dict | None = None,
) -> str:
    """Run the model <-> tool loop for one user message and return the final reply text."""
    tools = [t for t in tools if t["name"] not in EXCLUDED_TOOLS.get(mode, ())]  # e.g. route mode never sees web_search or weather
    tool_defs = to_tools(tools)
    # Logged with every model decision: which mode and exact prompt version produced the step.
    # The instructions: the fixed system prompt first, then (only when preferences are set) one short validated line.
    # The tools and the fixed text stay the identical prefix, so prompt caching still works; the hash below is of the fixed text.
    line = preferences_line(preferences)
    instructions = prompt.text + "\n" + line + "\n" if line else prompt.text
    prompt_info = {"mode": mode, "prompt_hash": prompt.hash, "prompt_versions": list(prompt.versions)}
    tools_called: list[str] = []  # tool names the model asked for so far in this request (names only)
    # Only tool names the server really offered may be called (the model's names are not trusted).
    allowed = {t["name"] for t in tools}
    # The conversation sent every round (nothing is stored at OpenAI, so we resend it).
    # Per-session memory (nothing is stored here): the earlier messages first, then what the app knows about the last place,
    # then the new message. The system prompt and the tools (sent separately, ahead of the input) stay the stable prefix.
    input_items: list[dict] = _history_items(history)
    if last_place:
        place_json = json.dumps({"name": last_place["name"], "lat": last_place["lat"], "lng": last_place["lng"]}, ensure_ascii=False)
        input_items.append({"role": "user", "content": LAST_PLACE_NOTE.format(place=place_json)})
    input_items.append({"role": "user", "content": message})
    photos: list[dict] = []  # photos from successful get_place_images calls, shown by the app under the final reply
    geocoded: list[dict] = []  # places from successful geocode_places calls, sent back so the browser can remember the place
    # The tools this turn must attempt before its final answer (explore: all four, those offered; none for a greeting).
    required = required_tools(mode, message, allowed)
    attempted: set[str] = set()  # tools the model has tried so far (whether or not they returned data)
    failed: set[str] = set()  # tools whose latest attempt did not return data
    failed_codes: dict[str, str] = {}  # the error code of each failed tool's latest attempt (for example "not_configured")
    extra_choice: dict | None = None  # the forced tool_choice of the one extra round, when it is scheduled
    extra_done = False
    geo = GeoState()  # what geocode_places found (route mode checks every train, bus and road search against it)
    roads: list[dict] = []  # successful get_route results: {inputs, points}, drawn as solid road legs
    gateway_flow = False  # route mode: the destination has no station or bus route, so the gateway-city flow is used
    map_extra_done = False  # route mode: the separate one-time extra round that forces get_route after the places are known

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        started = time.perf_counter()
        if extra_choice is not None:
            tool_choice, choice_label, extra_choice = extra_choice, "required", None
        else:
            tool_choice, choice_label = _tool_choice(mode, message, sorted(allowed), round_no)
        forced = round_no == 1 and choice_label == "required"
        try:
            response = await _generate(input_items, tool_defs, instructions, tool_choice)
        except AgentError as exc:
            audit_event(
                "model_decision", "error", round=round_no, error_type=_cause_name(exc),
                http_status=exc.status_code, error_kind=exc.kind, duration_ms=_ms(started), tool_choice=choice_label,
                tools_called=list(tools_called), openai_request_id=openai_request_id(exc.__cause__), **prompt_info,
            )
            raise
        calls = _function_calls(response)
        # OpenAI's request id and the token counts (input, output, total and cached) for the audit log.
        usage = usage_fields(response)
        if not calls:
            # No tool requested: this is the final answer. The text is empty for e.g. a blocked reply.
            text = getattr(response, "output_text", None)
            if not text:
                audit_event(
                    "model_decision", "error", round=round_no, error_type="empty_reply",
                    duration_ms=_ms(started), tool_choice=choice_label,
                    tools_called=list(tools_called), **usage, **prompt_info,
                )
                raise AgentError("OpenAI returned an empty reply", kind="empty_reply")

            # Guard: a short clarifying question as the first reply, before any tool other than the forced search was used, is
            # not checked (the user has to answer first). A known last place counts as a named place: the model has nothing
            # to ask, so the tools must run.
            if required and not extra_done and not (attempted - {"web_search"}) and not last_place and is_clarifying_question(text):
                audit_event(
                    "model_decision", round=round_no, decision="guard_skipped", reason="clarifying_question",
                    duration_ms=_ms(started), tool_choice=choice_label, tools_called=[], **usage, **prompt_info,
                )
                return text

            # Required-tools check: the answer is only accepted once every required tool was attempted. If some were
            # not, run ONE extra round that forces the missing ones (the draft answer is dropped, not kept).
            # get_route (route mode) is only required once geocode_places found two places; it needs their coordinates, so
            # it can be forced in a later extra round of its own (map_extra_done) after the other tools were forced.
            geocoded_distinct = {(p["lat"], p["lng"]) for p in geocoded}
            map_ready = len(geocoded_distinct) >= MAP_MIN_PLACES
            missing = [name for name in required if name not in attempted and (name != MAP_TOOL or map_ready)]
            map_only = missing == [MAP_TOOL]
            if missing and not (map_extra_done if map_only else extra_done) and round_no + 2 <= MAX_TOOL_ROUNDS:  # room for the extra round and a final one
                choice = oa.build_tool_choice(missing, available_names=allowed, mode="required")
                if choice is not None:
                    audit_event(
                        "model_decision", "retry", round=round_no, decision="force_missing_tools", missing=missing,
                        duration_ms=_ms(started), tool_choice=choice_label, tools_called=list(tools_called),
                        **usage, **prompt_info,
                    )
                    extra_choice = choice
                    if map_only:
                        map_extra_done = True
                    else:
                        extra_done = True
                    continue

            # Final decision. Without web_search there are no checkable facts: the fixed reply, never memory.
            if "web_search" in required and ("web_search" in failed or "web_search" not in attempted):
                return AgentReply(_search_failed_reply(failed_codes, round_no, prompt_info), places=geocoded)
            # Weather, map locations or photos that failed (or were never tried) are left out and named in a note.
            unchecked = [n for n in required if n in TOOL_NOTE_LABELS and (n in failed or n not in attempted)]
            audit_event(
                "model_decision", round=round_no, decision="final_answer",
                duration_ms=_ms(started), tool_choice=choice_label, unchecked_tools=unchecked,
                image_count=len(clean_images(photos)), tools_called=list(tools_called), **usage, **prompt_info,
            )
            not_set_up = [n for n in unchecked if failed_codes.get(n) == NOT_CONFIGURED]
            for name in not_set_up:
                logger.warning("%s is not configured on the tool server; its part of the answer was left out", name)
            # The photos travel with the text (AgentReply is a str), so the app can show them under the reply.
            legs = build_legs(roads, geo.journey, gateway_flow)
            return AgentReply(f"{text}\n\n{_unchecked_note(unchecked, not_set_up)}" if unchecked else text, photos, geocoded, legs=legs)

        # Identical calls (same tool, same arguments) in one round run once; the extra ones are dropped.
        to_run, duplicates = _dedupe_calls(calls)
        # Record which tools the model chose (names only; the model's names are untrusted but safe to log).
        tools_called.extend(c.name for c, _ in to_run)
        audit_event(
            "model_decision", round=round_no, decision="tool_calls", tool_names=[c.name for c, _ in to_run],
            duration_ms=_ms(started), tool_choice=choice_label,
            tools_called=list(tools_called), **usage, **prompt_info,
        )
        for call, parsed in duplicates:
            # Only argument NAMES are logged, never their values.
            audit_event(
                "tool_call", "skipped", round=round_no, tool=call.name,
                argument_names=sorted(parsed.arguments), error_code="duplicate", duration_ms=0,
            )

        # Keep the model's tool requests in the conversation (without item ids: nothing is stored), then answer each.
        # A dropped duplicate is left out of the conversation altogether, so no call is left without its output.
        input_items.extend(
            {"type": "function_call", "call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c, _ in to_run
        )
        failures = 0  # how many of this round's tool calls did not return data
        if mode == "route":
            # Locate the places first, so the searches of the same round can be checked against them (stable sort).
            to_run.sort(key=lambda item: item[0].name != "geocode_places")
        for call, parsed in to_run:
            args = parsed.arguments
            tool_started = time.perf_counter()
            if parsed.error is not None:
                # Unreadable arguments: do not call the tool, tell the model (fixed text, not its own).
                failed_call = True
                result = {"ok": False, "error": "bad_arguments", "message": parsed.error}
                audit_event(
                    "tool_call", "refused", round=round_no, tool=call.name,
                    argument_names=[], error_code="bad_arguments", duration_ms=_ms(tool_started),
                )
            elif call.name == "send_trip_email" and args.get("confirmed") is True and not is_send_confirmation(message):
                # History is untrusted: only the CURRENT message can ask to send or confirm. An earlier message (or a
                # made-up one in the history) can never confirm an email.
                failed_call = True
                result = {
                    "ok": False, "error": "confirmation_required",
                    "message": "The user has not confirmed sending in their latest message. Show the draft and ask them to confirm.",
                }
                audit_event(
                    "tool_call", "refused", round=round_no, tool=call.name,
                    argument_names=sorted(args), error_code="confirmation_required", duration_ms=_ms(tool_started),
                )
            elif mode == "route" and (refusal := _place_refusal(geo, call.name, args)):
                # Only pairs where both ends were geocoded are searched (never a remote place that has no station).
                failed_call = True
                result = refusal
                audit_event(
                    "tool_call", "refused", round=round_no, tool=call.name,
                    argument_names=sorted(args), error_code=refusal["error"], duration_ms=_ms(tool_started),
                )
            elif mcp is not None and call.name in allowed:
                result = await mcp.call_tool(call.name, args)
                # A tool reports failure with ok=False and an error code of its own.
                failed_call = result.get("ok") is False
                audit_event(
                    "tool_call", "error" if failed_call else "ok", round=round_no, tool=call.name,
                    argument_names=sorted(args), error_code=result.get("error") if failed_call else None,
                    duration_ms=_ms(tool_started),
                )
                # The places geocode_places found are sent back, so the browser can remember the last place.
                if call.name == "geocode_places" and result.get("ok") is True:
                    geocoded.extend(_geocoded(result))
                    geo.add(result)
                # A found road is drawn as a solid leg (its input points tell which journey pairs it covers).
                if call.name == "get_route" and result.get("ok") is True and result.get("found") is True and isinstance(result.get("route"), list):
                    roads.append({"inputs": args.get("points") or [], "points": result["route"]})
                # Photos found by the image tool are kept for the app to show under the reply (checked again later).
                if call.name == "get_place_images" and result.get("ok") is True and result.get("found") is True:
                    photos.extend(result.get("images") if isinstance(result.get("images"), list) else [])
            else:
                failed_call = True
                result = {"ok": False, "error": "unknown_tool", "message": "That tool is not available."}
                audit_event(
                    "tool_call", "refused", round=round_no, tool=call.name,
                    argument_names=sorted(args), error_code="unknown_tool", duration_ms=_ms(tool_started),
                )
            # A destination with no station or bus city (or one that is not located) is the signal for the gateway flow.
            if mode == "route" and is_gateway_signal(call.name, result):
                gateway_flow = True
            failures += failed_call
            attempted.add(call.name)
            # The latest attempt decides: a tool that failed earlier and worked later counts as working.
            if failed_call:
                failed.add(call.name)
                failed_codes[call.name] = str(result.get("error") or "")
            else:
                failed.discard(call.name)
                failed_codes.pop(call.name, None)
            # The result goes back as data only, never as instructions (rule 5).
            input_items.append(oa.function_call_output(call.call_id, result))
        if "web_search" in required and "web_search" in failed:
            # Search is the one tool whose failure we never talk around: a fixed reply, no further model call.
            # (It still carries the places found, so the browser can remember the place; never the photos.)
            return AgentReply(_search_failed_reply(failed_codes, round_no, prompt_info), places=geocoded)
        if forced and failures == len(to_run) and mode != "route":
            # The model was forced to use a tool and every tool failed: do not let it answer from memory. (Never in route
            # mode: there the failed tools are named in the answer, with the other results kept, never this fixed reply.)
            audit_event("model_decision", "fallback", round=round_no, decision="all_tools_failed", **prompt_info)
            return NO_LIVE_INFO_REPLY

    audit_event("model_decision", "error", round=MAX_TOOL_ROUNDS, error_type="too_many_rounds", **prompt_info)
    raise AgentError("Too many tool rounds")


async def _run(
    message: str, mode: str, prompt: BuiltPrompt, history: list[dict] | None = None, last_place: dict | None = None,
    preferences: dict | None = None,
) -> str:
    """Connect to the MCP server (if it is up), discover its tools and run the conversation."""
    try:
        # One MCP connection per request keeps the server stateless (rule 1); it closes on exit.
        async with McpClient() as mcp:
            try:
                tools = await mcp.list_tools()
                audit_event("mcp_discovery", tool_names=[t["name"] for t in tools])
            except Exception as exc:
                # Discovery failed: carry on without tools rather than failing the chat.
                logger.warning("MCP tool discovery failed; answering without tools", exc_info=True)
                audit_event("mcp_discovery", "error", error_type=type(exc).__name__)
                tools = []
            return await _converse(message, mode, prompt, mcp, tools, history, last_place, preferences)
    except McpClientError as exc:
        # The tool server could not start: still answer, just without tools.
        logger.warning("MCP server unavailable; answering without tools", exc_info=True)
        audit_event("mcp_discovery", "error", error_type=type(exc).__name__, detail="mcp_server_unavailable")
        return await _converse(message, mode, prompt, None, [], history, last_place, preferences)


async def run_agent(
    message: str, history: list[dict] | None = None, last_mode: str | None = None, last_place: dict | None = None,
    preferences: dict | None = None,
) -> str:
    """Answer one user message, letting the model use the MCP tools. Returns the reply text (an AgentReply).

    Per-session memory comes with the request and is never stored: history (earlier user and assistant messages, in
    order), last_mode (the mode of the previous reply) and last_place ({name, lat, lng} of the last geocoded place).
    All of it is untrusted browser data. Writes the request's audit trail: received (with only the NUMBER of history
    messages, never their text), each step, then completed or failed.

    preferences: the saved reply language, budget style and travel mode (validated by the route). They become one short
    fixed-template line after the fixed system prompt (modes.preferences_line); only the names of the fields set are logged.
    """
    new_request_id()
    _active_model.set("")  # every request starts on the main model
    started = time.perf_counter()
    history_items = _history_items(history)
    # The mode is chosen again on every turn; when nothing in the message decides it, the previous reply's mode is kept.
    mode, mode_reason = select_mode_with_reason(message, last_mode if last_mode in MODES else None)
    prompt = build_system_prompt(mode)
    audit_event(
        "request_received", mode=mode, mode_reason=mode_reason, prompt_hash=prompt.hash, prompt_versions=list(prompt.versions),
        history_count=len(history_items), last_mode=last_mode if last_mode in MODES else None, has_last_place=bool(last_place),
        preference_fields=sorted(k for k, v in (preferences or {}).items() if v),
        **message_fingerprint(message),
    )
    log_question(message)  # debug log only, and only with LOG_MESSAGES on (never the JSON audit log)
    cap = get_settings().chat_request_timeout_seconds
    try:
        # One overall cap for the whole request (every model call, retry and tool call together).
        async with asyncio.timeout(cap) as limit:
            reply = await _run(message, mode, prompt, history_items, last_place, preferences)
    except TimeoutError:
        if not limit.expired():
            # A timeout from somewhere else, not our cap: record its type like any unexpected failure, then let it surface.
            audit_event("request_failed", "error", error_type="TimeoutError", duration_ms=_ms(started))
            raise
        # The cap ran out: a friendly reply, not an error page. Only the cap and the duration are logged.
        audit_event("request_timeout", "error", cap_s=cap, duration_ms=_ms(started))
        log_answer(REQUEST_TOO_LONG_REPLY, mode)
        return AgentReply(REQUEST_TOO_LONG_REPLY, mode=mode)
    except AgentError as exc:
        audit_event(
            "request_failed", "error", error=str(exc), error_type=_cause_name(exc),
            http_status=exc.status_code, error_kind=exc.kind, duration_ms=_ms(started),
        )
        raise
    except Exception as exc:
        # Unexpected failure: record only its type, then let it surface as before.
        audit_event("request_failed", "error", error_type=type(exc).__name__, duration_ms=_ms(started))
        raise
    audit_event("request_completed", reply_chars=len(reply), duration_ms=_ms(started))
    log_answer(str(reply), mode)  # debug log only; an email draft is never written
    # Every reply tells the browser which mode was used (and carries any photos and places found).
    if not isinstance(reply, AgentReply):
        reply = AgentReply(reply)
    reply.mode = mode
    return reply
