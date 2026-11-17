# test_model_fallback.py: tests for switching to GEMINI_FALLBACK_MODEL when the main model keeps failing.
# The Gemini client is faked (no key, quota or network) and the retry pauses are set to zero.

import json

import httpx
import pytest

from app.agent import agent
from app.agent.agent import AgentBusyError, AgentError, run_agent
from app.core import audit
from tests.test_agent_tools import FakeMcp, text_reply, tool_reply
from tests.test_gemini_retry import api_error, use_gemini

PRIMARY = "main-model"
FALLBACK = "backup-model"


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    """No real pauses, fixed model names, a fake MCP client, and the audit log in a temp file."""
    monkeypatch.setattr(agent, "RETRY_DELAYS_SECONDS", (0, 0))
    settings = audit.get_settings()
    monkeypatch.setattr(settings, "gemini_model", PRIMARY)
    monkeypatch.setattr(settings, "gemini_fallback_model", FALLBACK)
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / "audit.log"))
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    FakeMcp.result = {"ok": True, "temperature_c": 30}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    audit.reset_audit_logger()
    yield tmp_path / "audit.log"
    audit.reset_audit_logger()


def events(path):
    """Read the audit file back as a list of dicts."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_fallback_is_used_after_the_main_model_keeps_failing(monkeypatch, setup):
    fake = use_gemini(monkeypatch, [api_error(503)] * 3 + [text_reply("From the backup.")])
    assert await run_agent("hi") == "From the backup."
    # 1 try + 2 retries on the main model, then one call on the fallback.
    assert fake.used_models == [PRIMARY] * 3 + [FALLBACK]
    switch = next(e for e in events(setup) if e["event"] == "model_fallback")
    assert switch["http_status"] == 503 and switch["from_model"] == PRIMARY and switch["to_model"] == FALLBACK


@pytest.mark.parametrize("code", [500, 504])
async def test_other_server_errors_also_trigger_the_fallback(monkeypatch, code):
    fake = use_gemini(monkeypatch, [api_error(code, "INTERNAL")] * 3 + [text_reply("ok")])
    assert await run_agent("hi") == "ok"
    assert fake.used_models[-1] == FALLBACK


async def test_later_tool_rounds_stay_on_the_fallback(monkeypatch):
    # Round 1 switches to the fallback and asks for a tool; round 2 must not go back to the main model.
    fake = use_gemini(
        monkeypatch,
        [api_error(503)] * 3 + [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm.")],
    )
    assert await run_agent("Weather?") == "Warm."
    assert fake.used_models == [PRIMARY] * 3 + [FALLBACK, FALLBACK]


async def test_each_request_starts_on_the_main_model(monkeypatch):
    fake = use_gemini(monkeypatch, [api_error(503)] * 3 + [text_reply("one"), text_reply("two")])
    assert await run_agent("first") == "one"
    assert await run_agent("second") == "two"
    assert fake.used_models == [PRIMARY] * 3 + [FALLBACK, PRIMARY]


@pytest.mark.parametrize("code,status", [(429, "RESOURCE_EXHAUSTED"), (401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED"), (400, "INVALID_ARGUMENT")])
async def test_client_errors_do_not_trigger_the_fallback(monkeypatch, code, status):
    fake = use_gemini(monkeypatch, [api_error(code, status), text_reply("never reached")])
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.used_models == [PRIMARY] and info.value.status_code == code


async def test_timeout_does_not_trigger_the_fallback(monkeypatch):
    fake = use_gemini(monkeypatch, [httpx.ReadTimeout("slow"), text_reply("never reached")])
    with pytest.raises(AgentError):
        await run_agent("hi")
    assert fake.used_models == [PRIMARY]


@pytest.mark.parametrize("fallback", ["", "   ", PRIMARY])
async def test_no_fallback_when_unset_or_same_as_main(monkeypatch, fallback):
    monkeypatch.setattr(audit.get_settings(), "gemini_fallback_model", fallback)
    fake = use_gemini(monkeypatch, [api_error(503)] * 3)
    with pytest.raises(AgentBusyError):
        await run_agent("hi")
    assert fake.used_models == [PRIMARY] * 3


async def test_both_models_failing_gives_the_busy_error(monkeypatch, setup):
    fake = use_gemini(monkeypatch, [api_error(503)] * 6)
    with pytest.raises(AgentBusyError) as info:
        await run_agent("hi")
    assert fake.used_models == [PRIMARY] * 3 + [FALLBACK] * 3 and info.value.status_code == 503
    assert events(setup)[-1]["event"] == "request_failed" and events(setup)[-1]["http_status"] == 503
