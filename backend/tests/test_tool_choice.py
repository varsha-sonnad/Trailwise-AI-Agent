# test_tool_choice.py: tests for forcing a tool on round 1 (tool_choice allowed_tools, mode "required"), automatic
# choice afterwards (no tool_choice sent), the greeting and email exceptions, and the "could not check live
# information" fallback. The model call (_generate) and the MCP client are faked: no key, quota or network is used.

import pytest

from app.agent import agent
from app.agent.agent import NO_LIVE_INFO_REPLY, run_agent
from app.agent.modes import is_small_talk
from tests.test_agent_tools import FakeMcp, text_reply, tool_calls_reply, tool_reply

ALL_TOOLS = ["web_search", "get_weather", "geocode_places", "get_place_images", "get_route", "send_trip_email", "search_buses"]


def tool_list(*names):
    """The tool list an MCP server would report, for the given tool names."""
    return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in names]


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    """Install the fake MCP client with all tools offered; tests change FakeMcp.offered when needed."""
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    FakeMcp.result = {"ok": True, "temperature_c": 30}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)

    async def list_tools(self):
        return tool_list(*FakeMcp.offered)

    FakeMcp.offered = ALL_TOOLS
    monkeypatch.setattr(FakeMcp, "list_tools", list_tools, raising=False)


def script(monkeypatch, replies):
    """Make _generate return the replies in order; record each call's tool_choice."""
    choices = []
    it = iter(replies)

    async def fake_generate(input_items, tools, system_prompt, tool_choice=None):
        choices.append(tool_choice)
        return next(it)

    monkeypatch.setattr(agent, "_generate", fake_generate)
    return choices


def settings_of(choice):
    """(mode, allowed names) of a recorded tool_choice."""
    assert choice["type"] == "allowed_tools"
    return choice["mode"], [t["name"] for t in choice["tools"]]


async def test_round_one_is_forced_with_the_modes_tools_then_auto(monkeypatch):
    choices = script(monkeypatch, [tool_reply("web_search", {"query": "x"}), text_reply("Done.")])
    await run_agent("Where should I go in winter?")  # explore mode
    assert settings_of(choices[0]) == ("required", ["web_search", "get_weather", "geocode_places", "get_place_images"])
    assert choices[1] is None  # later rounds send no tool_choice (automatic), so the model can write the final answer


async def test_forced_tools_are_function_tools_listed_by_name_only(monkeypatch):
    choices = script(monkeypatch, [text_reply("ok")])
    await run_agent("Where should I go in winter?")
    assert all(t == {"type": "function", "name": t["name"]} for t in choices[0]["tools"])


@pytest.mark.parametrize("message, allowed", [
    ("Best train from Pune to Goa?", ["geocode_places", "search_buses"]),  # route (search_trains is not in ALL_TOOLS here)
    ("Plan 3 days in Jaipur", ["get_weather", "web_search", "geocode_places", "get_route"]),  # itinerary
])
async def test_each_mode_has_its_own_allowed_tools(monkeypatch, message, allowed):
    choices = script(monkeypatch, [text_reply("ok")])
    await run_agent(message)
    assert settings_of(choices[0]) == ("required", allowed)


async def test_allowed_tools_are_limited_to_those_discovered(monkeypatch):
    FakeMcp.offered = ["get_weather", "send_trip_email"]
    choices = script(monkeypatch, [text_reply("ok")])
    await run_agent("Where should I go in winter?")
    assert settings_of(choices[0]) == ("required", ["get_weather"])  # no web_search, no email tool


async def test_not_forced_when_none_of_the_modes_tools_exist(monkeypatch):
    FakeMcp.offered = ["send_trip_email", "search_buses"]
    choices = script(monkeypatch, [text_reply("ok")])
    await run_agent("Where should I go in winter?")
    assert choices == [None]


async def test_email_mode_is_never_forced(monkeypatch):
    choices = script(monkeypatch, [text_reply("Here is the draft.")])
    await run_agent("Email me this plan")
    assert choices == [None]


async def test_no_tools_discovered_means_no_tool_choice(monkeypatch):
    FakeMcp.offered = []
    choices = script(monkeypatch, [text_reply("ok")])
    await run_agent("Where should I go in winter?")
    assert choices == [None]


async def test_no_tool_choice_when_the_mcp_server_is_down(monkeypatch):
    FakeMcp.fail_connect = True
    choices = script(monkeypatch, [text_reply("ok")])
    await run_agent("Where should I go in winter?")
    assert choices == [None]


@pytest.mark.parametrize("message", ["hi", "Hello!", "hey", "Thanks.", "thank you", "  OK  ", "yes", "No", "bye!"])
async def test_greetings_and_confirmations_are_not_forced(monkeypatch, message):
    assert is_small_talk(message)
    choices = script(monkeypatch, [text_reply("Hello!")])
    await run_agent(message)
    assert choices == [None]


