# test_mcp_timeouts.py: tests for the per-tool call limits of the MCP client (web_search gets WEB_SEARCH_CALL_TIMEOUT_SECONDS, default
# 150, from a setting), that the limit stays above the search tool's own time budget, and how the agent answers when web_search times out. The MCP session and the
# model call are faked: no key, network or subprocess is used.

import ast
import asyncio
from pathlib import Path

import pytest

from app.agent import agent, mcp_client
from app.agent.agent import NO_LIVE_INFO_REPLY, SEARCH_TIMEOUT_REPLY, run_agent
from app.agent.mcp_client import CALL_TIMEOUT_SECONDS, McpClient, call_limit
from app.config import Settings, get_settings
from tests.test_agent_tools import FakeMcp, script_model, text_reply
from tests.test_tool_choice import tool_list


class FakeSession:
    """Stands in for the MCP ClientSession: records the call, then answers or hangs."""

    def __init__(self, hang=False):
        self.hang, self.calls = hang, []

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        if self.hang:
            await asyncio.sleep(30)
        raise AssertionError("not reached in these tests")


def client_with(session):
    """An McpClient (not started) that uses the fake session."""
    client = McpClient()
    client._session = session
    return client


@pytest.fixture
def limits(monkeypatch):
    """Record the timeout each call_tool gets, and let the real wait_for run with it (the fake call returns at once)."""
    seen = []
    real = asyncio.wait_for

    async def spy(awaitable, timeout):
        seen.append(timeout)
        return await real(awaitable, timeout=0.05)  # never really wait long

    monkeypatch.setattr(mcp_client.asyncio, "wait_for", spy)
    return seen


# ---------------------------------------------------------------- the limits

def test_web_search_gets_150_seconds_by_default_and_other_tools_keep_30():
    assert Settings.model_fields["web_search_call_timeout_seconds"].default == 150.0
    assert call_limit("web_search") == get_settings().web_search_call_timeout_seconds
    assert call_limit("get_weather") == call_limit("geocode_places") == call_limit("anything_else") == CALL_TIMEOUT_SECONDS == 30


def test_the_web_search_limit_follows_the_setting(monkeypatch):
    monkeypatch.setattr(get_settings(), "web_search_call_timeout_seconds", 99.0)
    assert call_limit("web_search") == 99.0 and call_limit("get_weather") == 30


async def test_call_tool_uses_the_per_tool_limit(limits):
    await client_with(FakeSession()).call_tool("web_search", {"query": "x"})
    await client_with(FakeSession()).call_tool("get_weather", {"location": "Goa"})
    await client_with(FakeSession()).call_tool("get_place_images", {"place": "Goa"})
    assert limits == [get_settings().web_search_call_timeout_seconds, 30, 30]


async def test_a_call_that_hangs_gives_a_timeout_error_object(monkeypatch):
    monkeypatch.setattr(get_settings(), "web_search_call_timeout_seconds", 0.05)
    session = FakeSession(hang=True)
    result = await client_with(session).call_tool("web_search", {"query": "x"})
    assert result == {"ok": False, "error": "timeout", "message": "Tool 'web_search' took too long."}


async def test_other_failures_are_still_tool_call_failed():
    result = await client_with(FakeSession()).call_tool("web_search", {"query": "x"})  # the fake session raises AssertionError
    assert result["ok"] is False and result["error"] == "tool_call_failed"


def test_the_agent_limit_is_larger_than_the_search_tools_own_budget():
    """The search tool holds both attempts to (call limit - SAFETY_MARGIN_SECONDS): read the margin from mcp_server/tools/web_search.py."""
    source = (Path(__file__).resolve().parents[2] / "mcp_server" / "tools" / "web_search.py").read_text(encoding="utf-8")
    margin = next(
        n.value.value for n in ast.parse(source).body
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "SAFETY_MARGIN_SECONDS" for t in n.targets)
    )
    assert margin >= 5  # the tool answers "timeout" itself at least 5 s before the agent cuts the call off
    assert get_settings().web_search_call_timeout_seconds - margin > 120  # room for the 120 s search attempt (default)


