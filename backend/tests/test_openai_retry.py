# test_openai_retry.py: tests for the OpenAI call in agent._generate / _call_model: retries of temporary errors,
# the single retry of a rate limit, no retry of a used-up quota, the request that is sent, and the client settings.
# The OpenAI client is faked (no key, quota or network) and the pauses are set to zero.

import json

import httpx2
import openai
import pytest

from app.agent import agent
from app.agent.agent import AgentBusyError, AgentError, AgentUnavailableError, run_agent
from app.agent.modes import build_system_prompt
from app.core import audit
from tests.test_agent_tools import FakeMcp, text_reply, tool_reply

REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")
ERROR_CLASSES = {400: openai.BadRequestError, 401: openai.AuthenticationError, 403: openai.PermissionDeniedError,
                 429: openai.RateLimitError}


def api_error(status, code=None):
    """The SDK's own error for an HTTP status (the body is the inner error object, as the real client builds it)."""
    response = httpx2.Response(status, request=REQUEST, json={"error": {"code": code, "message": "fake"}})
    cls = ERROR_CLASSES.get(status, openai.InternalServerError if status >= 500 else openai.APIStatusError)
    return cls("fake", response=response, body={"code": code, "message": "fake"})


class FakeOpenAI:
    """Stand-in for the OpenAI client: plays back scripted results (an exception is raised, else returned)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        # The model name and the whole request of each call, in order.
        self.used_models = []
        self.requests = []
        # Mirrors client.responses.create.
        self.responses = self

    async def create(self, **kwargs):
        self.calls += 1
        self.used_models.append(kwargs.get("model"))
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    """No real pauses, a fixed main model, a fake MCP client, and the audit log in a temp file."""
    monkeypatch.setattr(agent, "RETRY_DELAYS_SECONDS", (0, 0))
    settings = audit.get_settings()
    monkeypatch.setattr(settings, "openai_model", "main-model")
    monkeypatch.setattr(settings, "openai_fallback_model", "")
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    FakeMcp.result = {"ok": True, "temperature_c": 30}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / "audit.log"))
    audit.reset_audit_logger()
    yield tmp_path / "audit.log"
    audit.reset_audit_logger()


def use_openai(monkeypatch, script):
    """Install a fake OpenAI client with the scripted results and return it."""
    fake = FakeOpenAI(script)
    monkeypatch.setattr(agent, "get_client", lambda: fake)
    return fake


def events(path):
    """Read the audit file back as a list of dicts."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- temporary server errors

async def test_retries_503_then_succeeds(monkeypatch, setup):
    fake = use_openai(monkeypatch, [api_error(503), api_error(503), text_reply("Hello!")])
    assert await run_agent("hi") == "Hello!"
    assert fake.calls == 3
    retries = [e for e in events(setup) if e["event"] == "model_retry"]
    assert [(e["attempt"], e["http_status"], e["error_kind"]) for e in retries] == [(1, 503, "busy"), (2, 503, "busy")]


@pytest.mark.parametrize("code", [500, 502, 504])
async def test_other_server_errors_are_retried(monkeypatch, code):
    fake = use_openai(monkeypatch, [api_error(code), text_reply("ok")])
    assert await run_agent("hi") == "ok"
    assert fake.calls == 2