@pytest.mark.parametrize("message", ["hi there", "yes please plan goa", "thanks for the trip ideas in Goa", ""])
def test_only_a_whole_greeting_counts(message):
    assert not is_small_talk(message)


async def test_all_tools_failing_on_a_forced_round_gives_the_fallback(monkeypatch):
    FakeMcp.result = {"ok": False, "error": "unavailable", "message": "down"}
    choices = script(monkeypatch, [tool_reply("web_search", {"query": "x"}), text_reply("From memory: Goa is great.")])
    reply = await run_agent("Where should I go in winter?")
    assert reply == NO_LIVE_INFO_REPLY
    assert len(choices) == 1  # the model was not asked again, so nothing can come from memory


async def test_an_unknown_tool_on_a_forced_round_also_gives_the_fallback(monkeypatch):
    choices = script(monkeypatch, [tool_reply("delete_everything", {}), text_reply("From memory.")])
    assert await run_agent("Where should I go in winter?") == NO_LIVE_INFO_REPLY
    assert len(choices) == 1


async def test_unreadable_arguments_on_a_forced_round_also_give_the_fallback(monkeypatch):
    choices = script(monkeypatch, [tool_reply("web_search", None, raw_arguments="{broken"), text_reply("From memory.")])
    assert await run_agent("Where should I go in winter?") == NO_LIVE_INFO_REPLY
    assert len(choices) == 1 and FakeMcp.calls == []


async def test_one_working_tool_is_enough_to_carry_on(monkeypatch):
    results = iter([{"ok": False, "error": "unavailable"}, {"ok": True, "temperature_c": 30}])

    async def call_tool(self, name, arguments):
        return next(results)

    monkeypatch.setattr(FakeMcp, "call_tool", call_tool)
    choices = script(monkeypatch, [
        tool_calls_reply(("web_search", {}), ("get_weather", {})),
        text_reply("Warm. Search was unavailable."),
    ])
    assert await run_agent("Where should I go in winter?") == "Warm. Search was unavailable."
    assert len(choices) == 2


async def test_failing_tools_on_a_not_forced_round_do_not_trigger_the_fallback(monkeypatch):
    FakeMcp.result = {"ok": False, "error": "unavailable", "message": "down"}
    script(monkeypatch, [tool_reply("get_weather", {}), text_reply("Sorry, weather is unavailable.")])
    assert await run_agent("hi") == "Sorry, weather is unavailable."  # a greeting is never forced


async def test_tool_choice_and_tools_called_are_logged_without_message_text(monkeypatch):
    logged = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: logged.append((event, fields)))
    script(monkeypatch, [tool_reply("web_search", {"query": "secret place"}), text_reply("Fine.")])
    await run_agent("Where should I go in winter near Secretville?")
    decisions = [f for e, f in logged if e == "model_decision"]
    assert [d["tool_choice"] for d in decisions] == ["required", "auto"]
    assert decisions[0]["tools_called"] == ["web_search"] and decisions[1]["tools_called"] == ["web_search"]
    assert "Secretville" not in repr(logged) and "secret place" not in repr(logged)


async def test_fallback_is_logged(monkeypatch):
    logged = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: logged.append((event, status, fields)))
    FakeMcp.result = {"ok": False, "error": "unavailable"}
    script(monkeypatch, [tool_reply("web_search", {})])
    await run_agent("Where should I go in winter?")
    assert any(e == "model_decision" and f.get("decision") == "all_tools_failed" for e, _, f in logged)


async def test_generate_sends_tool_choice_tools_and_store_false(monkeypatch):
    """The real _generate puts the tools, the tool_choice, the instructions and store=False into the request."""
    sent = {}

    async def fake_call_model(model, request):
        sent["request"] = request
        return text_reply("ok")

    monkeypatch.setattr(agent, "_call_model", fake_call_model)
    tools = agent.to_tools(tool_list("geocode_places"))
    choice, label = agent._tool_choice("route", "Best train from Pune to Goa?", ["geocode_places"], 1)
    await agent._generate([{"role": "user", "content": "x"}], tools, "the prompt", choice)
    request = sent["request"]
    assert label == "required" and settings_of(request["tool_choice"]) == ("required", ["geocode_places"])
    assert request["store"] is False and request["instructions"] == "the prompt"
    assert request["input"] == [{"role": "user", "content": "x"}]
    assert request["tools"][0]["name"] == "geocode_places" and request["tools"][0]["strict"] is False


async def test_generate_sends_neither_tools_nor_tool_choice_when_there_are_no_tools(monkeypatch):
    """No tools means no `tools` and no `tool_choice` field at all (the API rejects a tool_choice without tools)."""
    sent = {}

    async def fake_call_model(model, request):
        sent["request"] = request
        return text_reply("ok")

    monkeypatch.setattr(agent, "_call_model", fake_call_model)
    await agent._generate([{"role": "user", "content": "x"}], [], "the prompt", None)
    assert "tools" not in sent["request"] and "tool_choice" not in sent["request"]
    assert sent["request"]["store"] is False
