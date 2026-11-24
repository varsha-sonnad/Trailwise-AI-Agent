# test_gateway_flow.py: tests for route mode's gateway flow (remote places such as Kedarnath with no station or bus route):
# the "destination not found" signal, only searching pairs whose ends were geocoded, unknown junctions dropped, the map legs
# (road legs solid, other connections dashed, drawn by the server), the route.md rules and the legs in the /chat response.
# The model call (_generate) and the MCP client are faked; no .env, key or network is used.

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent import agent, modes
from app.agent.agent import AgentReply, run_agent
from app.agent.legs import GeoState, build_legs, check_city_pair, check_route_points, is_gateway_signal
from app.agent.modes import REQUIRED_TOOLS, build_system_prompt, prompt_version
from app.main import app
from app.models.chat import MAX_CHAT_LEGS, ChatResponse, Leg, clean_legs
from tests.test_agent_tools import FakeMcp, script_model, text_reply, tool_calls_reply, tool_reply
from tests.test_tool_choice import tool_list

OFFERED = ["geocode_places", "get_route", "search_buses", "search_trains"]
MESSAGE = "How do I go from Delhi to Kedarnath?"
NORMAL_MESSAGE = "Best way from Delhi to Haridwar?"
# Coordinates of the places used below (test data only, never used by the app).
COORDS = {
    "Delhi": (28.61, 77.21), "Kedarnath": (30.73, 79.07), "Haridwar": (29.95, 78.16),
    "Sonprayag": (30.62, 79.09), "Gaurikund": (30.65, 79.08),
}
ROAD_LINE = [[29.95, 78.16], [30.3, 78.9], [30.62, 79.09], [30.65, 79.08]]


def found(name):
    """One found entry of a geocode_places result."""
    lat, lng = COORDS[name]
    return {"name": name, "found": True, "lat": lat, "lng": lng}


def geocode_result(*names, missing=()):
    """What geocode_places returns for these names, in order; names in `missing` come back not found."""
    return {"ok": True, "places": [{"name": n, "found": False, "lat": None, "lng": None} if n in missing else found(n) for n in names]}


NO_STATION = {"ok": False, "error": "station_not_found", "failed_end": "destination", "message": "No station found."}
NO_CITY = {"ok": False, "error": "city_not_found", "failed_end": "destination", "message": "No city found."}


class Env:
    """What the fake MCP server answers: a result per tool name, or a function (arguments) -> result."""

    results: dict = {}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """Required-tools check ON (conftest switches it off) and a fake MCP server with the route-mode tools."""
    monkeypatch.setattr(modes, "REQUIRED_TOOLS", REQUIRED_TOOLS)
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    Env.results = {}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)

    async def list_tools(self):
        return tool_list(*OFFERED)

    async def call_tool(self, name, arguments):
        FakeMcp.calls.append((name, arguments))
        result = Env.results.get(name, {"ok": True, "data": name})
        return result(arguments) if callable(result) else result

    monkeypatch.setattr(FakeMcp, "list_tools", list_tools, raising=False)
    monkeypatch.setattr(FakeMcp, "call_tool", call_tool)


def calls_of(tool):
    """The arguments of every call of one tool, in order."""
    return [args for name, args in FakeMcp.calls if name == tool]


def pairs(tool):
    """(source, destination) of every call of a search tool."""
    return [(a["source_city"], a["destination_city"]) for a in calls_of(tool)]


def points(*names):
    """The [lat, lng] points of these places."""
    return [list(COORDS[n]) for n in names]


def kedarnath_script(monkeypatch):
    """The whole gateway conversation: round 1 searches Kedarnath itself, round 2 uses Haridwar, round 3 draws the road."""
    Env.results.update({
        "geocode_places": lambda a: geocode_result(*a["names"], missing=("Atlantis",)),
        "search_trains": lambda a: NO_STATION if a["destination_city"] == "Kedarnath" else {"ok": True, "trains": []},
        "search_buses": lambda a: NO_CITY if a["destination_city"] == "Kedarnath" else {"ok": True, "buses": []},
        "get_route": {"ok": True, "found": True, "route": ROAD_LINE},
    })
    chain = ["Delhi", "Haridwar", "Sonprayag", "Gaurikund", "Atlantis", "Kedarnath"]  # Atlantis is an unknown junction
    return script_model(monkeypatch, [
        tool_calls_reply(
            ("geocode_places", {"names": ["Delhi", "Kedarnath"], "context": "India"}),
            ("search_trains", {"source_city": "Delhi", "destination_city": "Kedarnath"}),
            ("search_buses", {"source_city": "Delhi", "destination_city": "Kedarnath"}),
        ),
        tool_calls_reply(
            ("geocode_places", {"names": chain, "context": "Uttarakhand, India"}),
            ("search_trains", {"source_city": "Delhi", "destination_city": "Haridwar"}),
            ("search_buses", {"source_city": "Delhi", "destination_city": "Haridwar"}),
        ),
        tool_reply("get_route", {"points": points("Haridwar", "Sonprayag", "Gaurikund"), "mode": "driving"}),
        text_reply("No direct train or bus. Go via Haridwar."),
    ])


