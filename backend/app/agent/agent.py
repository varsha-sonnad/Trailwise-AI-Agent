# agent.py: the Trailwise agent. Loads the system prompt, discovers the MCP tools, sends the user's
# message to Gemini and runs a capped tool loop: when Gemini asks for a tool, the agent calls it
# through the MCP client and gives the result back, until Gemini writes the final reply.
# Every step is also written to the audit log (app/core/audit.py): request, model decisions,
# tool calls, status, timing and errors, never message text, tool arguments or keys.
# No modes or chat history yet; those are added later.

import asyncio
import contextvars
import logging
import time
from functools import lru_cache
from pathlib import Path

from google import genai
from google.genai import errors, types

from app.agent.mcp_client import McpClient, McpClientError
from app.config import get_settings
from app.core.audit import audit_event, message_fingerprint, new_request_id

logger = logging.getLogger(__name__)

# The prompt file sits next to this file, in agent/prompts/.
SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"

# How long to wait for one Gemini call before giving up, in milliseconds.
REQUEST_TIMEOUT_MS = 20_000

# Pauses (seconds) before each retry of a Gemini call that failed with a temporary server error.
# Two retries at most, so a busy model costs a few seconds, not a failed chat.
RETRY_DELAYS_SECONDS = (2, 5)
# HTTP codes worth retrying: 500 server error, 503 overloaded, 504 gateway timeout.
# Client errors (401 bad key, 429 rate limit, 4xx bad request) are never retried.
RETRYABLE_STATUS_CODES = frozenset({500, 503, 504})
# The code Gemini uses for "model overloaded" (high demand).
OVERLOADED_STATUS_CODE = 503

# Most Gemini rounds per request. Stops a model that keeps asking for tools from looping forever.
MAX_TOOL_ROUNDS = 5

# The fallback model this request has switched to ("" = still on the main model). Once a request
# switches, its later tool rounds stay on the fallback so they do not wait through the main model's
# failures again. A ContextVar is per-request state, not stored user data (rule 1).
_active_model: contextvars.ContextVar[str] = contextvars.ContextVar("agent_active_model", default="")


class AgentError(Exception):
    """Raised when the agent cannot produce a reply (Gemini failed or returned nothing)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        # The HTTP status Gemini answered with, if it answered (kept for the audit log, never shown to users).
        self.status_code = status_code


class AgentBusyError(AgentError):
    """Raised when Gemini is overloaded (HTTP 503) even after retries; the route answers 503, not 502."""


def _ms(start: float) -> int:
    """Milliseconds elapsed since a time.perf_counter() reading."""
    return round((time.perf_counter() - start) * 1000)


def _cause_name(exc: Exception) -> str:
    """Name of the underlying error type (never its message, which could carry request details)."""
    return type(exc.__cause__ or exc).__name__


def _status_code(exc: Exception) -> int | None:
    """The HTTP status code of a Gemini API error, or None for anything else (timeouts, network errors)."""
    if isinstance(exc, errors.APIError) and isinstance(exc.code, int):
        return exc.code
    return None


@lru_cache
def load_system_prompt() -> str:
    """Read the system prompt from disk once and reuse it on every later call."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache
def get_client() -> genai.Client:
    """Build the Gemini client once. The API key comes from settings, never from code."""
    settings = get_settings()
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )


def to_declarations(tools: list[dict]) -> list[types.FunctionDeclaration]:
    """Turn the MCP tool list (name, description, input_schema) into Gemini function declarations."""
    return [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters_json_schema=t["input_schema"],
        )
        for t in tools
    ]


async def _call_model(model: str, contents: list[types.Content], config: types.GenerateContentConfig):
    """One async call to one Gemini model. Wraps SDK errors.

    A temporary server error (500, 503, 504) is retried after a short pause, at most
    len(RETRY_DELAYS_SECONDS) times. Any other error, and timeouts, fail at once.
    """
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            # The async client keeps the server non-blocking (project rule 2).
            return await get_client().aio.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            code = _status_code(exc)
            if code in RETRYABLE_STATUS_CODES and attempt < len(RETRY_DELAYS_SECONDS):
                # Temporary failure with retries left: note it (model, status code and delay only), wait, try again.
                delay = RETRY_DELAYS_SECONDS[attempt]
                audit_event("model_retry", "retry", model=model, attempt=attempt + 1, http_status=code, delay_s=delay)
                await asyncio.sleep(delay)
                continue
            # Out of retries or not retryable. Hide SDK details from callers; keep the original error
            # attached for logs. An overloaded model gets its own error so the route can say "busy".
            error_class = AgentBusyError if code == OVERLOADED_STATUS_CODE else AgentError
            raise error_class("Gemini request failed", status_code=code) from exc