async def test_retries_use_the_configured_delays(monkeypatch):
    """The pauses are the delays in order: the first retry waits the first delay, the second the second."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(agent, "RETRY_DELAYS_SECONDS", (2, 5))
    monkeypatch.setattr(agent.asyncio, "sleep", fake_sleep)
    use_openai(monkeypatch, [api_error(503), api_error(502), text_reply("ok")])
    assert await run_agent("hi") == "ok"
    assert slept == [2, 5]


async def test_gives_up_after_retries_with_busy_error(monkeypatch, setup):
    fake = use_openai(monkeypatch, [api_error(503)] * 3)
    with pytest.raises(AgentBusyError) as info:
        await run_agent("hi")
    assert fake.calls == 3 and info.value.status_code == 503  # 1 try + 2 retries
    last = events(setup)[-1]
    assert last["event"] == "request_failed" and last["http_status"] == 503 and last["error_kind"] == "busy"
    failed_round = next(e for e in events(setup) if e["event"] == "model_decision")
    assert failed_round["http_status"] == 503 and failed_round["error_kind"] == "busy"


async def test_other_server_errors_that_keep_failing_are_a_plain_agent_error(monkeypatch):
    fake = use_openai(monkeypatch, [api_error(502)] * 3)
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert not isinstance(info.value, AgentBusyError) and info.value.status_code == 502 and fake.calls == 3


# ---------------------------------------------------------------- 429: rate limit and quota

async def test_a_rate_limit_is_retried_once(monkeypatch, setup):
    fake = use_openai(monkeypatch, [api_error(429, "rate_limit_exceeded"), text_reply("ok")])
    assert await run_agent("hi") == "ok"
    assert fake.calls == 2
    retries = [e for e in events(setup) if e["event"] == "model_retry"]
    assert [(e["attempt"], e["http_status"], e["error_kind"]) for e in retries] == [(1, 429, "rate_limited")]


async def test_a_rate_limit_that_persists_is_retried_only_once(monkeypatch, setup):
    fake = use_openai(monkeypatch, [api_error(429, "rate_limit_exceeded")] * 3)
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.calls == 2  # one try + one retry, not more
    assert info.value.kind == "rate_limited" and not isinstance(info.value, AgentUnavailableError)
    assert events(setup)[-1]["error_kind"] == "rate_limited"


async def test_the_rate_limit_retry_waits_the_last_delay(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(agent, "RETRY_DELAYS_SECONDS", (2, 5))
    monkeypatch.setattr(agent.asyncio, "sleep", fake_sleep)
    use_openai(monkeypatch, [api_error(429, "rate_limit_exceeded"), text_reply("ok")])
    await run_agent("hi")
    assert slept == [5]


async def test_the_rate_limit_retry_is_separate_from_the_server_error_retries(monkeypatch):
    """A 503 then a 429 then success: both kinds get their own retry."""
    fake = use_openai(monkeypatch, [api_error(503), api_error(429), text_reply("ok")])
    assert await run_agent("hi") == "ok"
    assert fake.calls == 3


async def test_insufficient_quota_is_never_retried_and_says_not_available(monkeypatch, setup):
    fake = use_openai(monkeypatch, [api_error(429, "insufficient_quota"), text_reply("never reached")])
    with pytest.raises(AgentUnavailableError) as info:
        await run_agent("hi")
    assert fake.calls == 1 and info.value.status_code == 429 and info.value.kind == "quota"
    logged = events(setup)
    assert not [e for e in logged if e["event"] == "model_retry"]
    assert logged[-1]["event"] == "request_failed" and logged[-1]["error_kind"] == "quota" and logged[-1]["http_status"] == 429
    assert next(e for e in logged if e["event"] == "model_decision")["error_kind"] == "quota"


# ---------------------------------------------------------------- errors that are not retried

@pytest.mark.parametrize("code", [401, 403, 400])
async def test_client_errors_are_not_retried(monkeypatch, setup, code):
    fake = use_openai(monkeypatch, [api_error(code), text_reply("never reached")])
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.calls == 1  # no retry
    assert not isinstance(info.value, (AgentBusyError, AgentUnavailableError)) and info.value.status_code == code
    assert events(setup)[-1]["http_status"] == code


async def test_timeout_is_not_retried(monkeypatch, setup):
    fake = use_openai(monkeypatch, [openai.APITimeoutError(REQUEST), text_reply("never reached")])
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.calls == 1 and info.value.status_code is None and info.value.kind == "timeout"
    assert events(setup)[-1]["http_status"] is None


async def test_an_unexpected_exception_becomes_an_agent_error(monkeypatch):
    fake = use_openai(monkeypatch, [RuntimeError("boom")])
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.calls == 1 and info.value.kind == "unknown"


async def test_error_text_never_reaches_the_audit_log(monkeypatch, setup):
    response = httpx2.Response(401, request=REQUEST, json={})
    use_openai(monkeypatch, [openai.AuthenticationError("Incorrect API key provided: sk-SECRETSECRETSECRET", response=response, body=None)])
    with pytest.raises(AgentError):
        await run_agent("hi")
    assert "SECRET" not in setup.read_text(encoding="utf-8")


# ---------------------------------------------------------------- the request that is sent

async def test_the_request_has_store_false_the_prompt_the_message_and_the_main_model(monkeypatch):
    fake = use_openai(monkeypatch, [text_reply("Take the train.")])
    await run_agent("Best train from Pune to Goa?")
    request = fake.requests[0]
    assert request["model"] == "main-model"
    assert request["store"] is False
    assert request["instructions"] == build_system_prompt("route").text
    assert request["input"] == [{"role": "user", "content": "Best train from Pune to Goa?"}]
    assert "tool_choice" not in request  # nothing forced: this message names no two cities, and route hides weather and search


async def test_a_forced_first_round_and_an_automatic_second_round(monkeypatch):
    fake = use_openai(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm.")])
    await run_agent("Weather?")  # explore mode: get_weather is one of its forced tools
    first, second = fake.requests
    assert first["tool_choice"] == {"type": "allowed_tools", "mode": "required", "tools": [{"type": "function", "name": "get_weather"}]}
    assert "tool_choice" not in second  # round 2 is automatic
    assert second["store"] is False and len(second["input"]) == 3


async def test_the_model_name_comes_from_settings_only(monkeypatch):
    monkeypatch.setattr(audit.get_settings(), "openai_model", "  some-other-model  ")
    fake = use_openai(monkeypatch, [text_reply("ok")])
    await run_agent("hi")
    assert fake.used_models == ["some-other-model"]  # taken from the setting (trimmed), never a hardcoded name


@pytest.mark.parametrize("value", ["", "   "])
async def test_no_model_configured_is_an_error_and_no_call_is_made(monkeypatch, setup, value):
    monkeypatch.setattr(audit.get_settings(), "openai_model", value)
    fake = use_openai(monkeypatch, [text_reply("never reached")])
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.calls == 0 and info.value.kind == "no_model"


async def test_cached_tokens_are_logged_from_usage(monkeypatch, setup):
    use_openai(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}, cached=1024), text_reply("Warm.", cached=2048)])
    await run_agent("Weather?")
    decisions = [e for e in events(setup) if e["event"] == "model_decision"]
    assert [d["cached_tokens"] for d in decisions] == [1024, 2048]


# ---------------------------------------------------------------- the real client's settings

def test_client_is_built_with_no_sdk_retries_and_the_configured_timeout(monkeypatch):
    monkeypatch.setattr(audit.get_settings(), "openai_timeout_seconds", 42.0)
    agent.get_client.cache_clear()
    try:
        client = agent.get_client()
        assert isinstance(client, openai.AsyncOpenAI)
        assert client.max_retries == 0  # our own loop does the retrying
        assert client.timeout == 42.0
    finally:
        agent.get_client.cache_clear()


async def test_request_id_and_all_token_counts_are_logged_in_model_decision(monkeypatch, setup):
    """Each model_decision carries OpenAI's request id and the input, output, total and cached token counts."""
    first = tool_reply("get_weather", {"location": "Goa"}, cached=1024)
    first._request_id = "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    second = text_reply("Warm.", cached=2048)
    second._request_id = "req_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    use_openai(monkeypatch, [first, second])
    await run_agent("Weather?")
    decisions = [e for e in events(setup) if e["event"] == "model_decision"]
    assert [d["openai_request_id"] for d in decisions] == ["req_" + "a" * 32, "req_" + "b" * 32]
    assert [d["cached_tokens"] for d in decisions] == [1024, 2048]
    assert all((d["input_tokens"], d["output_tokens"], d["total_tokens"]) == (1500, 20, 1520) for d in decisions)


async def test_the_request_id_of_a_failed_call_is_logged_with_the_retry_and_the_failure(monkeypatch, setup):
    """A failed call's request id (from the x-request-id header) is on the model_retry and the failed model_decision."""
    request_id = "req_cccccccccccccccccccccccccccccccc"
    response = httpx2.Response(503, request=REQUEST, headers={"x-request-id": request_id}, json={"error": {"message": "x"}})
    error = openai.InternalServerError("x", response=response, body=None)
    use_openai(monkeypatch, [error, error, error])
    with pytest.raises(AgentBusyError):
        await run_agent("hi")
    logged = events(setup)
    assert [e["openai_request_id"] for e in logged if e["event"] == "model_retry"] == [request_id, request_id]
    assert next(e for e in logged if e["event"] == "model_decision")["openai_request_id"] == request_id


async def test_a_response_without_usage_or_request_id_still_logs_zero_cached_tokens(monkeypatch, setup):
    use_openai(monkeypatch, [text_reply("Hello!")])
    await run_agent("hi")
    decision = next(e for e in events(setup) if e["event"] == "model_decision")
    assert decision["cached_tokens"] == 0 and "openai_request_id" not in decision and "input_tokens" not in decision