def test_the_two_new_time_settings_have_the_agreed_defaults_and_limits(monkeypatch):
    from pydantic import ValidationError

    s = Settings(_env_file=None)
    assert (s.web_search_call_timeout_seconds, s.chat_request_timeout_seconds) == (150.0, 180.0)
    assert s.chat_request_timeout_seconds > s.web_search_call_timeout_seconds  # the overall cap is the outermost limit
    for name in ("WEB_SEARCH_CALL_TIMEOUT_SECONDS", "CHAT_REQUEST_TIMEOUT_SECONDS"):
        monkeypatch.setenv(name, "42.5")
        assert Settings(_env_file=None).model_dump()[name.lower()] == 42.5
        for bad in ("0", "-1", "901", "abc"):
            monkeypatch.setenv(name, bad)
            with pytest.raises(ValidationError):
                Settings(_env_file=None)
        monkeypatch.delenv(name)


# ---------------------------------------------------------------- what the agent says when the search times out

MESSAGE = "Tell me about Rishikesh"


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """A fake MCP server offering web_search and get_weather, with the explore required-tools check on."""
    from app.agent import modes

    monkeypatch.setattr(modes, "REQUIRED_TOOLS", {"explore": ("web_search", "get_weather")})
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    monkeypatch.setattr(agent, "McpClient", FakeMcp)

    async def list_tools(self):
        return tool_list("web_search", "get_weather")

    monkeypatch.setattr(FakeMcp, "list_tools", list_tools, raising=False)


def timeout_result(monkeypatch, code="timeout"):
    async def call_tool(self, name, arguments):
        FakeMcp.calls.append(name)
        if name == "web_search":
            return {"ok": False, "error": code, "message": "The web search took too long. Please try again."}
        return {"ok": True, "temperature_c": 30}

    monkeypatch.setattr(FakeMcp, "call_tool", call_tool)


async def test_a_search_timeout_says_it_took_too_long_and_to_try_again(monkeypatch):
    timeout_result(monkeypatch)
    from tests.test_agent_tools import tool_calls_reply

    seen = script_model(monkeypatch, [tool_calls_reply(("web_search", {"query": "Rishikesh"}), ("get_weather", {"location": "Rishikesh"})), text_reply("Never used.")])
    reply = await run_agent(MESSAGE)
    assert reply == SEARCH_TIMEOUT_REPLY == "The live search took too long. Please try again."
    assert reply != NO_LIVE_INFO_REPLY and len(seen["inputs"]) == 1  # no further model call


async def test_the_timeout_reply_is_logged_as_its_own_decision(monkeypatch):
    events = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: events.append((event, fields)))
    timeout_result(monkeypatch)
    from tests.test_agent_tools import tool_calls_reply

    script_model(monkeypatch, [tool_calls_reply(("web_search", {"query": "Rishikesh"}), ("get_weather", {"location": "Rishikesh"})), text_reply("x")])
    await run_agent(MESSAGE)
    assert any(e == "model_decision" and f.get("decision") == "web_search_timeout" for e, f in events)
    tool_call = next(f for e, f in events if e == "tool_call" and f["tool"] == "web_search")
    assert tool_call["error_code"] == "timeout" and "Rishikesh" not in repr(events)  # code and names only


@pytest.mark.parametrize("code", ["unavailable", "quota_exceeded", "tool_call_failed"])
async def test_other_search_failures_keep_the_try_again_reply(monkeypatch, code):
    timeout_result(monkeypatch, code)
    from tests.test_agent_tools import tool_calls_reply

    script_model(monkeypatch, [tool_calls_reply(("web_search", {"query": "Rishikesh"}), ("get_weather", {"location": "Rishikesh"})), text_reply("x")])
    assert await run_agent(MESSAGE) == NO_LIVE_INFO_REPLY
