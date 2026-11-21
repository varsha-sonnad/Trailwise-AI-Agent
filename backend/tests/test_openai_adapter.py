# test_openai_adapter.py: tests for app/agent/openai_adapter.py. They use the real OpenAI SDK types and error classes,
# and a fake HTTP transport inside the SDK client, so nothing touches the network and no real key or .env is used.

import json

import httpx2
import openai
import pytest
from openai.types.responses import FunctionTool, Response, ResponseFunctionToolCall, ResponseUsage, ToolChoiceAllowed
from openai.types.responses.response_input_item_param import ResponseInputItemParam
from pydantic import TypeAdapter

from app.agent import openai_adapter as oa

# A real SDK validator for one input item (the shape function_call_output must have).
INPUT_ITEM = TypeAdapter(ResponseInputItemParam)
REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def status_error(status: int, code: str | None = None) -> openai.APIStatusError:
    """Build the SDK's own error for an HTTP status, the way the client does (the body is the inner error object)."""
    response = httpx2.Response(status, request=REQUEST, json={"error": {"code": code, "message": "SECRET-ECHO"}})
    body = {"code": code, "message": "SECRET-ECHO"}
    cls = {400: openai.BadRequestError, 401: openai.AuthenticationError, 403: openai.PermissionDeniedError,
           404: openai.NotFoundError, 409: openai.ConflictError, 422: openai.UnprocessableEntityError,
           429: openai.RateLimitError}.get(status, openai.InternalServerError if status >= 500 else openai.APIStatusError)
    return cls("SECRET-ECHO", response=response, body=body)


# ---------------------------------------------------------------- tool definitions

def test_function_tool_is_a_valid_sdk_tool_and_never_strict_true():
    """The definition validates as the SDK's FunctionTool, and strict is the explicit value False (not true, not missing)."""
    schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    tool = oa.function_tool("get_weather", "Weather for a city.", schema)
    parsed = FunctionTool.model_validate(tool)
    assert (parsed.type, parsed.name, parsed.description) == ("function", "get_weather", "Weather for a city.")
    assert parsed.parameters == schema
    assert tool["strict"] is False


def test_function_tool_without_parameters_or_description_gets_an_empty_object_schema():
    """No schema gives an empty object schema; no description leaves the key out."""
    tool = oa.function_tool("ping", None, None)
    assert tool["parameters"] == {"type": "object", "properties": {}}
    assert "description" not in tool
    assert oa.function_tool("ping", "", {})["parameters"] == {"type": "object", "properties": {}}


def test_function_tool_does_not_share_the_default_schema_between_tools():
    """Changing one tool's default schema must not change another's."""
    a, b = oa.function_tool("a", None, None), oa.function_tool("b", None, None)
    a["parameters"]["properties"]["x"] = {}
    assert b["parameters"] == {"type": "object", "properties": {}}


@pytest.mark.parametrize("name", ["", "   ", None, 5])
def test_function_tool_needs_a_name(name):
    """A tool without a usable name is a programming error and raises."""
    with pytest.raises(ValueError):
        oa.function_tool(name, "d", {})


def test_tools_from_mcp_reads_objects_and_dicts():
    """MCP tool objects (inputSchema attribute) and plain dicts both convert, in order."""

    class McpTool:
        name = "geocode_places"
        description = "Find places."
        inputSchema = {"type": "object", "properties": {"q": {"type": "string"}}}

    tools = oa.tools_from_mcp([McpTool(), {"name": "ping", "description": None, "inputSchema": None}])
    assert [t["name"] for t in tools] == ["geocode_places", "ping"]
    assert tools[0]["parameters"]["properties"] == {"q": {"type": "string"}}
    assert all(t["strict"] is False for t in tools)


# ---------------------------------------------------------------- allowed_tools builder

def test_build_tool_choice_is_a_valid_sdk_allowed_tools_choice():
    """The choice validates as the SDK's ToolChoiceAllowed and lists each function by name, in order."""
    choice = oa.build_tool_choice(["web_search", "get_weather"])
    parsed = ToolChoiceAllowed.model_validate(choice)
    assert parsed.mode == "required"
    assert parsed.tools == [{"type": "function", "name": "web_search"}, {"type": "function", "name": "get_weather"}]


