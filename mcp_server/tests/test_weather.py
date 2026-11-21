# test_weather.py: tests for the weather tool and its registration in server.py.
# The weather API (httpx) is faked, so no key or network is needed.

import logging

import httpx
import pytest

from tools import weather

# A trimmed OpenWeatherMap response.
API_JSON = {
    "name": "Goa",
    "sys": {"country": "IN"},
    "main": {"temp": 30.5, "feels_like": 33.0, "humidity": 70},
    "wind": {"speed": 3.2},
    "weather": [{"description": "light rain"}],
}


class FakeClient:
    """Stand-in for httpx.AsyncClient that returns a canned response or raises."""

    def __init__(self, status=200, body=None, error=None):
        self.status, self.body, self.error = status, body, error
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.calls += 1
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.body if self.body is not None else {})


@pytest.fixture
def setup(monkeypatch):
    """Fake key and a helper to install a fake HTTP client."""
    monkeypatch.setattr(weather.get_settings(), "openweather_api_key", "fake-key")

    def install(client):
        monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **kw: client)
        return client

    return install


async def test_calls_api_and_shapes_result(setup):
    client = setup(FakeClient(body=API_JSON))
    result = await weather.get_weather("Goa")
    assert result["ok"] and result["temperature_c"] == 30.5
    assert client.calls == 1


@pytest.mark.parametrize("bad", ["", "   ", "x" * 101, "Goa; DROP", "a\nb"])
async def test_bad_arguments(setup, bad):
    result = await weather.get_weather(bad)
    assert result["ok"] is False and result["error"] == "invalid_arguments"


async def test_unknown_location(setup):
    install = setup
    install(FakeClient(status=404))
    assert (await weather.get_weather("Nowhereville"))["error"] == "location_not_found"


async def test_api_failure_and_timeout(setup):
    install = setup
    install(FakeClient(status=500))
    assert (await weather.get_weather("Goa"))["error"] == "unavailable"
    install(FakeClient(error=httpx.TimeoutException("slow")))
    assert (await weather.get_weather("Goa"))["error"] == "unavailable"


async def test_missing_key(setup, monkeypatch):
    monkeypatch.setattr(weather.get_settings(), "openweather_api_key", "")
    assert (await weather.get_weather("Goa"))["error"] == "not_configured"


async def test_tool_registered_in_server():
    # The tool must be listed by the real MCP server, with a location parameter.
    import server

    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "get_weather")
    assert "location" in tool.inputSchema["properties"]


# ---------------------------------------------------------------- the fallback names and coordinates

KEY = "fake-key-for-weather-tests"


class ScriptedClient:
    """Stand-in for httpx.AsyncClient: plays back one answer per request (a status number, an Exception, or a Response)."""

    def __init__(self, *script):
        self.script = list(script)
        self.params = []  # the params of every request, in order

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.params.append(dict(params))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(item, json=API_JSON if item == 200 else {})

    @property
    def queries(self):
        """What each request asked for: the q text, or 'lat,lon' for coordinates."""
        return [p["q"] if "q" in p else f"{p['lat']},{p['lon']}" for p in self.params]


@pytest.fixture
def api(monkeypatch):
    """A fake key, and a function that installs a scripted fake HTTP client and returns it."""
    monkeypatch.setattr(weather.get_settings(), "openweather_api_key", KEY)

    def install(*script):
        client = ScriptedClient(*script)
        monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **kw: client)
        return client

    return install


# --- the names tried

@pytest.mark.parametrize("location, expected", [
    ("Goa", ["Goa", "Goa,IN"]),
    ("Kedarnath, Uttarakhand, India", ["Kedarnath, Uttarakhand, India", "Kedarnath", "Kedarnath,IN"]),
    ("Kedarnath,Uttarakhand", ["Kedarnath,Uttarakhand", "Kedarnath", "Kedarnath,IN"]),
    ("Kedarnath,IN", ["Kedarnath,IN", "Kedarnath"]),              # the last name repeats the given one: tried once
    ("kedarnath,in", ["kedarnath,in", "kedarnath"]),              # repeats are found ignoring case
    ("  Rishikesh , Uttarakhand ", ["Rishikesh , Uttarakhand", "Rishikesh", "Rishikesh,IN"]),
    (", Uttarakhand", [", Uttarakhand"]),                         # nothing before the first comma: only the given text
    ("", []),
])
def test_candidate_names(location, expected):
    assert weather.candidate_names(location.strip()) == expected


