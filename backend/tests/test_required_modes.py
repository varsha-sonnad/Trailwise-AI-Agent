# test_required_modes.py: tests for the required-tools check in itinerary and route mode, the clarifying-question guard,
# round 1 not being forced for a mode with required tools, and the overall time cap for one chat request.
# (Explore's own cases are in test_explore_required.py.) The model call (_generate) and the MCP client are faked.

import asyncio
import json

import pytest

from app.agent import agent, modes
from app.agent.agent import (
    NO_LIVE_INFO_REPLY, REQUEST_TOO_LONG_REPLY, SEARCH_NOT_SET_UP_REPLY, SEARCH_TIMEOUT_REPLY, run_agent,
)
from app.agent.modes import FORCED_TOOLS, REQUIRED_TOOLS, build_system_prompt, is_clarifying_question, prompt_version
from app.core import audit
from tests.test_agent_tools import FakeMcp, script_model, text_reply, tool_calls_reply, tool_reply
from tests.test_tool_choice import tool_list

OFFERED = ["web_search", "get_weather", "geocode_places", "get_place_images", "get_route", "search_buses", "search_trains"]
ITINERARY_MESSAGE = "Plan 3 days in Jaipur"
ROUTE_MESSAGE = "Best train from Pune to Goa?"
ITINERARY_CALLS = [
    ("web_search", {"query": "Jaipur events and closures"}),
    ("get_weather", {"location": "Jaipur"}),
    ("geocode_places", {"names": ["Jaipur"], "context": "Rajasthan, India"}),
]
# Route mode, round 1: the model asks for the map lookup and both transport searches together (no web_search).
ROUTE_CALLS = [
    ("geocode_places", {"names": ["Pune", "Goa"], "context": "India"}),
    ("search_trains", {"source_city": "Pune", "destination_city": "Goa"}),
    ("search_buses", {"source_city": "Pune", "destination_city": "Goa"}),
]
ROUTE_CALL = ("get_route", {"points": [[18.52, 73.86], [15.49, 73.83]], "mode": "driving"})
# What geocode_places returns when both cities were found (get_route becomes required) or only one was.
TWO_PLACES = {"ok": True, "places": [
    {"name": "Pune", "found": True, "lat": 18.52, "lng": 73.86}, {"name": "Goa", "found": True, "lat": 15.49, "lng": 73.83},
]}
ONE_PLACE = {"ok": True, "places": [
    {"name": "Pune", "found": True, "lat": 18.52, "lng": 73.86}, {"name": "Goa", "found": False},
]}


class Env:
    """What the fake MCP server offers and answers; tests change it."""

    offered = OFFERED
    results: dict = {}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """Turn the required-tools check back ON (conftest switches it off), and install a fake MCP server."""
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


def names(choice):
    """The tool names of a recorded tool_choice (it must be the forced "required" kind)."""
    assert choice["type"] == "allowed_tools" and choice["mode"] == "required"
    return [t["name"] for t in choice["tools"]]


def fail(*tools, code="unavailable"):
    """Make these tools return an error object with this code."""
    for tool in tools:
        Env.results[tool] = {"ok": False, "error": code, "message": "down"}


def logged(monkeypatch):
    """Record every audit event as (event, status, fields)."""
    events = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: events.append((event, status, fields)))
    return events


# ---------------------------------------------------------------- the settings

def test_required_tools_per_mode():
    assert REQUIRED_TOOLS["itinerary"] == ("web_search", "get_weather", "geocode_places")
    assert "get_route" not in REQUIRED_TOOLS["itinerary"]  # get_route stays optional
    assert REQUIRED_TOOLS["route"] == ("geocode_places", "search_trains", "search_buses", "get_route")
    assert FORCED_TOOLS["route"] == ("geocode_places", "search_trains", "search_buses")
    assert "web_search" not in REQUIRED_TOOLS["route"] and "web_search" not in FORCED_TOOLS["route"]
    assert "email" not in REQUIRED_TOOLS