# ---------------------------------------------------------------- the gateway flow end to end

async def test_a_remote_destination_uses_the_gateway_city_and_draws_road_and_dashed_legs(monkeypatch):
    kedarnath_script(monkeypatch)
    reply = await run_agent(MESSAGE)
    assert reply == "No direct train or bus. Go via Haridwar."
    # The first leg is searched from the start city to the gateway city.
    assert pairs("search_trains") == [("Delhi", "Kedarnath"), ("Delhi", "Haridwar")]
    assert pairs("search_buses") == [("Delhi", "Kedarnath"), ("Delhi", "Haridwar")]
    kinds = [leg["kind"] for leg in reply.legs]
    assert kinds.count("road") == 1 and kinds.count("other") == 2
    assert [leg["points"] for leg in reply.legs if leg["kind"] == "road"] == [ROAD_LINE]
    # Dashed: start city to gateway (train or bus) and the roadhead to the destination (trek, pony or helicopter).
    dashed = [leg["points"] for leg in reply.legs if leg["kind"] == "other"]
    assert points("Delhi", "Haridwar") in dashed and points("Gaurikund", "Kedarnath") in dashed
    # The road legs are not drawn a second time as dashed lines.
    assert points("Haridwar", "Sonprayag") not in dashed and points("Sonprayag", "Gaurikund") not in dashed


async def test_an_unknown_junction_is_dropped_everywhere(monkeypatch):
    kedarnath_script(monkeypatch)
    reply = await run_agent(MESSAGE)
    assert "Atlantis" not in json.dumps(reply.legs) and all(p["name"] != "Atlantis" for p in reply.places)
    # Only the known places are joined, in travel order: one road and two dashed legs.
    assert len(reply.legs) == 3


def test_a_missing_destination_station_is_the_gateway_signal_only_for_the_destination():
    assert is_gateway_signal("search_trains", NO_STATION) and is_gateway_signal("search_buses", NO_CITY)
    assert not is_gateway_signal("search_trains", {**NO_STATION, "failed_end": "source"})
    assert not is_gateway_signal("search_trains", {"ok": False, "error": "unavailable"})
    assert not is_gateway_signal("get_route", NO_STATION) and not is_gateway_signal("search_trains", {"ok": True})


async def test_without_the_signal_a_normal_route_draws_only_the_road(monkeypatch):
    """A normal city pair: no dashed lines are invented."""
    Env.results.update({"geocode_places": geocode_result("Delhi", "Haridwar"), "get_route": {"ok": True, "found": True, "route": ROAD_LINE}})
    script_model(monkeypatch, [
        tool_calls_reply(
            ("geocode_places", {"names": ["Delhi", "Haridwar"], "context": "India"}),
            ("search_trains", {"source_city": "Delhi", "destination_city": "Haridwar"}),
            ("search_buses", {"source_city": "Delhi", "destination_city": "Haridwar"}),
        ),
        tool_reply("get_route", {"points": points("Delhi", "Haridwar"), "mode": "driving"}),
        text_reply("Take the train."),
    ])
    reply = await run_agent(NORMAL_MESSAGE)
    assert [leg["kind"] for leg in reply.legs] == ["road"]


async def test_a_missing_source_station_does_not_start_the_gateway_flow(monkeypatch):
    Env.results.update({
        "geocode_places": geocode_result("Delhi", "Kedarnath", "Haridwar"),
        "search_trains": {**NO_STATION, "failed_end": "source"}, "search_buses": {"ok": True, "buses": []},
        "get_route": {"ok": True, "found": True, "route": ROAD_LINE},
    })
    script_model(monkeypatch, [
        tool_calls_reply(
            ("geocode_places", {"names": ["Delhi", "Kedarnath", "Haridwar"], "context": "India"}),
            ("search_trains", {"source_city": "Delhi", "destination_city": "Haridwar"}),
            ("search_buses", {"source_city": "Delhi", "destination_city": "Haridwar"}),
        ),
        tool_reply("get_route", {"points": points("Delhi", "Haridwar"), "mode": "driving"}),
        text_reply("Bus only."),
    ])
    reply = await run_agent(NORMAL_MESSAGE)
    assert reply.legs and all(leg["kind"] == "road" for leg in reply.legs)


