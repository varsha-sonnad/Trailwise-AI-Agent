# test_agent_tools.py: tests for the agent's MCP tool loop.
# Gemini (_generate) and the MCP client are faked, so no key, quota, subprocess or network is used.

from types import SimpleNamespace

import pytest
from google.genai import types

from app.agent import agent
from app.agent.agent import AgentError, MAX_TOOL_ROUNDS, run_agent
from app.agent.mcp_client import McpClientError

# The tool list a real server would report.
TOOLS = [{"name": "get_weather", "description": "Weather", "input_schema": {"type": "object", "properties": {"location": {"type": "string"}}}}]


def text_reply(text):
    """A fake Gemini response that is a final text answer."""
    return SimpleNamespace(function_calls=None, text=text, candidates=None)


def tool_reply(name, args):
    """A fake Gemini response that asks for one tool call."""
    call = SimpleNamespace(name=name, args=args)
    content = types.Content(role="model", parts=[types.Part.from_function_call(name=name, args=args)])
    return SimpleNamespace(function_calls=[call], text=None, candidates=[SimpleNamespace(content=content)])


class FakeMcp:
    """Stand-in for McpClient: records calls and returns a canned result."""

    calls = []
    result = {"ok": True, "temperature_c": 30}
    fail_connect = False
    fail_list = False

    async def __aenter__(self):
        if FakeMcp.fail_connect:
            raise McpClientError("down")
        return self

    async def __aexit__(self, *a):
        return False

    async def list_tools(self):
        if FakeMcp.fail_list:
            raise RuntimeError("boom")
        return TOOLS

    async def call_tool(self, name, arguments):
        FakeMcp.calls.append((name, arguments))
        return FakeMcp.result


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    """Reset the fake MCP client and install it; tests set the scripted Gemini replies."""
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    FakeMcp.result = {"ok": True, "temperature_c": 30}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)


def script_gemini(monkeypatch, replies):
    """Make _generate return the given replies in order; record what it was sent."""
    seen = {"declarations": [], "contents": []}
    it = iter(replies)

    async def fake_generate(contents, declarations):
        seen["declarations"].append(declarations)
        seen["contents"].append(len(contents))
        return next(it)

    monkeypatch.setattr(agent, "_generate", fake_generate)
    return seen


async def test_tool_is_discovered_called_and_result_used(monkeypatch):
    seen = script_gemini(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm in Goa.")])
    assert await run_agent("Weather in Goa?") == "Warm in Goa."
    assert FakeMcp.calls == [("get_weather", {"location": "Goa"})]
    # Gemini was given the discovered tool, and the second round carried the tool exchange.
    assert seen["declarations"][0][0].name == "get_weather"
    assert seen["contents"] == [1, 3]


async def test_unknown_tool_name_is_refused(monkeypatch):
    script_gemini(monkeypatch, [tool_reply("delete_everything", {}), text_reply("Sorry.")])
    assert await run_agent("hi") == "Sorry."
    assert FakeMcp.calls == []  # never reached the MCP server


async def test_tool_error_is_passed_back_and_chat_continues(monkeypatch):
    FakeMcp.result = {"ok": False, "error": "unavailable", "message": "Weather is unavailable right now."}
    script_gemini(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Weather unavailable.")])
    assert await run_agent("Weather?") == "Weather unavailable."


async def test_round_cap_stops_endless_tool_requests(monkeypatch):
    script_gemini(monkeypatch, [tool_reply("get_weather", {"location": "Goa"})] * (MAX_TOOL_ROUNDS + 2))
    with pytest.raises(AgentError):
        await run_agent("loop")
    assert len(FakeMcp.calls) == MAX_TOOL_ROUNDS


async def test_falls_back_to_no_tools_when_mcp_is_down(monkeypatch):
    FakeMcp.fail_connect = True
    seen = script_gemini(monkeypatch, [text_reply("Plain answer.")])
    assert await run_agent("hi") == "Plain answer."
    assert seen["declarations"] == [[]]  # no tools offered


async def test_falls_back_when_discovery_fails(monkeypatch):
    FakeMcp.fail_list = True
    seen = script_gemini(monkeypatch, [text_reply("Plain answer.")])
    assert await run_agent("hi") == "Plain answer."
    assert seen["declarations"] == [[]]


async def test_empty_final_reply_raises(monkeypatch):
    script_gemini(monkeypatch, [text_reply(None)])
    with pytest.raises(AgentError):
        await run_agent("hi")