def test_the_prompts_have_the_new_rules_and_bumped_versions():
    assert prompt_version("itinerary") == "trailwise.itinerary@6" and prompt_version("route") == "trailwise.route@6"
    itinerary, route = build_system_prompt("itinerary").text, build_system_prompt("route").text
    for tool in ("web_search", "get_weather", "geocode_places"):
        assert f"`{tool}`" in itinerary
    assert "same round" in itinerary and "ask your short question first" in itinerary and "`get_route` is optional" in itinerary
    for tool in ("geocode_places", "search_trains", "search_buses", "get_route"):
        assert f"`{tool}`" in route
    assert "same round" in route and "ask your short question first and call no tools yet" in route
    assert "Do not use `web_search` in this mode" in route and "The app always runs `web_search`" not in route


# ---------------------------------------------------------------- itinerary mode

async def test_itinerary_weather_only_gets_the_missing_tools_forced(monkeypatch):
    """The 09:22 case: the model called only get_weather. Now web_search and geocode_places are forced in one extra round."""
    seen = script_model(monkeypatch, [
        tool_reply(*ITINERARY_CALLS[1]),
        text_reply("A short plan from memory."),
        tool_calls_reply(ITINERARY_CALLS[0], ITINERARY_CALLS[2]),
        text_reply("A grounded plan."),
    ])
    assert await run_agent(ITINERARY_MESSAGE) == "A grounded plan."
    choices = seen["tool_choices"]
    assert choices[0] is None and choices[1] is None and names(choices[2]) == ["web_search", "geocode_places"] and choices[3] is None  # itinerary no longer forces web_search first
    assert "from memory" not in repr(seen["inputs"][2:])  # the draft was dropped


async def test_itinerary_all_three_together_need_no_extra_round(monkeypatch):
    seen = script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Plan.")])
    assert await run_agent(ITINERARY_MESSAGE) == "Plan."
    assert len(seen["inputs"]) == 2 and [n for n, _ in FakeMcp.calls] == ["web_search", "get_weather", "geocode_places"]


async def test_itinerary_get_route_is_optional(monkeypatch):
    seen = script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Plan without a route.")])
    assert await run_agent(ITINERARY_MESSAGE) == "Plan without a route."  # no extra round, no note
    assert "get_route" not in [n for n, _ in FakeMcp.calls] and len(seen["inputs"]) == 2


async def test_itinerary_web_search_failing_gives_the_could_not_check_reply(monkeypatch):
    fail("web_search")
    seen = script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Never used.")])
    assert await run_agent(ITINERARY_MESSAGE) == NO_LIVE_INFO_REPLY and len(seen["inputs"]) == 1


@pytest.mark.parametrize("code, reply", [("timeout", SEARCH_TIMEOUT_REPLY), ("not_configured", SEARCH_NOT_SET_UP_REPLY)])
async def test_itinerary_search_timeout_and_not_configured_keep_their_own_replies(monkeypatch, code, reply):
    fail("web_search", code=code)
    script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Never used.")])
    assert await run_agent(ITINERARY_MESSAGE) == reply


@pytest.mark.parametrize("broken, note", [
    (["get_weather"], "the weather"),
    (["geocode_places"], "the map locations"),
    (["get_weather", "geocode_places"], "the weather and the map locations"),
])
async def test_itinerary_failed_weather_or_geocode_is_noted(monkeypatch, broken, note):
    fail(*broken)
    script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Plan.")])
    assert await run_agent(ITINERARY_MESSAGE) == f"Plan.\n\nNote: I could not check {note} right now."


async def test_itinerary_photos_are_not_required(monkeypatch):
    seen = script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Plan.")])
    await run_agent(ITINERARY_MESSAGE)
    assert "get_place_images" not in [n for n, _ in FakeMcp.calls] and len(seen["inputs"]) == 2


# ---------------------------------------------------------------- route mode (no web_search)

def route_model(monkeypatch, *replies):
    """Script the model with these replies; geocode_places finds both cities unless a test changed Env.results."""
    Env.results.setdefault("geocode_places", TWO_PLACES)
    return script_model(monkeypatch, list(replies))


def called():
    """The tool names called so far, in order."""
    return [n for n, _ in FakeMcp.calls]


async def test_route_round_one_forces_the_three_route_tools_when_both_cities_are_named(monkeypatch):
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("Done."))
    await run_agent(ROUTE_MESSAGE)
    assert names(seen["tool_choices"][0]) == ["geocode_places", "search_trains", "search_buses"]  # never web_search or weather