async def _generate(contents: list[types.Content], declarations: list[types.FunctionDeclaration]):
    """One async Gemini call with the system prompt and (if any) the tools.

    Tries the main model (with retries). If it is still failing with a server error (500, 503, 504)
    and a different GEMINI_FALLBACK_MODEL is set, switches to that model for the rest of the request.
    Client errors (bad key, rate limit, bad request) and timeouts never trigger the fallback.
    """
    config = types.GenerateContentConfig(
        system_instruction=load_system_prompt(),
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        # We run the tool loop ourselves, through MCP, so the SDK must not call tools on its own.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    settings = get_settings()
    primary = settings.gemini_model
    fallback = settings.gemini_fallback_model.strip()

    # This request already switched to the fallback in an earlier round: stay on it.
    if _active_model.get():
        return await _call_model(_active_model.get(), contents, config)

    try:
        return await _call_model(primary, contents, config)
    except AgentError as exc:
        # Only a server-side failure with a usable, different fallback model leads to a switch.
        if not fallback or fallback == primary or exc.status_code not in RETRYABLE_STATUS_CODES:
            raise
        audit_event("model_fallback", "fallback", http_status=exc.status_code, from_model=primary, to_model=fallback)
        _active_model.set(fallback)
        return await _call_model(fallback, contents, config)


async def _converse(message: str, mcp: McpClient | None, tools: list[dict]) -> str:
    """Run the Gemini <-> tool loop for one user message and return the final reply text."""
    declarations = to_declarations(tools)
    # Only tool names the server really offered may be called (the model's names are not trusted).
    allowed = {t["name"] for t in tools}
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=message)])]

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        started = time.perf_counter()
        try:
            response = await _generate(contents, declarations)
        except AgentError as exc:
            audit_event(
                "model_decision", "error", round=round_no, error_type=_cause_name(exc),
                http_status=exc.status_code, duration_ms=_ms(started),
            )
            raise
        calls = response.function_calls
        if not calls:
            # No tool requested: this is the final answer. text is None for e.g. a blocked reply.
            if not response.text:
                audit_event("model_decision", "error", round=round_no, error_type="empty_reply", duration_ms=_ms(started))
                raise AgentError("Gemini returned an empty reply")
            audit_event("model_decision", round=round_no, decision="final_answer", duration_ms=_ms(started))
            return response.text

        # Record which tools Gemini chose (names only; the model's names are untrusted but safe to log).
        audit_event("model_decision", round=round_no, decision="tool_calls", tool_names=[c.name for c in calls], duration_ms=_ms(started))

        # Keep Gemini's tool request in the history, then answer each call.
        contents.append(response.candidates[0].content)
        parts = []
        for call in calls:
            args = dict(call.args or {})
            tool_started = time.perf_counter()
            if mcp is not None and call.name in allowed:
                result = await mcp.call_tool(call.name, args)
                # A tool reports failure with ok=False and an error code of its own.
                failed = result.get("ok") is False
                audit_event(
                    "tool_call", "error" if failed else "ok", round=round_no, tool=call.name,
                    argument_names=sorted(args), error_code=result.get("error") if failed else None,
                    duration_ms=_ms(tool_started),
                )
            else:
                result = {"ok": False, "error": "unknown_tool", "message": "That tool is not available."}
                audit_event(
                    "tool_call", "refused", round=round_no, tool=call.name,
                    argument_names=sorted(args), error_code="unknown_tool", duration_ms=_ms(tool_started),
                )
            # The result goes back as data only, never as instructions (rule 5).
            parts.append(types.Part.from_function_response(name=call.name, response=result))
        contents.append(types.Content(role="user", parts=parts))

    audit_event("model_decision", "error", round=MAX_TOOL_ROUNDS, error_type="too_many_rounds")
    raise AgentError("Too many tool rounds")


async def _run(message: str) -> str:
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
            return await _converse(message, mcp, tools)
    except McpClientError as exc:
        # The tool server could not start: still answer, just without tools.
        logger.warning("MCP server unavailable; answering without tools", exc_info=True)
        audit_event("mcp_discovery", "error", error_type=type(exc).__name__, detail="mcp_server_unavailable")
        return await _converse(message, None, [])


async def run_agent(message: str) -> str:
    """Answer one user message, letting Gemini use the MCP tools. Returns the reply text.

    Writes the request's audit trail: received, each step, then completed or failed.
    """
    new_request_id()
    _active_model.set("")  # every request starts on the main model
    started = time.perf_counter()
    audit_event("request_received", **message_fingerprint(message))
    try:
        reply = await _run(message)
    except AgentError as exc:
        audit_event(
            "request_failed", "error", error=str(exc), error_type=_cause_name(exc),
            http_status=exc.status_code, duration_ms=_ms(started),
        )
        raise
    except Exception as exc:
        # Unexpected failure: record only its type, then let it surface as before.
        audit_event("request_failed", "error", error_type=type(exc).__name__, duration_ms=_ms(started))
        raise
    audit_event("request_completed", reply_chars=len(reply), duration_ms=_ms(started))
    return reply
