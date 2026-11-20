# test_force_web_search.py: tests for forcing web_search as the FIRST model call, so the model never answers from its own
# knowledge: which messages are forced, what is sent, what happens when the model ignores it or the search fails, the
# clarifying-question guard that now follows the forced search, and the prompt rules. The model call (_generate) and the
# MCP client are faked, so no key, quota or network is used.

import pytest

from app.agent import agent, modes
from app.agent.agent import MAX_TOOL_ROUNDS, NO_LIVE_INFO_REPLY, SEARCH_NOT_SET_UP_REPLY, SEARCH_TIMEOUT_REPLY, run_agent
from app.agent.modes import REQUIRED_TOOLS, build_system_prompt
from tests.test_agent_tools import FakeMcp, script_model, text_reply, tool_calls_reply, tool_reply
from tests.test_tool_choice import tool_list

OFFERED = ["web_search", "get_weather", "geocode_places", "get_place_images", "get_route", "search_buses", "search_trains", "send_trip_email"]
SEARCH = ("web_search", {"query": "Kedarnath"})
GEOCODE = ("geocode_places", {"names": ["Kedarnath"], "context": "Uttarakhand, India"})
IMAGES = ("get_place_images", {"place": "Kedarnath", "region": "Uttarakhand, India"})
WEATHER = ("get_weather", {"lat": 30.7352, "lng": 79.0669})
PLACE = {"name": "Kedarnath", "lat": 30.7352, "lng": 79.0669}
FORCED_WEB_SEARCH = {"type": "allowed_tools", "mode": "required", "tools": [{"type": "function", "name": "web_search"}]}


class Env:
    offered = OFFERED
    results: dict = {}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """The required-tools check ON (conftest switches it off), and a fake MCP server."""
    monkeypatch.setattr(modes, "REQUIRED_TOOLS", REQUIRED_TOOLS)
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    Env.offered, Env.results = list(OFFERED), {}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)

    async def list_tools(self):
        return tool_list(*Env.offered)

    async def call_tool(self, name, arguments):
        FakeMcp.calls.append((name, arguments))
        return Env.results.get(name, {"ok": True, "data": name})

    monkeypatch.setattr(FakeMcp, "list_tools", list_tools, raising=False)
    monkeypatch.setattr(FakeMcp, "call_tool", call_tool)


def full_run(extra_final="Done."):
    """A model that does everything right: search first, then geocode and photos, then weather, then the answer."""
    return [tool_reply(*SEARCH), tool_calls_reply(GEOCODE, IMAGES), tool_reply(*WEATHER), text_reply(extra_final)]


# ---------------------------------------------------------------- the first call is web_search and nothing else

@pytest.mark.parametrize("message", ["Tell me about Kedarnath", "hmm interesting", "weather in Goa?"])
async def test_the_first_call_is_forced_to_be_web_search_only(monkeypatch, message):
    model = script_model(monkeypatch, full_run())
    await run_agent(message)
    assert model["tool_choices"][0] == FORCED_WEB_SEARCH  # allowed_tools, mode required, one tool


async def test_only_the_first_call_is_forced_the_later_rounds_are_automatic(monkeypatch):
    model = script_model(monkeypatch, full_run())
    assert await run_agent("Tell me about Kedarnath") == "Done."
    assert model["tool_choices"] == [FORCED_WEB_SEARCH, None, None, None]
    assert [n for n, _ in FakeMcp.calls] == ["web_search", "geocode_places", "get_place_images", "get_weather"]  # the search ran first


async def test_the_audit_label_for_the_forced_round_is_required(monkeypatch):
    events = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: events.append((event, fields)))
    script_model(monkeypatch, full_run())
    await run_agent("Tell me about Kedarnath")
    labels = [f["tool_choice"] for e, f in events if e == "model_decision" and "tool_choice" in f]
    assert labels[0] == "required" and set(labels[1:]) == {"auto"}


async def test_follow_up_questions_with_memory_are_forced_too(monkeypatch):
    model = script_model(monkeypatch, full_run())
    history = [{"role": "user", "content": "Tell me about Kedarnath"}, {"role": "assistant", "content": "A pilgrimage town."}]
    await run_agent("What about the weather there?", history=history, last_mode="explore", last_place=PLACE)
    assert model["tool_choices"][0] == FORCED_WEB_SEARCH  # even though the answer might be in the history: no model knowledge