def test_build_tool_choice_auto_mode():
    """mode='auto' is passed through."""
    assert oa.build_tool_choice(["a"], mode="auto")["mode"] == "auto"


@pytest.mark.parametrize("names", [[], (), iter(())])
def test_no_names_means_no_tool_choice(names):
    """With nothing to allow there is no tool_choice at all (None), so the caller omits the field."""
    assert oa.build_tool_choice(names) is None


def test_unregistered_names_are_dropped_and_all_dropped_means_none():
    """Only names that exist among the registered tools stay; if none do, there is no tool_choice."""
    choice = oa.build_tool_choice(["web_search", "ghost", "get_weather"], available_names=["get_weather", "web_search"])
    assert [t["name"] for t in choice["tools"]] == ["web_search", "get_weather"]
    assert oa.build_tool_choice(["ghost"], available_names=["get_weather"]) is None
    assert oa.build_tool_choice(["web_search"], available_names=[]) is None


def test_duplicate_and_blank_names_are_ignored():
    """Repeated, empty and non-string names are skipped."""
    choice = oa.build_tool_choice(["a", "a", "", None, "b"])
    assert [t["name"] for t in choice["tools"]] == ["a", "b"]


def test_a_bad_mode_is_rejected():
    """Only 'required' and 'auto' are valid modes."""
    with pytest.raises(ValueError):
        oa.build_tool_choice(["a"], mode="any")


# ---------------------------------------------------------------- argument parsing

def test_good_arguments_parse_to_a_dict():
    """A JSON object string becomes a dict with no error."""
    parsed = oa.parse_arguments('{"city": "Goa", "days": 3}')
    assert parsed.arguments == {"city": "Goa", "days": 3} and parsed.error is None


@pytest.mark.parametrize("raw", ["", "   ", None, "{}"])
def test_empty_arguments_mean_no_arguments(raw):
    """An empty or missing string, or {}, is a valid call with no arguments."""
    parsed = oa.parse_arguments(raw)
    assert parsed.arguments == {} and parsed.error is None


@pytest.mark.parametrize("raw", ['{"city": ', "not json", "{'a': 1}", '{"a": 1}}'])
def test_bad_json_gives_an_empty_dict_and_an_error_without_raising(raw):
    """Broken JSON never raises; the error text is fixed and does not repeat the model's text."""
    parsed = oa.parse_arguments(raw)
    assert parsed.arguments == {}
    assert parsed.error == "the tool arguments were not valid JSON"
    assert raw not in parsed.error


@pytest.mark.parametrize("raw", ["[1, 2]", '"text"', "5", "null", "true"])
def test_json_that_is_not_an_object_is_an_error(raw):
    """A list, string, number, null or boolean is valid JSON but not valid arguments."""
    parsed = oa.parse_arguments(raw)
    assert parsed.arguments == {} and parsed.error == "the tool arguments were not a JSON object"


def test_oversized_arguments_are_refused():
    """A runaway string is refused without parsing it."""
    parsed = oa.parse_arguments('{"a": "' + "x" * oa.MAX_ARGUMENT_CHARS + '"}')
    assert parsed.arguments == {} and parsed.error == "the tool arguments were too long"


def test_non_text_arguments_are_an_error():
    """Something that is not a string (a dict already parsed) is reported, not crashed on."""
    assert oa.parse_arguments({"a": 1}).error == "the tool arguments were not text"


def test_arguments_of_a_real_sdk_function_call_parse():
    """The arguments field of the SDK's ResponseFunctionToolCall goes straight into parse_arguments."""
    call = ResponseFunctionToolCall(type="function_call", call_id="call_1", name="get_weather", arguments='{"city":"Goa"}')
    assert oa.parse_arguments(call.arguments).arguments == {"city": "Goa"}


# ---------------------------------------------------------------- function_call_output items

def test_function_call_output_with_text():
    """Text output is passed unchanged; the item is a valid SDK input item."""
    item = oa.function_call_output("call_1", "plain text")
    assert item == {"type": "function_call_output", "call_id": "call_1", "output": "plain text"}
    INPUT_ITEM.validate_python(item)