async def test_the_location_as_given_is_tried_first_and_success_stops_the_tries(api):
    client = api(200)
    result = await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert result["ok"] is True and client.queries == ["Kedarnath, Uttarakhand, India"]


async def test_then_the_part_before_the_first_comma(api):
    client = api(404, 200)
    result = await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert result["ok"] is True and client.queries == ["Kedarnath, Uttarakhand, India", "Kedarnath"]


async def test_then_that_part_plus_in(api):
    client = api(404, 404, 200)
    result = await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert result["ok"] is True and client.queries == ["Kedarnath, Uttarakhand, India", "Kedarnath", "Kedarnath,IN"]


async def test_every_call_has_the_key_and_metric_units_and_nothing_else(api):
    client = api(404, 404, 200)
    await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert all(set(p) == {"q", "appid", "units"} and p["appid"] == KEY and p["units"] == "metric" for p in client.params)


async def test_all_names_failing_is_location_not_found_and_logs_the_names_tried(api, caplog):
    client = api(404, 404, 404)
    with caplog.at_level(logging.INFO):
        result = await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert result["ok"] is False and result["error"] == "location_not_found" and len(client.params) == 3
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "weather location not found" in log
    assert "location='Kedarnath, Uttarakhand, India'" in log
    assert "names_tried=['Kedarnath, Uttarakhand, India', 'Kedarnath', 'Kedarnath,IN']" in log
    assert "coordinates_tried=False" in log


async def test_a_name_with_no_comma_has_two_tries(api):
    client = api(404, 404)
    result = await weather.get_weather("Nowhereville")
    assert result["error"] == "location_not_found" and client.queries == ["Nowhereville", "Nowhereville,IN"]


async def test_the_log_has_no_key_and_nothing_but_the_location_and_names(api, caplog):
    api(404, 404, 404)
    with caplog.at_level(logging.DEBUG):
        await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert KEY not in caplog.text and "appid" not in caplog.text and "http" not in caplog.text


async def test_a_success_logs_nothing(api, caplog):
    api(404, 200)
    with caplog.at_level(logging.DEBUG):
        await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert caplog.records == [] or all("not found" not in r.getMessage() for r in caplog.records)


# --- coordinates

async def test_coordinates_are_used_first_when_given(api):
    client = api(200)
    result = await weather.get_weather("Kedarnath, Uttarakhand, India", lat=30.7352, lng=79.0669)
    assert result["ok"] is True and client.queries == ["30.7352,79.0669"]
    assert set(client.params[0]) == {"lat", "lon", "appid", "units"}  # no q: the coordinates are the query


async def test_coordinates_alone_are_enough(api):
    client = api(200)
    assert (await weather.get_weather(lat=30.7352, lng=79.0669))["ok"] is True
    assert client.queries == ["30.7352,79.0669"]


async def test_when_the_coordinates_fail_the_names_are_tried_after_them(api):
    client = api(404, 404, 200)
    result = await weather.get_weather("Kedarnath, Uttarakhand, India", lat=30.7352, lng=79.0669)
    assert result["ok"] is True and client.queries == ["30.7352,79.0669", "Kedarnath, Uttarakhand, India", "Kedarnath"]


async def test_coordinates_only_failing_is_location_not_found_and_says_so(api, caplog):
    api(404)
    with caplog.at_level(logging.INFO):
        result = await weather.get_weather(lat=0.5, lng=0.5)
    assert result["error"] == "location_not_found" and "those coordinates" in result["message"]
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "location=''" in log and "coordinates_tried=True" in log and "names_tried=[]" in log


async def test_everything_failing_with_both_logs_the_names_and_that_coordinates_were_tried(api, caplog):
    client = api(404, 404, 404, 404)
    with caplog.at_level(logging.INFO):
        result = await weather.get_weather("Kedarnath, Uttarakhand, India", lat=30.7352, lng=79.0669)
    assert result["error"] == "location_not_found" and len(client.params) == 4
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "coordinates_tried=True" in log and "'Kedarnath,IN'" in log
    assert KEY not in log