@pytest.mark.parametrize("message", ["hi", "Hello!", "thanks", "ok", "yes", "bye"])
async def test_a_greeting_or_short_confirmation_is_not_forced(monkeypatch, message):
    model = script_model(monkeypatch, [text_reply("Hello!")])
    assert await run_agent(message) == "Hello!"
    assert model["tool_choices"] == [None] and len(model["inputs"]) == 1


async def test_email_mode_is_not_forced(monkeypatch):
    model = script_model(monkeypatch, [text_reply("Here is a draft.")])
    await run_agent("Email me this plan")
    assert model["tool_choices"] == [None]


async def test_without_web_search_on_the_server_nothing_is_forced_to_it(monkeypatch):
    Env.offered = ["get_weather", "geocode_places"]
    model = script_model(monkeypatch, [text_reply("Plain answer."), tool_calls_reply(GEOCODE, ("get_weather", {"location": "Kedarnath"})), text_reply("Done.")])
    await run_agent("Tell me about Kedarnath")
    assert model["tool_choices"][0] is None  # there is no web_search tool to force; the other required tools are forced afterwards
    assert [t["name"] for t in model["tool_choices"][1]["tools"]] == ["get_weather", "geocode_places"]


async def test_no_tools_at_all_means_no_tool_choice(monkeypatch):
    Env.offered = []
    model = script_model(monkeypatch, [text_reply("Plain answer.")])
    await run_agent("Tell me about Kedarnath")
    assert model["tool_choices"] == [None]


async def test_the_forced_choice_really_goes_to_the_model_in_the_request(monkeypatch):
    """The real _generate: tool_choice is in the request with only web_search allowed, and the tools are all there."""
    sent = []

    async def fake_call_model(model, request):
        sent.append(request)
        return tool_reply(*SEARCH) if len(sent) == 1 else text_reply("Done.")

    monkeypatch.setattr(agent, "_call_model", fake_call_model)
    Env.offered = ["web_search"]
    await run_agent("Tell me about Kedarnath")
    assert sent[0]["tool_choice"] == FORCED_WEB_SEARCH and [t["name"] for t in sent[0]["tools"]] == ["web_search"]
    assert "tool_choice" not in sent[1]


# ---------------------------------------------------------------- no model knowledge: ignoring it or a failed search

async def test_if_the_model_ignores_the_forced_search_its_text_is_dropped_and_the_search_is_forced_again(monkeypatch):
    model = script_model(monkeypatch, [
        text_reply("Kedarnath is at 3,583 m (from my own memory)."),   # ignored the forced search
        tool_calls_reply(SEARCH, GEOCODE, IMAGES, ("get_weather", {"location": "Kedarnath"})),
        text_reply("From the tools."),
    ])
    assert await run_agent("Tell me about Kedarnath") == "From the tools."
    assert [t["name"] for t in model["tool_choices"][1]["tools"]][0] == "web_search"  # the extra round forces web_search among the missing
    assert "my own memory" not in repr(model["inputs"][1:])  # the draft never reaches the model again, nor the user


async def test_a_failed_search_gives_a_fixed_reply_and_never_an_answer_from_memory(monkeypatch):
    Env.results["web_search"] = {"ok": False, "error": "no_results", "message": "x"}
    model = script_model(monkeypatch, [tool_reply(*SEARCH), text_reply("From memory.")])
    reply = await run_agent("Tell me about Kedarnath")
    assert reply == NO_LIVE_INFO_REPLY and len(model["inputs"]) == 1  # the model was not asked again


@pytest.mark.parametrize("code, expected", [("timeout", SEARCH_TIMEOUT_REPLY), ("not_configured", SEARCH_NOT_SET_UP_REPLY), ("quota_exceeded", NO_LIVE_INFO_REPLY)])
async def test_the_fixed_replies_for_other_search_errors(monkeypatch, code, expected):
    Env.results["web_search"] = {"ok": False, "error": code, "message": "x"}
    script_model(monkeypatch, [tool_reply(*SEARCH), text_reply("From memory.")])
    assert await run_agent("Tell me about Kedarnath") == expected


async def test_the_other_tools_cannot_be_called_in_the_forced_round_but_a_misbehaving_model_is_still_checked(monkeypatch):
    """A model that asks for other tools in round 1 anyway (the API would not allow it) is still held to the search."""
    Env.results["web_search"] = {"ok": False, "error": "unavailable", "message": "x"}
    script_model(monkeypatch, [tool_calls_reply(SEARCH, GEOCODE), text_reply("From memory.")])
    assert await run_agent("Tell me about Kedarnath") == NO_LIVE_INFO_REPLY