# ---------------------------------------------------------------- only pairs whose ends are geocoded

async def test_a_destination_that_was_not_located_is_never_searched(monkeypatch):
    Env.results["geocode_places"] = geocode_result("Delhi", "Kedarnath", missing=("Kedarnath",))
    seen = script_model(monkeypatch, [
        tool_calls_reply(
            ("search_trains", {"source_city": "Delhi", "destination_city": "Kedarnath"}),  # listed before the geocode on purpose
            ("geocode_places", {"names": ["Delhi", "Kedarnath"], "context": "India"}),
            ("search_buses", {"source_city": "Delhi", "destination_city": "Kedarnath"}),
        ),
        text_reply("No direct train or bus to Kedarnath."),
    ])
    await run_agent(MESSAGE)
    assert [n for n, _ in FakeMcp.calls] == ["geocode_places"]  # geocoding ran first; the two searches were refused
    outputs = [json.loads(i["output"]) for i in seen["inputs"][1] if i.get("type") == "function_call_output"]
    refused = [o for o in outputs if o.get("error") == "place_not_located"]
    assert len(refused) == 2 and all(o["failed_end"] == "destination" for o in refused)


async def test_a_pair_with_an_ungeocoded_end_is_refused_but_a_located_pair_runs(monkeypatch):
    Env.results["geocode_places"] = lambda a: geocode_result(*a["names"])
    script_model(monkeypatch, [
        tool_calls_reply(
            ("geocode_places", {"names": ["Delhi", "Kedarnath"], "context": "India"}),
            ("search_trains", {"source_city": "Delhi", "destination_city": "Haridwar"}),  # Haridwar was never geocoded
            ("search_buses", {"source_city": "Delhi", "destination_city": "Kedarnath"}),
        ),
        text_reply("Done."),  # a draft: get_route is still required, so one extra round is forced
        tool_reply("get_route", {"points": points("Delhi", "Kedarnath"), "mode": "driving"}),
        text_reply("Done."),
    ])
    await run_agent(MESSAGE)
    assert calls_of("search_trains") == [] and len(calls_of("search_buses")) == 1


async def test_get_route_with_made_up_points_is_refused(monkeypatch):
    Env.results["geocode_places"] = geocode_result("Delhi", "Haridwar")
    script_model(monkeypatch, [
        tool_calls_reply(
            ("geocode_places", {"names": ["Delhi", "Haridwar"], "context": "India"}),
            ("search_trains", {"source_city": "Delhi", "destination_city": "Haridwar"}),
            ("search_buses", {"source_city": "Delhi", "destination_city": "Haridwar"}),
        ),
        tool_reply("get_route", {"points": [[28.61, 77.21], [12.34, 56.78]], "mode": "driving"}),  # the second point was invented
        text_reply("Done."),
    ])
    reply = await run_agent(NORMAL_MESSAGE)
    assert calls_of("get_route") == [] and reply.legs == []


async def test_a_geocoder_outage_does_not_block_the_searches(monkeypatch):
    Env.results["geocode_places"] = {"ok": False, "error": "unavailable", "message": "down"}
    script_model(monkeypatch, [
        tool_calls_reply(
            ("geocode_places", {"names": ["Delhi", "Haridwar"], "context": "India"}),
            ("search_trains", {"source_city": "Delhi", "destination_city": "Haridwar"}),
            ("search_buses", {"source_city": "Delhi", "destination_city": "Haridwar"}),
        ),
        text_reply("Trains and buses only."),
    ])
    await run_agent(NORMAL_MESSAGE)
    assert len(calls_of("search_trains")) == 1 and len(calls_of("search_buses")) == 1


# ---------------------------------------------------------------- the pure helpers

def state_with(*names, missing=()):
    state = GeoState()
    state.add(geocode_result(*names, missing=missing))
    return state


def test_check_city_pair_needs_both_ends_geocoded_once_a_lookup_worked():
    state = state_with("Delhi", "Haridwar")
    assert check_city_pair(state, {"source_city": "Delhi", "destination_city": "Haridwar"}) is None
    assert check_city_pair(state, {"source_city": " delhi ", "destination_city": "HARIDWAR"}) is None
    bad = check_city_pair(state, {"source_city": "Delhi", "destination_city": "Kedarnath"})
    assert bad["error"] == "place_not_located" and bad["failed_end"] == "destination"
    assert check_city_pair(state, {"source_city": "Kedarnath", "destination_city": "Delhi"})["failed_end"] == "source"
    assert check_city_pair(GeoState(), {"source_city": "Anywhere", "destination_city": "Else"}) is None  # no lookup yet: not enforced