async def test_route_happy_path_trains_buses_map_then_route_without_extra_rounds(monkeypatch):
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("Take the train or bus."))
    assert await run_agent(ROUTE_MESSAGE) == "Take the train or bus."
    assert called() == ["geocode_places", "search_trains", "search_buses", "get_route"] and "web_search" not in called()
    assert names(seen["tool_choices"][0]) == ["geocode_places", "search_trains", "search_buses"] and seen["tool_choices"][1:] == [None, None]  # only round 1 is forced


async def test_route_a_skipped_tool_gets_one_extra_forced_round(monkeypatch):
    seen = route_model(monkeypatch,
        tool_reply("search_buses", {"source_city": "Pune", "destination_city": "Goa"}),  # trains and the map were skipped
        text_reply("Draft."),
        tool_calls_reply(ROUTE_CALLS[0], ROUTE_CALLS[1]),
        tool_reply(*ROUTE_CALL),
        text_reply("Final."),
    )
    assert await run_agent(ROUTE_MESSAGE) == "Final."
    assert names(seen["tool_choices"][2]) == ["geocode_places", "search_trains"]  # only the missing ones, never web_search
    assert "Draft." not in repr(seen["inputs"][2:])  # the draft was dropped


async def test_route_get_route_is_forced_after_two_places_were_found(monkeypatch):
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), text_reply("No map yet."), tool_reply(*ROUTE_CALL), text_reply("With the map."))
    assert await run_agent(ROUTE_MESSAGE) == "With the map."
    assert names(seen["tool_choices"][2]) == ["get_route"] and called()[-1] == "get_route"


async def test_route_get_route_is_forced_at_most_once_more(monkeypatch):
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), text_reply("a"), text_reply("b"), text_reply("c"))
    assert await run_agent(ROUTE_MESSAGE) == "b"  # the one forced round was ignored by the model; its next text is accepted
    assert len(seen["inputs"]) == 3 and called().count("get_route") == 0


@pytest.mark.parametrize("geocode", [ONE_PLACE, {"ok": True, "places": []}, {"ok": False, "error": "unavailable"}])
async def test_route_get_route_is_not_required_without_two_found_places(monkeypatch, geocode):
    Env.results["geocode_places"] = geocode
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), text_reply("Trains and buses only."))
    assert (await run_agent(ROUTE_MESSAGE)).startswith("Trains and buses only.") and "get_route" not in called()
    assert len(seen["inputs"]) == 2  # no extra round for the map


async def test_route_the_same_place_twice_counts_as_one(monkeypatch):
    Env.results["geocode_places"] = {"ok": True, "places": [{"name": "Pune", "found": True, "lat": 18.52, "lng": 73.86}] * 2}
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), text_reply("Done."))
    await run_agent(ROUTE_MESSAGE)
    assert len(seen["inputs"]) == 2 and "get_route" not in called()


@pytest.mark.parametrize("message, question", [("Best train", "Which two cities do you want to travel between?"), ("buses to Goa", "Which city are you leaving from?")])
async def test_route_a_missing_city_forces_and_requires_nothing(monkeypatch, message, question):
    seen = route_model(monkeypatch, text_reply(question))
    assert await run_agent(message) == question
    assert FakeMcp.calls == [] and len(seen["inputs"]) == 1 and seen["tool_choices"] == [None]


async def test_route_failed_trains_are_left_out_and_the_other_results_kept(monkeypatch):
    fail("search_trains", code="quota_exceeded")
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("I could not check trains right now. Buses: ..."))
    assert await run_agent(ROUTE_MESSAGE) == "I could not check trains right now. Buses: ..."
    assert len(seen["inputs"]) == 3 and called()[-1] == "get_route"  # the answer was accepted, nothing was replaced
    assert '"quota_exceeded"' in repr(seen["inputs"][1])  # the model was told what failed


async def test_route_failed_buses_and_trains_never_give_the_could_not_check_reply(monkeypatch):
    fail("search_trains", "search_buses")
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("Trains and buses are unavailable right now."))
    assert await run_agent(ROUTE_MESSAGE) == "Trains and buses are unavailable right now."
    assert NO_LIVE_INFO_REPLY not in repr(seen)


