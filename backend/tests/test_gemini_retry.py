# test_gemini_retry.py: tests for retrying temporary Gemini failures in agent._generate.
# The Gemini client is faked (no key, quota or network) and the pauses are set to zero.

import json

import httpx
import pytest
from google.genai import errors

from app.agent import agent
from app.agent.agent import AgentBusyError, AgentError, run_agent
from app.core import audit
from tests.test_agent_tools import FakeMcp, text_reply


def api_error(code, status="UNAVAILABLE"):
    """A Gemini SDK error with the given HTTP code (ServerError for 5xx, ClientError for 4xx)."""
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": "fake", "status": status}})


class FakeGemini:
    """Stand-in for the Gemini client: plays back scripted results (an exception is raised, else returned)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        # The model name each call asked for, in order.
        self.used_models = []
        # Mirrors client.aio.models.generate_content.
        self.aio = self
        self.models = self

    async def generate_content(self, **kwargs):
        self.calls += 1
        self.used_models.append(kwargs.get("model"))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    """No real pauses, a fake MCP client, and the audit log in a temp file."""
    monkeypatch.setattr(agent, "RETRY_DELAYS_SECONDS", (0, 0))
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    settings = audit.get_settings()
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / "audit.log"))
    audit.reset_audit_logger()
    yield tmp_path / "audit.log"
    audit.reset_audit_logger()


def use_gemini(monkeypatch, script):
    """Install a fake Gemini client with the scripted results and return it."""
    fake = FakeGemini(script)
    monkeypatch.setattr(agent, "get_client", lambda: fake)
    return fake


def events(path):
    """Read the audit file back as a list of dicts."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_retries_503_then_succeeds(monkeypatch, setup):
    fake = use_gemini(monkeypatch, [api_error(503), api_error(503), text_reply("Hello!")])
    assert await run_agent("hi") == "Hello!"
    assert fake.calls == 3
    retries = [e for e in events(setup) if e["event"] == "model_retry"]
    assert [(e["attempt"], e["http_status"]) for e in retries] == [(1, 503), (2, 503)]


@pytest.mark.parametrize("code", [500, 504])
async def test_other_server_errors_are_retried(monkeypatch, code):
    fake = use_gemini(monkeypatch, [api_error(code, "INTERNAL"), text_reply("ok")])
    assert await run_agent("hi") == "ok"
    assert fake.calls == 2


async def test_gives_up_after_retries_with_busy_error(monkeypatch, setup):
    fake = use_gemini(monkeypatch, [api_error(503)] * 3)
    with pytest.raises(AgentBusyError) as info:
        await run_agent("hi")
    assert fake.calls == 3 and info.value.status_code == 503  # 1 try + 2 retries
    # The audit log now shows the status code.
    last = events(setup)[-1]
    assert last["event"] == "request_failed" and last["http_status"] == 503
    failed_round = next(e for e in events(setup) if e["event"] == "model_decision")
    assert failed_round["http_status"] == 503


@pytest.mark.parametrize("code,status", [(429, "RESOURCE_EXHAUSTED"), (401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED"), (400, "INVALID_ARGUMENT")])
async def test_client_errors_are_not_retried(monkeypatch, setup, code, status):
    fake = use_gemini(monkeypatch, [api_error(code, status), text_reply("never reached")])
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.calls == 1  # no retry
    assert not isinstance(info.value, AgentBusyError) and info.value.status_code == code
    assert events(setup)[-1]["http_status"] == code


async def test_timeout_is_not_retried(monkeypatch, setup):
    fake = use_gemini(monkeypatch, [httpx.ReadTimeout("slow"), text_reply("never reached")])
    with pytest.raises(AgentError) as info:
        await run_agent("hi")
    assert fake.calls == 1 and info.value.status_code is None
    assert events(setup)[-1]["http_status"] is None


async def test_timeout_setting_is_20_seconds():
    assert agent.REQUEST_TIMEOUT_MS == 20_000
