# openai_adapter.py: small pure helpers between the agent and the OpenAI Responses API (migration step 2).
# Nothing here calls the network or reads settings: the functions only build request pieces (tool definitions, the
# allowed_tools choice, function_call_output items) and read replies (tool arguments, cached tokens, errors). The
# agent loop does not use them yet; step 3 wires them in. The OpenAI SDK's own types describe the shapes.

import copy
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import openai
from openai.types.responses.function_tool_param import FunctionToolParam
from openai.types.responses.response_input_item_param import FunctionCallOutput
from openai.types.responses.tool_choice_allowed_param import ToolChoiceAllowedParam

# What the model gets when a tool declares no parameters: an object schema with no properties.
EMPTY_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}}

# Tool arguments longer than this (in characters) are refused instead of parsed (a runaway reply is not a real call).
MAX_ARGUMENT_CHARS = 20_000

# HTTP statuses worth retrying: 500 server error, 502 bad gateway, 503 overloaded, 504 gateway timeout.
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})


# ---------------------------------------------------------------- tool definitions

def function_tool(name: str, description: Optional[str], parameters: Optional[dict]) -> FunctionToolParam:
    """Build one function tool definition for the `tools` list. `strict` is always False.

    The Responses API treats a missing `strict` as strict mode, which forces every property to be required and
    forbids extra ones; our tool schemas have optional arguments, so strict is sent explicitly as False.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a tool needs a non-empty name")
    tool: FunctionToolParam = {
        "type": "function",
        "name": name,
        "parameters": parameters if isinstance(parameters, dict) and parameters else copy.deepcopy(EMPTY_PARAMETERS),
        "strict": False,
    }
    if description:
        tool["description"] = description
    return tool


def tools_from_mcp(mcp_tools: Iterable[Any]) -> list[FunctionToolParam]:
    """Turn MCP tool listings (objects with name, description, inputSchema, or dicts with those keys) into definitions."""
    out: list[FunctionToolParam] = []
    for t in mcp_tools:
        get = (lambda key: t.get(key)) if isinstance(t, dict) else (lambda key: getattr(t, key, None))
        out.append(function_tool(get("name"), get("description"), get("inputSchema")))
    return out


# ---------------------------------------------------------------- forcing a tool (allowed_tools)

def build_tool_choice(
    allowed_names: Iterable[str],
    available_names: Optional[Iterable[str]] = None,
    mode: str = "required",
) -> Optional[ToolChoiceAllowedParam]:
    """Build the `tool_choice` value that limits the model to certain function tools, or None.

    None means "send no tool_choice at all": that is the result when there are no names (the API rejects a
    tool_choice without tools, and email mode / greetings force nothing). Names are de-duplicated in order, and when
    `available_names` is given, names that are not registered tools are dropped (the API rejects unknown ones).
    mode "required" forces a call to one of the allowed tools (round 1); "auto" lets the model choose among them.
    """
    if mode not in ("required", "auto"):
        raise ValueError("mode must be 'required' or 'auto'")
    available = None if available_names is None else set(available_names)
    names: list[str] = []
    for n in allowed_names:
        if isinstance(n, str) and n and n not in names and (available is None or n in available):
            names.append(n)
    if not names:
        return None
    return {"type": "allowed_tools", "mode": mode, "tools": [{"type": "function", "name": n} for n in names]}


# ---------------------------------------------------------------- reading tool calls and answering them

@dataclass(frozen=True)
class ParsedArguments:
    """The result of reading a tool call's arguments: the dict (empty on failure) and an error text or None."""

    arguments: dict
    error: Optional[str] = None


def parse_arguments(raw: Optional[str]) -> ParsedArguments:
    """Parse the JSON string in a function call's `arguments`. Never raises.

    An empty or missing string means "no arguments" ({}). Bad JSON, JSON that is not an object, or an oversized
    string gives an empty dict plus a short fixed error text that never repeats the model's text (it is data).
    """
    if raw is None or not str(raw).strip():
        return ParsedArguments({})
    if not isinstance(raw, str):
        return ParsedArguments({}, "the tool arguments were not text")
    if len(raw) > MAX_ARGUMENT_CHARS:
        return ParsedArguments({}, "the tool arguments were too long")
    try:
        value = json.loads(raw)
    except ValueError:  # json.JSONDecodeError is a ValueError
        return ParsedArguments({}, "the tool arguments were not valid JSON")
    if not isinstance(value, dict):
        return ParsedArguments({}, "the tool arguments were not a JSON object")
    return ParsedArguments(value)


def function_call_output(call_id: str, output: Any) -> FunctionCallOutput:
    """Build the input item that answers one function call. `output` is text, or any JSON-able value (turned to text)."""
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call_id is required")
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
    return {"type": "function_call_output", "call_id": call_id, "output": text}


# ---------------------------------------------------------------- usage

def cached_tokens(source: Any) -> int:
    """Read usage.input_tokens_details.cached_tokens from a Response or a ResponseUsage. 0 when anything is missing."""
    usage = getattr(source, "usage", source)  # a Response has .usage; a usage object is used as it is
    details = getattr(usage, "input_tokens_details", None)
    value = getattr(details, "cached_tokens", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


# ---------------------------------------------------------------- errors

@dataclass(frozen=True)
class ErrorInfo:
    """A classified OpenAI failure. `kind` is one of: busy, server_error, rate_limited, quota, auth, bad_request,
    timeout, connection, unknown. `message` is fixed text, never taken from the exception (it can echo request data)."""

    kind: str
    retryable: bool
    status_code: Optional[int]
    message: str


def map_error(exc: BaseException) -> ErrorInfo:
    """Classify an exception from the OpenAI client. Retryable only for 500, 502, 503 and 504."""
    if isinstance(exc, openai.APITimeoutError):  # a subclass of APIConnectionError, so it is checked first
        return ErrorInfo("timeout", False, None, "The AI service did not answer in time.")
    if isinstance(exc, openai.APIConnectionError):
        return ErrorInfo("connection", False, None, "The AI service could not be reached.")
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status == 429:
            # The same status covers "slow down" and "the account has no quota left"; only the error code tells them apart.
            if getattr(exc, "code", None) == "insufficient_quota":
                return ErrorInfo("quota", False, status, "The AI service quota is used up.")
            return ErrorInfo("rate_limited", False, status, "The AI service is rate limiting requests.")
        if status == 503:
            return ErrorInfo("busy", True, status, "The AI service is overloaded.")
        if status in RETRYABLE_STATUS_CODES or status >= 500:
            return ErrorInfo("server_error", status in RETRYABLE_STATUS_CODES, status, "The AI service had a server error.")
        if status in (401, 403):
            return ErrorInfo("auth", False, status, "The AI service refused the API key.")
        if status in (400, 404, 409, 422):
            return ErrorInfo("bad_request", False, status, "The AI service rejected the request.")
        return ErrorInfo("unknown", False, status, "The AI service returned an unexpected error.")
    return ErrorInfo("unknown", False, None, "The AI call failed.")