@pytest.mark.parametrize("broken", [["search_trains"], ["search_buses"], ["search_trains", "search_buses"], ["geocode_places", "search_trains", "search_buses"], ["get_route"]])
async def test_route_never_uses_the_could_not_check_live_information_reply(monkeypatch, broken):
    """Even when round 1 is forced and every tool fails, or tools fail later, route keeps the model's own answer."""
    monkeypatch.setattr(agent, "FORCE_FIRST_ROUND_WHEN_REQUIRED", True)  # round 1 forced to the route tools
    fail(*broken)
    route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("Here is what I could check."))
    reply = await run_agent(ROUTE_MESSAGE)
    assert NO_LIVE_INFO_REPLY not in reply and SEARCH_TIMEOUT_REPLY not in reply and SEARCH_NOT_SET_UP_REPLY not in reply
    assert reply.startswith("Here is what I could check.")


async def test_route_failed_geocode_is_noted_and_the_map_is_left_out(monkeypatch):
    fail("geocode_places")
    route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), text_reply("Take the train."))
    reply = await run_agent(ROUTE_MESSAGE)
    assert reply == "Take the train.\n\nNote: I could not check the map locations right now." and "get_route" not in called()


async def test_route_failed_get_route_is_left_out_without_a_fixed_reply(monkeypatch):
    fail("get_route")
    route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("Take the train."))
    assert await run_agent(ROUTE_MESSAGE) == "Take the train."  # no note, no fixed reply: the map is simply not shown


async def test_route_web_search_is_never_forced_required_or_checked(monkeypatch):
    fail("web_search")  # even if the model uses it and it fails, route does not turn that into the fixed reply
    route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS, ("web_search", {"query": "x"})), tool_reply(*ROUTE_CALL), text_reply("Answer."))
    assert await run_agent(ROUTE_MESSAGE) == "Answer."


async def test_route_only_offered_tools_are_required(monkeypatch):
    Env.offered = ["geocode_places", "search_trains"]  # no buses and no get_route on this server
    seen = route_model(monkeypatch, tool_calls_reply(ROUTE_CALLS[0], ROUTE_CALLS[1]), text_reply("Trains only."))
    assert await run_agent(ROUTE_MESSAGE) == "Trains only." and len(seen["inputs"]) == 2


async def test_itinerary_and_explore_still_use_the_fixed_reply_when_web_search_fails(monkeypatch):
    fail("web_search")
    script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Never used.")])
    assert await run_agent(ITINERARY_MESSAGE) == NO_LIVE_INFO_REPLY


async def test_route_mode_does_not_offer_web_search_or_weather(monkeypatch):
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("Done."))
    await run_agent(ROUTE_MESSAGE)
    offered = {t["name"] for t in seen["tools"][0]}
    assert "web_search" not in offered and "get_weather" not in offered
    assert {"geocode_places", "search_trains", "search_buses", "get_route"} <= offered
    # Other modes still get them.
    seen = script_model(monkeypatch, [text_reply("Which kind of place do you like?")])
    await run_agent("Where should I go in winter?")
    assert {"web_search", "get_weather"} <= {t["name"] for t in seen["tools"][0]}


async def test_route_mode_refuses_a_web_search_or_weather_call_the_model_makes_anyway(monkeypatch):
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS, ("web_search", {"query": "x"}), ("get_weather", {"location": "Goa"})), tool_reply(*ROUTE_CALL), text_reply("Done."))
    assert await run_agent(ROUTE_MESSAGE) == "Done."
    assert "web_search" not in called() and "get_weather" not in called()  # refused as unknown tools, never run


@pytest.mark.parametrize("message", [
    "Gulbarga to Bangalore", "travel options from Gulbarga to Bangalore", "how can I go to Bangalore from Gulbarga", "to Mysore from Bangalore",
])
async def test_route_round_one_is_forced_to_the_three_tools_for_every_two_city_shape(monkeypatch, message):
    seen = route_model(monkeypatch, tool_calls_reply(*ROUTE_CALLS), tool_reply(*ROUTE_CALL), text_reply("Done."))
    await run_agent(message)
    assert names(seen["tool_choices"][0]) == ["geocode_places", "search_trains", "search_buses"]


async def test_route_words_only_leave_round_one_free_so_the_model_can_ask_for_a_city(monkeypatch):
    seen = route_model(monkeypatch, text_reply("Which two cities?"))
    assert await run_agent("any bus options") == "Which two cities?"
    assert seen["tool_choices"] == [None] and FakeMcp.calls == []