@pytest.mark.parametrize("lat, lng", [(-90, -180), (90, 180), (0, 0), (30, 79), (-33.86, 151.2)])
async def test_valid_coordinates_including_the_limits_and_whole_numbers_are_accepted(api, lat, lng):
    client = api(200)
    assert (await weather.get_weather(lat=lat, lng=lng))["ok"] is True
    assert client.params[0]["lat"] == float(lat) and client.params[0]["lon"] == float(lng)


# --- argument validation (no API call is made for any of these)

@pytest.mark.parametrize("kwargs", [
    {"lat": 30.0},                                  # lat without lng
    {"lng": 79.0},                                  # lng without lat
    {"lat": 90.0001, "lng": 0}, {"lat": -90.0001, "lng": 0},
    {"lat": 0, "lng": 180.0001}, {"lat": 0, "lng": -180.0001},
    {"lat": float("nan"), "lng": 0}, {"lat": 0, "lng": float("nan")},
    {"lat": float("inf"), "lng": 0}, {"lat": 0, "lng": float("-inf")},
    {"lat": True, "lng": 0}, {"lat": 0, "lng": False},
    {"lat": "30.7", "lng": "79.0"}, {"lat": [30], "lng": 79}, {"lat": {"v": 1}, "lng": 79},
    {"location": "Goa", "lat": 30.0},               # a good name does not rescue a half pair
    {"location": "Goa; DROP", "lat": 30.0, "lng": 79.0},  # a bad name is refused even with good coordinates
    {"location": "x" * 101, "lat": 30.0, "lng": 79.0},
    {},                                             # nothing to look up
    {"location": "   "},
])
async def test_bad_arguments_are_refused_without_any_request(api, kwargs):
    client = api(200)
    result = await weather.get_weather(**kwargs)
    assert result["ok"] is False and result["error"] == "invalid_arguments" and client.params == []


# --- failures that are not "not found" stop at once

@pytest.mark.parametrize("failure", [500, 502, 503, 401, 403, 429, httpx.TimeoutException("slow"), httpx.ConnectError("down")])
async def test_any_failure_other_than_not_found_stops_at_the_first_try(api, failure):
    client = api(failure, 200, 200)
    result = await weather.get_weather("Kedarnath, Uttarakhand, India", lat=30.0, lng=79.0)
    assert result["error"] == "unavailable" and len(client.params) == 1


async def test_a_failure_after_a_not_found_still_stops_there(api):
    client = api(404, 500, 200)
    result = await weather.get_weather("Kedarnath, Uttarakhand, India")
    assert result["error"] == "unavailable" and len(client.params) == 2


async def test_a_reply_that_is_not_json_is_unavailable(api):
    api(httpx.Response(200, text="not json"))
    assert (await weather.get_weather("Goa"))["error"] == "unavailable"


async def test_the_result_shape_is_unchanged(api):
    api(200)
    assert await weather.get_weather(lat=15.3, lng=76.4) == {
        "ok": True, "location": "Goa", "country": "IN", "temperature_c": 30.5, "feels_like_c": 33.0,
        "humidity_pct": 70, "wind_speed_ms": 3.2, "conditions": "light rain", "source": "openweathermap",
    }


async def test_a_missing_key_stops_before_any_request(monkeypatch):
    monkeypatch.setattr(weather.get_settings(), "openweather_api_key", "")
    client = ScriptedClient(200)
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **kw: client)
    assert (await weather.get_weather(lat=1.0, lng=2.0))["error"] == "not_configured" and client.params == []


# --- registration and the description

async def test_the_tool_schema_has_location_lat_and_lng_and_none_is_required():
    import server

    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "get_weather")
    assert set(tool.inputSchema["properties"]) == {"location", "lat", "lng"}
    assert not tool.inputSchema.get("required")  # either the name or the coordinates


async def test_the_description_prefers_lat_and_lng_from_geocode_places_then_a_plain_city_name():
    import server

    tools = await server.mcp.list_tools()
    description = next(t for t in tools if t.name == "get_weather").description
    assert description.index("1. What it does and returns:") < description.index("2. When to call:") < description.index("3. When NOT to call:")
    assert "Prefer lat and lng" in description and "geocode_places" in description
    assert "plain city name" in description and "no region or country" in description
    assert "Call geocode_places first and pass its lat and lng" in description