def test_check_route_points_accepts_only_geocoded_coordinates():
    state = state_with("Delhi", "Haridwar")
    assert check_route_points(state, {"points": points("Delhi", "Haridwar")}) is None
    for bad in ([[1, 2], list(COORDS["Delhi"])], [["x", 1], [2, 3]], [[True, 1], [2, 3]], [[28.61]]):
        assert check_route_points(state, {"points": bad})["error"] == "place_not_located"


def test_build_legs_marks_uncovered_pairs_as_other_and_needs_three_places():
    journey = [{"name": n, "lat": COORDS[n][0], "lng": COORDS[n][1]} for n in ("Delhi", "Haridwar", "Gaurikund")]
    road = {"inputs": points("Haridwar", "Gaurikund"), "points": ROAD_LINE}
    legs = build_legs([road], journey, gateway=True)
    assert [leg["kind"] for leg in legs] == ["road", "other"] and legs[1]["points"] == points("Delhi", "Haridwar")
    assert [leg["kind"] for leg in build_legs([road], journey, gateway=False)] == ["road"]  # never outside the gateway flow
    assert build_legs([], journey[:2], gateway=True) == []  # fewer than three places: no dashed guess


def test_geo_state_keeps_the_latest_journey_of_two_or_more_places():
    state = GeoState()
    state.add(geocode_result("Delhi", "Kedarnath"))
    state.add(geocode_result("Delhi", "Haridwar", "Gaurikund", "Kedarnath"))
    state.add(geocode_result("Kedarnath"))  # one place: does not replace the journey
    assert [p["name"] for p in state.journey] == ["Delhi", "Haridwar", "Gaurikund", "Kedarnath"]


# ---------------------------------------------------------------- the legs model and the /chat response

def test_leg_validation_and_cleaning():
    assert Leg.model_validate({"kind": "road", "points": [[1, 2], [3, 4]]}).kind == "road"
    for bad in (
        {"kind": "flight", "points": [[1, 2], [3, 4]]}, {"kind": "road", "points": [[1, 2]]}, {"kind": "road", "points": [[1, 2], [999, 4]]},
        {"kind": "road", "points": [[1, 2], ["x", 4]]}, {"kind": "road", "points": [[1, 2], [float("nan"), 4]]},
        {"kind": "road", "points": [[1, 2], [True, 4]]}, {"kind": "road", "points": [[1, 2]] * 301}, {"kind": "road"},
    ):
        with pytest.raises(ValidationError):
            Leg.model_validate(bad)
    good = {"kind": "other", "points": [[1, 2], [3, 4]]}
    assert clean_legs([good, {"kind": "x"}, "junk", None]) == [good]
    assert len(clean_legs([good] * 20)) == MAX_CHAT_LEGS and clean_legs("no") == []
    assert ChatResponse(reply="Hi").legs == []


def test_the_chat_response_carries_the_legs(monkeypatch):
    legs = [{"kind": "road", "points": [[1, 2], [3, 4]]}, {"kind": "other", "points": [[3, 4], [5, 6]]}, {"kind": "bad", "points": []}]

    async def fake(message, **kwargs):
        return AgentReply("Via Haridwar.", places=[], mode="route", legs=legs)

    monkeypatch.setattr("app.api.chat.run_agent", fake)
    body = TestClient(app).post("/chat", json={"message": MESSAGE}).json()
    assert body["legs"] == [{"kind": "road", "points": [[1.0, 2.0], [3.0, 4.0]]}, {"kind": "other", "points": [[3.0, 4.0], [5.0, 6.0]]}]


# ---------------------------------------------------------------- route.md

def test_route_prompt_is_version_7_and_has_the_gateway_rules():
    assert prompt_version("route") == "trailwise.route@7"
    route = build_system_prompt("route").text
    for phrase in (
        "Remote destinations (gateway flow)", "there is no direct train or bus", "Haridwar, Rishikesh or Dehradun",
        "Never ask permission first", "Sonprayag, Gaurikund", "no timings, distances or prices",
        "suggested connections, verify before travel", "failed_end", "never give coordinates yourself",
        "A place that comes back not found is dropped",
    ):
        assert phrase in route, phrase
    assert 'Never start a reply with "I could not locate"' in route
    assert "say you could not locate that place" not in route  # the old instruction is gone
    assert "Verify before travel." in route  # the transport disclaimer stays (project rule 4)