async def test_only_explore_forces_web_search_first(monkeypatch):
    for message, forced in (("Tell me about Rishikesh", True), (ITINERARY_MESSAGE, False), (ROUTE_MESSAGE, False)):
        seen = script_model(monkeypatch, [tool_calls_reply(*(ITINERARY_CALLS + [("get_place_images", {"place": "x", "region": "y"})] + ROUTE_CALLS)), tool_reply(*ROUTE_CALL), text_reply("Done."), text_reply("Done.")])
        Env.results["geocode_places"] = TWO_PLACES
        await run_agent(message)
        first = seen["tool_choices"][0]
        assert (first is not None and names(first) == ["web_search"]) is forced


async def test_email_mode_is_still_never_checked_or_forced(monkeypatch):
    seen = script_model(monkeypatch, [text_reply("Draft email.")])
    assert await run_agent("Email me this plan") == "Draft email."
    assert seen["tool_choices"] == [None] and len(seen["inputs"]) == 1


# ---------------------------------------------------------------- round 1 is forced to be web_search for these modes
# (test_force_web_search.py has the full set of cases; the switches are checked here next to the older ones)

async def test_round_one_is_forced_to_be_web_search_only_in_explore(monkeypatch):
    message = "Tell me about Rishikesh"
    events = logged(monkeypatch)
    seen = script_model(monkeypatch, [tool_calls_reply(*(ITINERARY_CALLS + [("get_place_images", {"place": "x", "region": "y"})])), text_reply("Done.")])
    await run_agent(message)
    assert names(seen["tool_choices"][0]) == ["web_search"]
    assert [f["tool_choice"] for e, s, f in events if e == "model_decision" and "tool_choice" in f][0] == "required"


async def test_without_the_web_search_switch_round_one_is_automatic(monkeypatch):
    monkeypatch.setattr(agent, "FORCE_WEB_SEARCH_FIRST", False)
    seen = script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Plan.")])
    await run_agent(ITINERARY_MESSAGE)
    assert seen["tool_choices"][0] is None


async def test_round_one_can_be_forced_to_any_required_tool_with_the_older_switch(monkeypatch):
    monkeypatch.setattr(agent, "FORCE_WEB_SEARCH_FIRST", False)
    monkeypatch.setattr(agent, "FORCE_FIRST_ROUND_WHEN_REQUIRED", True)
    seen = script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Plan.")])
    await run_agent(ITINERARY_MESSAGE)
    assert names(seen["tool_choices"][0]) == [t for t in FORCED_TOOLS["itinerary"] if t in OFFERED]
    assert seen["tool_choices"][1] is None


# ---------------------------------------------------------------- the clarifying-question guard

@pytest.mark.parametrize("text, expected", [
    ("Where would you like to go, and for how many days?", True),
    ("Which city are you starting from?", True),
    ('Did you mean "Goa"?', True),
    ("Which place (for example Goa?)", True),
    ("Where to? " + "x" * 289 + "?", True),  # exactly 300 characters
    ("Where to? " + "x" * 291 + "?", False),  # 301 characters: too long to be a short question
    ("Here is a plan for Jaipur.", False),
    ("Here is a plan. Does that work? Tell me more", False),  # does not end with a question mark
    ("", False),
    ("   ", False),
])
def test_is_clarifying_question(text, expected):
    assert is_clarifying_question(text) is expected


@pytest.mark.parametrize("message, question", [
    ("Plan a trip", "Where would you like to go, and for how many days?"),   # itinerary
    ("Best train", "Which two cities do you want to travel between?"),         # route
    ("Suggest somewhere nice", "What kind of place do you like: beaches, hills or cities?"),  # explore
])
async def test_a_short_question_as_the_first_reply_skips_the_check_and_is_logged(monkeypatch, message, question):
    events = logged(monkeypatch)
    seen = script_model(monkeypatch, [text_reply(question)])
    assert await run_agent(message) == question
    assert len(seen["inputs"]) == 1 and FakeMcp.calls == []  # no extra round, no tools, no note
    guard = [f for e, s, f in events if e == "model_decision" and f.get("decision") == "guard_skipped"]
    assert len(guard) == 1 and guard[0]["reason"] == "clarifying_question" and guard[0]["round"] == 1
    assert question not in repr(events) and message not in repr(events)  # no message or reply text in the log