# ---------------------------------------------------------------- the clarifying-question guard after the forced search

async def test_a_short_question_after_the_forced_search_is_returned_without_forcing_the_other_tools(monkeypatch):
    events = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: events.append((event, fields)))
    model = script_model(monkeypatch, [tool_reply("web_search", {"query": "trip ideas"}), text_reply("Which place do you want to visit?")])
    assert await run_agent("Plan a trip") == "Which place do you want to visit?"
    assert len(model["inputs"]) == 2 and [n for n, _ in FakeMcp.calls] == ["web_search"]  # no extra round
    assert [f["reason"] for e, f in events if f.get("decision") == "guard_skipped"] == ["clarifying_question"]


async def test_a_question_straight_away_still_skips_the_check(monkeypatch):
    model = script_model(monkeypatch, [text_reply("Where to?")])
    assert await run_agent("Plan a trip") == "Where to?" and len(model["inputs"]) == 1


async def test_a_question_after_another_tool_was_used_is_not_skipped(monkeypatch):
    model = script_model(monkeypatch, [
        tool_reply(*SEARCH), tool_reply(*GEOCODE), text_reply("Which month?"),
        tool_calls_reply(IMAGES, ("get_weather", {"location": "Kedarnath"})), text_reply("Final."),
    ])
    assert await run_agent("Tell me about Kedarnath") == "Final."
    assert model["tool_choices"][3]["mode"] == "required"  # the missing tools were forced


async def test_a_known_last_place_means_the_question_is_not_skipped(monkeypatch):
    model = script_model(monkeypatch, [
        tool_reply(*SEARCH), text_reply("Which place do you mean?"),
        tool_calls_reply(GEOCODE, IMAGES, ("get_weather", {"location": "Kedarnath"})), text_reply("Final."),
    ])
    assert await run_agent("What about it?", last_place=PLACE) == "Final."
    assert len(model["inputs"]) == 4


# ---------------------------------------------------------------- the switches and the number of rounds

async def test_the_switch_turns_the_forcing_off(monkeypatch):
    monkeypatch.setattr(agent, "FORCE_WEB_SEARCH_FIRST", False)
    model = script_model(monkeypatch, full_run())
    await run_agent("Tell me about Kedarnath")
    assert model["tool_choices"][0] is None


def test_the_forcing_is_on_by_default():
    assert agent.FORCE_WEB_SEARCH_FIRST is True


async def test_the_normal_flow_fits_in_the_round_cap_with_room_for_an_extra_round(monkeypatch):
    """search, geocode and photos, a draft that skips the weather, the forced weather, the answer: five of the six rounds."""
    assert MAX_TOOL_ROUNDS == 6
    model = script_model(monkeypatch, [
        tool_reply(*SEARCH), tool_calls_reply(GEOCODE, IMAGES), text_reply("Draft without the weather."),
        tool_reply(*WEATHER), text_reply("Final."),
    ])
    assert await run_agent("Tell me about Kedarnath") == "Final." and len(model["inputs"]) == 5


# ---------------------------------------------------------------- the prompts match the new flow

@pytest.mark.parametrize("mode", ["explore", "itinerary"])
def test_the_prompts_say_the_app_runs_web_search_first_and_the_model_uses_only_what_the_tools_return(mode):
    text = build_system_prompt(mode).text
    assert "The app always runs `web_search` first, before you write anything" in text
    assert "use only what they and the other tools return, never your own knowledge" in text
    assert "do not search the web again for the same question" in text


def test_the_prompts_no_longer_ask_for_web_search_together_with_the_other_tools_in_the_first_round():
    for mode in ("explore", "itinerary"):
        line = next(l for l in build_system_prompt(mode).text.splitlines() if "together in the same round" in l)
        assert "call `web_search`" not in line and "`web_search` and `geocode_places`" not in line and "`geocode_places` and `web_search`" not in line


def test_asking_a_question_first_is_still_allowed_and_does_not_repeat_the_search():
    text = build_system_prompt("itinerary").text
    assert "ask your short question first and call no other tools yet (the app has already run its web search; do not repeat it)" in text
    assert "ask your short question first and call no tools yet" in build_system_prompt("route").text  # route has no web search
