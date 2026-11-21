# test_agent_tools.py: tests for the agent's MCP tool loop.
# The model call (_generate) and the MCP client are faked, so no key, quota, subprocess or network is used.
# The fake replies are real OpenAI SDK Response objects, so the agent reads them exactly as it reads real ones.

import itertools
import json

import pytest
from openai.types.responses import Response

from app.agent import agent
from app.agent.agent import AgentError, MAX_TOOL_ROUNDS, run_agent
from app.agent.mcp_client import McpClientError

# The tool list a real server would report.
TOOLS = [{"name": "get_weather", "description": "Weather", "input_schema": {"type": "object", "properties": {"location": {"type": "string"}}}}]


_call_ids = itertools.count(1)  # each fake tool call gets its own call_id


def make_response(output, cached=None):
    """A real SDK Response with the given output items (and a usage block when `cached` is a token count)."""
    data = {
        "id": "resp_test", "object": "response", "created_at": 0, "model": "test-model", "status": "completed",
        "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    }
    if cached is not None:
        data["usage"] = {
            "input_tokens": 1500, "output_tokens": 20, "total_tokens": 1520,
            "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    return Response.model_validate(data)


def text_reply(text, cached=None):
    """A fake model response that is a final text answer (text=None gives an empty reply)."""
    output = [] if text is None else [{
        "type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }]
    return make_response(output, cached)


def call_item(name, args, raw_arguments=None):
    """One function_call output item; raw_arguments replaces the JSON text (to simulate broken JSON)."""
    arguments = raw_arguments if raw_arguments is not None else json.dumps(args)
    return {"type": "function_call", "call_id": f"call_{next(_call_ids)}", "name": name, "arguments": arguments}


def tool_reply(name, args, cached=None, raw_arguments=None):
    """A fake model response that asks for one tool call."""
    return make_response([call_item(name, args, raw_arguments)], cached)


def tool_calls_reply(*calls):
    """A fake model response that asks for several tool calls at once: pass (name, args) pairs."""
    return make_response([call_item(name, args) for name, args in calls])


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
    """Reset the fake MCP client and install it; tests set the scripted model replies."""
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    FakeMcp.result = {"ok": True, "temperature_c": 30}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)


def script_model(monkeypatch, replies):
    """Make _generate return the given replies in order; record what it was sent."""
    seen = {"tools": [], "inputs": [], "tool_choices": []}
    it = iter(replies)

    async def fake_generate(input_items, tools, system_prompt, tool_choice=None):
        seen["tool_choices"].append(tool_choice)
        seen["tools"].append(tools)
        seen["inputs"].append(list(input_items))  # a copy: the agent keeps adding to its own list
        return next(it)

    monkeypatch.setattr(agent, "_generate", fake_generate)
    return seen


async def test_tool_is_discovered_called_and_result_used(monkeypatch):
    seen = script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm in Goa.")])
    assert await run_agent("Weather in Goa?") == "Warm in Goa."
    assert FakeMcp.calls == [("get_weather", {"location": "Goa"})]
    # The model was given the discovered tool (not strict), and the second round carried the tool exchange.
    assert seen["tools"][0][0]["name"] == "get_weather" and seen["tools"][0][0]["strict"] is False
    assert [len(i) for i in seen["inputs"]] == [1, 3]
    call, output = seen["inputs"][1][1], seen["inputs"][1][2]
    # The tool request goes back without an item id (nothing is stored at OpenAI), and the result answers its call_id.
    assert call["type"] == "function_call" and call["name"] == "get_weather" and "id" not in call
    assert json.loads(call["arguments"]) == {"location": "Goa"}
    assert output["type"] == "function_call_output" and output["call_id"] == call["call_id"]
    assert json.loads(output["output"]) == {"ok": True, "temperature_c": 30}


async def test_unknown_tool_name_is_refused(monkeypatch):
    script_model(monkeypatch, [tool_reply("delete_everything", {}), text_reply("Sorry.")])
    assert await run_agent("hi") == "Sorry."
    assert FakeMcp.calls == []  # never reached the MCP server


async def test_tool_error_on_a_forced_round_gives_the_fallback_reply(monkeypatch):
    FakeMcp.result = {"ok": False, "error": "unavailable", "message": "Weather is unavailable right now."}
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Weather unavailable.")])
    # "Weather?" is explore mode, so round 1 is forced: all tools failing now gives the fixed fallback reply,
    # not the model's own text (see test_tool_choice.py for the not-forced case, where the chat carries on).
    assert await run_agent("Weather?") == agent.NO_LIVE_INFO_REPLY


async def test_round_cap_stops_endless_tool_requests(monkeypatch):
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"})] * (MAX_TOOL_ROUNDS + 2))
    with pytest.raises(AgentError):
        await run_agent("loop")
    assert len(FakeMcp.calls) == MAX_TOOL_ROUNDS


async def test_falls_back_to_no_tools_when_mcp_is_down(monkeypatch):
    FakeMcp.fail_connect = True
    seen = script_model(monkeypatch, [text_reply("Plain answer.")])
    assert await run_agent("hi") == "Plain answer."
    assert seen["tools"] == [[]] and seen["tool_choices"] == [None]  # no tools, no tool_choice


async def test_falls_back_when_discovery_fails(monkeypatch):
    FakeMcp.fail_list = True
    seen = script_model(monkeypatch, [text_reply("Plain answer.")])
    assert await run_agent("hi") == "Plain answer."
    assert seen["tools"] == [[]] and seen["tool_choices"] == [None]


async def test_empty_final_reply_raises(monkeypatch):
    script_model(monkeypatch, [text_reply(None)])
    with pytest.raises(AgentError):
        await run_agent("hi")


async def test_unreadable_arguments_do_not_call_the_tool(monkeypatch):
    """Broken JSON in a tool call is not run: the model gets a bad_arguments result and can carry on."""
    seen = script_model(monkeypatch, [tool_reply("get_weather", None, raw_arguments='{"location": '), text_reply("Sorry.")])
    assert await run_agent("hi") == "Sorry."  # "hi" is a greeting, so the round was not forced
    assert FakeMcp.calls == []
    output = json.loads(seen["inputs"][1][2]["output"])
    assert output["ok"] is False and output["error"] == "bad_arguments"


async def test_two_calls_in_one_round_are_both_answered(monkeypatch):
    """Each call gets its own function_call_output with its own call_id."""
    seen = script_model(monkeypatch, [tool_calls_reply(("get_weather", {"location": "Goa"}), ("get_weather", {"location": "Pune"})), text_reply("Both warm.")])
    assert await run_agent("hi") == "Both warm."
    items = seen["inputs"][1]
    calls = [i for i in items if i.get("type") == "function_call"]
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert len(calls) == len(outputs) == 2
    assert [c["call_id"] for c in calls] == [o["call_id"] for o in outputs]