async def test_the_guard_is_written_to_the_real_audit_file(monkeypatch, tmp_path):
    settings = audit.get_settings()
    path = tmp_path / "audit.log"
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(path))
    audit.reset_audit_logger()
    try:
        script_model(monkeypatch, [text_reply("Where to?")])
        await run_agent("Plan a trip")
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    finally:
        audit.reset_audit_logger()
    guard = [x for x in lines if x.get("decision") == "guard_skipped"]
    assert len(guard) == 1 and guard[0]["reason"] == "clarifying_question" and guard[0]["mode"] == "itinerary"


async def test_a_long_first_reply_is_not_skipped(monkeypatch):
    seen = script_model(monkeypatch, [text_reply("A long plan from memory. " * 20 + "Is that fine?"), tool_calls_reply(*ITINERARY_CALLS), text_reply("Grounded.")])
    assert await run_agent(ITINERARY_MESSAGE) == "Grounded."
    assert len(seen["inputs"]) == 3 and names(seen["tool_choices"][1]) == ["web_search", "get_weather", "geocode_places"]


async def test_a_first_reply_that_is_not_a_question_is_not_skipped(monkeypatch):
    seen = script_model(monkeypatch, [text_reply("Jaipur is lovely."), tool_calls_reply(*ITINERARY_CALLS), text_reply("Grounded.")])
    assert await run_agent(ITINERARY_MESSAGE) == "Grounded." and len(seen["inputs"]) == 3


async def test_a_question_after_a_tool_was_requested_is_not_skipped(monkeypatch):
    """The guard is only for the very first reply: after tools were asked for, the normal check applies."""
    seen = script_model(monkeypatch, [
        tool_reply(*ITINERARY_CALLS[1]), text_reply("Which month are you going?"),
        tool_calls_reply(ITINERARY_CALLS[0], ITINERARY_CALLS[2]), text_reply("Grounded."),
    ])
    assert await run_agent(ITINERARY_MESSAGE) == "Grounded."
    assert names(seen["tool_choices"][2]) == ["web_search", "geocode_places"]


@pytest.mark.parametrize("message", ["hi", "thanks", "Email me this plan"])
async def test_the_guard_does_not_log_when_nothing_is_required(monkeypatch, message):
    events = logged(monkeypatch)
    script_model(monkeypatch, [text_reply("Which one?")])
    await run_agent(message)
    assert not [1 for e, s, f in events if f.get("decision") == "guard_skipped"]


# ---------------------------------------------------------------- the overall time cap for one chat request

@pytest.fixture
def short_cap(monkeypatch):
    """A 50 ms cap for the whole request."""
    monkeypatch.setattr(agent.get_settings(), "chat_request_timeout_seconds", 0.05)