def test_function_call_output_turns_data_into_json_text():
    """A dict is sent as JSON text (keeping non-ASCII readable), never as a nested object."""
    item = oa.function_call_output("call_2", {"ok": False, "error": "unavailable", "city": "Zürich"})
    assert isinstance(item["output"], str)
    assert json.loads(item["output"]) == {"ok": False, "error": "unavailable", "city": "Zürich"}
    assert "Zürich" in item["output"]
    INPUT_ITEM.validate_python(item)


def test_function_call_output_survives_values_json_cannot_encode():
    """An odd value (a set) does not crash the loop; it is turned into text."""
    assert isinstance(oa.function_call_output("c", {"x": {1, 2}})["output"], str)


@pytest.mark.parametrize("call_id", ["", None, 5])
def test_function_call_output_needs_a_call_id(call_id):
    """Without the model's call id the output cannot be matched, so it raises."""
    with pytest.raises(ValueError):
        oa.function_call_output(call_id, "x")


# ---------------------------------------------------------------- cached tokens

def usage(cached: int) -> ResponseUsage:
    """A real SDK usage object with the given cached token count."""
    return ResponseUsage.model_validate({
        "input_tokens": 1200, "output_tokens": 30, "total_tokens": 1230,
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    })


def test_cached_tokens_from_a_usage_object_and_from_a_response():
    """The reader accepts the usage object or a Response holding it."""
    assert oa.cached_tokens(usage(1024)) == 1024
    response = Response.model_construct(usage=usage(512))
    assert oa.cached_tokens(response) == 512


def test_cached_tokens_is_zero_when_nothing_was_cached():
    """A real zero stays zero."""
    assert oa.cached_tokens(usage(0)) == 0


@pytest.mark.parametrize("source", [None, object(), Response.model_construct(usage=None),
                                    Response.model_construct(usage=ResponseUsage.model_construct(input_tokens_details=None))])
def test_cached_tokens_is_zero_when_data_is_missing(source):
    """No usage, no details or an unrelated object gives 0, never an error."""
    assert oa.cached_tokens(source) == 0


@pytest.mark.parametrize("bad", [-5, "12", 1.5, True, None])
def test_cached_tokens_ignores_odd_values(bad):
    """Negative numbers, strings, floats, booleans and None are not token counts: they give 0."""
    odd = ResponseUsage.model_construct(input_tokens_details=openai.types.responses.response_usage.InputTokensDetails.model_construct(cached_tokens=bad))
    assert oa.cached_tokens(odd) == 0


# ---------------------------------------------------------------- error mapping

@pytest.mark.parametrize("status,kind,retryable", [
    (503, "busy", True), (500, "server_error", True), (504, "server_error", True),
    (502, "server_error", True), (429, "rate_limited", False), (401, "auth", False),
    (403, "auth", False), (400, "bad_request", False), (404, "bad_request", False), (422, "bad_request", False),
])
def test_status_errors_are_classified(status, kind, retryable):
    """Each HTTP status maps to a kind, and only 500, 502, 503 and 504 are retryable."""
    info = oa.map_error(status_error(status))
    assert (info.kind, info.retryable, info.status_code) == (kind, retryable, status)


def test_a_429_with_insufficient_quota_is_a_quota_error_not_a_rate_limit():
    """Out-of-quota is not retryable and is told apart from "slow down" by the error code."""
    info = oa.map_error(status_error(429, "insufficient_quota"))
    assert (info.kind, info.retryable) == ("quota", False)


def test_unexpected_status_is_unknown():
    """A status the table does not know (418) is "unknown" and not retryable."""
    response = httpx2.Response(418, request=REQUEST, json={})
    info = oa.map_error(openai.APIStatusError("x", response=response, body=None))
    assert (info.kind, info.retryable, info.status_code) == ("unknown", False, 418)


def test_timeout_and_connection_errors():
    """A timeout and a connection failure are told apart (the timeout class is a subclass of the connection one)."""
    timeout = oa.map_error(openai.APITimeoutError(REQUEST))
    connection = oa.map_error(openai.APIConnectionError(request=REQUEST))
    assert (timeout.kind, timeout.retryable, timeout.status_code) == ("timeout", False, None)
    assert (connection.kind, connection.retryable, connection.status_code) == ("connection", False, None)


@pytest.mark.parametrize("exc", [ValueError("boom"), KeyError("k"), RuntimeError("SECRET-ECHO")])
def test_other_exceptions_are_unknown(exc):
    """Anything that is not an OpenAI error is "unknown" and not retryable."""
    info = oa.map_error(exc)
    assert (info.kind, info.retryable, info.status_code) == ("unknown", False, None)