async def test_a_request_that_runs_out_of_time_gets_a_friendly_reply(monkeypatch, short_cap):
    events = logged(monkeypatch)

    async def hang(input_items, tools, system_prompt, tool_choice=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(agent, "_generate", hang)
    reply = await run_agent(ITINERARY_MESSAGE)
    assert reply == REQUEST_TOO_LONG_REPLY and "took too long" in reply
    timeout = [f for e, s, f in events if e == "request_timeout"]
    assert len(timeout) == 1 and timeout[0]["cap_s"] == 0.05 and "duration_ms" in timeout[0]
    assert not [1 for e, s, f in events if e in ("request_completed", "request_failed")]  # it is its own outcome


async def test_a_hanging_tool_call_is_cut_off_by_the_cap_and_the_connection_is_cleaned_up(monkeypatch, short_cap):
    closed = []

    async def aexit(self, *a):
        closed.append(True)
        return False

    async def hang_tool(self, name, arguments):
        await asyncio.sleep(30)

    monkeypatch.setattr(FakeMcp, "__aexit__", aexit)
    monkeypatch.setattr(FakeMcp, "call_tool", hang_tool)
    script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Never reached.")])
    assert await run_agent(ITINERARY_MESSAGE) == REQUEST_TOO_LONG_REPLY
    assert closed == [True]  # the MCP connection was closed on the way out


async def test_the_cap_is_read_from_the_setting_on_every_request(monkeypatch):
    script_model(monkeypatch, [text_reply("Plain."), text_reply("Plain.")])
    monkeypatch.setattr(agent.get_settings(), "chat_request_timeout_seconds", 5.0)
    assert await run_agent("hi") == "Plain."
    # A second request with a tiny cap is cut off even though the first one finished.
    monkeypatch.setattr(agent.get_settings(), "chat_request_timeout_seconds", 0.05)

    async def hang(input_items, tools, system_prompt, tool_choice=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(agent, "_generate", hang)
    assert await run_agent("hi") == REQUEST_TOO_LONG_REPLY


async def test_a_fast_request_is_not_affected_by_the_cap(monkeypatch):
    script_model(monkeypatch, [tool_calls_reply(*ITINERARY_CALLS), text_reply("Plan.")])
    assert await run_agent(ITINERARY_MESSAGE) == "Plan."


async def test_a_timeout_that_is_not_the_cap_still_surfaces_as_an_error(monkeypatch):
    """A TimeoutError raised from inside (not our cap) must not be turned into the friendly reply."""
    events = logged(monkeypatch)

    async def boom(input_items, tools, system_prompt, tool_choice=None):
        raise TimeoutError("something else timed out")

    monkeypatch.setattr(agent, "_generate", boom)
    with pytest.raises(TimeoutError):
        await run_agent("hi")
    assert [f["error_type"] for e, s, f in events if e == "request_failed"] == ["TimeoutError"]


async def test_the_next_request_after_a_timeout_works(monkeypatch, short_cap):
    async def hang(input_items, tools, system_prompt, tool_choice=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(agent, "_generate", hang)
    assert await run_agent("hi") == REQUEST_TOO_LONG_REPLY
    monkeypatch.setattr(agent.get_settings(), "chat_request_timeout_seconds", 5.0)
    script_model(monkeypatch, [text_reply("Hello again.")])
    assert await run_agent("hi") == "Hello again."


async def test_the_timeout_reply_goes_through_the_chat_route_as_a_normal_reply(monkeypatch, short_cap):
    from fastapi.testclient import TestClient

    from app.main import app

    async def hang(input_items, tools, system_prompt, tool_choice=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(agent, "_generate", hang)
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 200 and response.json() == {"reply": REQUEST_TOO_LONG_REPLY, "images": [], "mode": "explore", "places": []}


# ---------------------------------------------------------------- get_weather after geocode_places, with lat and lng

@pytest.mark.parametrize("mode", ["explore", "itinerary"])
def test_the_prompts_tell_the_model_to_call_get_weather_after_geocode_places_with_lat_and_lng(mode):
    text = build_system_prompt(mode).text
    assert "in the next round, call `get_weather` once with the `lat` and `lng` that `geocode_places` returned" in text
    assert "never invented numbers" in text
    assert "call `get_weather` with a plain city name instead" in text  # only the fallback
    assert "Prefer `lat` and `lng` from `geocode_places`" in text
    # get_weather is no longer in the first round's "together" list.
    first_rule = next(line for line in text.splitlines() if "together in the same round" in line)
    assert "`get_weather`" not in first_rule.split("Then, in the next round")[0]


async def test_a_two_round_flow_geocode_first_then_weather_with_its_coordinates_is_accepted_without_an_extra_round(monkeypatch):
    seen = script_model(monkeypatch, [
        tool_calls_reply(ITINERARY_CALLS[0], ITINERARY_CALLS[2]),                       # round 1: web_search and geocode_places
        tool_reply("get_weather", {"lat": 26.9124, "lng": 75.7873}),                    # round 2: get_weather with the coordinates
        text_reply("Plan."),                                                            # round 3: the answer
    ])
    assert await run_agent(ITINERARY_MESSAGE) == "Plan."
    assert [c for c in FakeMcp.calls if c[0] == "get_weather"] == [("get_weather", {"lat": 26.9124, "lng": 75.7873})]
    assert len(seen["inputs"]) == 3 and seen["tool_choices"][0] is None  # itinerary: nothing is forced up front
    assert seen["tool_choices"][1:] == [None, None]


async def test_a_weather_call_with_coordinates_is_logged_by_argument_names_only(monkeypatch):
    events = logged(monkeypatch)
    script_model(monkeypatch, [
        tool_calls_reply(ITINERARY_CALLS[0], ITINERARY_CALLS[2]),
        tool_reply("get_weather", {"lat": 26.9124, "lng": 75.7873}),
        text_reply("Plan."),
    ])
    await run_agent(ITINERARY_MESSAGE)
    weather = [f for e, s, f in events if e == "tool_call" and f.get("tool") == "get_weather"]
    assert weather[0]["argument_names"] == ["lat", "lng"]
    assert "26.9124" not in repr(events) and "75.7873" not in repr(events)