@pytest.mark.parametrize("exc", [status_error(429, "insufficient_quota"), status_error(503), status_error(401),
                                 openai.APITimeoutError(REQUEST), RuntimeError("SECRET-ECHO")])
def test_error_messages_are_fixed_text_that_never_echo_the_exception(exc):
    """The message can be shown or logged safely: it never contains the exception's own text."""
    assert "SECRET-ECHO" not in oa.map_error(exc).message


# ---------------------------------------------------------------- the real SDK client with a fake transport

def fake_client(handler) -> openai.AsyncOpenAI:
    """A real AsyncOpenAI client whose HTTP layer is a local function: no network, no retries, a fake key."""
    return openai.AsyncOpenAI(api_key="fake-key", max_retries=0,
                              http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


async def test_adapter_output_goes_through_the_real_client_and_the_reply_is_read_back():
    """Tools, tool_choice and a function_call_output are serialised by the SDK; the canned reply is parsed and read."""
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "resp_1", "object": "response", "created_at": 0, "model": "test-model", "status": "completed",
            "output": [{"type": "function_call", "call_id": "call_9", "name": "get_weather", "arguments": '{"city": "Goa"}'}],
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": 1500, "output_tokens": 20, "total_tokens": 1520,
                      "input_tokens_details": {"cached_tokens": 1280, "cache_write_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}},
        })

    tools = [oa.function_tool("get_weather", "Weather.", {"type": "object", "properties": {"city": {"type": "string"}}})]
    choice = oa.build_tool_choice(["get_weather"], available_names=["get_weather"])
    response = await fake_client(handler).responses.create(
        model="test-model", store=False, input=[oa.function_call_output("call_0", {"ok": True})],
        tools=tools, tool_choice=choice)

    body = sent[0]
    assert body["store"] is False
    assert body["tools"][0]["strict"] is False and body["tools"][0]["name"] == "get_weather"
    assert body["tool_choice"] == {"type": "allowed_tools", "mode": "required", "tools": [{"type": "function", "name": "get_weather"}]}
    assert body["input"] == [{"type": "function_call_output", "call_id": "call_0", "output": '{"ok": true}'}]
    call = response.output[0]
    assert oa.parse_arguments(call.arguments).arguments == {"city": "Goa"}
    assert oa.cached_tokens(response) == 1280


async def test_no_tool_choice_field_is_sent_when_there_are_no_tools():
    """With no tools the caller leaves tool_choice out; the request body has neither field."""
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"id": "r", "object": "response", "created_at": 0, "model": "m", "status": "completed",
                                          "output": [], "parallel_tool_calls": True, "tool_choice": "auto", "tools": []})

    choice = oa.build_tool_choice([])
    kwargs = {} if choice is None else {"tool_choice": choice}
    response = await fake_client(handler).responses.create(model="m", input="hi", store=False, **kwargs)
    assert "tool_choice" not in sent[0] and "tools" not in sent[0]
    assert oa.cached_tokens(response) == 0  # a reply without usage


@pytest.mark.parametrize("status,code,kind", [(503, None, "busy"), (429, "insufficient_quota", "quota"),
                                              (429, "rate_limit_exceeded", "rate_limited"), (401, "invalid_api_key", "auth")])
async def test_errors_raised_by_the_real_client_are_mapped(status, code, kind):
    """The SDK raises its own error classes for HTTP failures (fake transport); map_error classifies them."""
    def handler(request):
        return httpx2.Response(status, json={"error": {"message": "SECRET-ECHO", "type": "x", "code": code}})

    with pytest.raises(openai.APIStatusError) as caught:
        await fake_client(handler).responses.create(model="m", input="hi")
    info = oa.map_error(caught.value)
    assert info.kind == kind and info.status_code == status
    assert "SECRET-ECHO" not in info.message


async def test_a_timeout_raised_by_the_real_client_is_mapped():
    """A transport timeout becomes the SDK's APITimeoutError, which maps to kind "timeout"."""
    def handler(request):
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(openai.APITimeoutError) as caught:
        await fake_client(handler).responses.create(model="m", input="hi")
    assert oa.map_error(caught.value).kind == "timeout"
