# test_bus.py: tests for the bus tool and its registration in server.py.
# The Parse.bot API (httpx) is faked, so no key, credits or network are needed.

import json
import logging
from datetime import date

import httpx
import pytest

from tools import bus

# "Today" for the tests, so the date rules do not depend on the real clock.
TODAY = date(2026, 11, 17)
GOOD_DATE = "2026-11-20"

CITY_DOCS = {
    "bangalore": {"status": "success", "data": {"docs": [{"ID": 1304, "locationType": "AIRPORT"}, {"ID": 122, "locationType": "CITY"}]}},
    "goa": {"status": "success", "data": {"docs": [{"ID": 210, "locationType": "CITY"}]}},
}

# A trimmed search_buses response with two buses.
SEARCH_JSON = {
    "status": "success",
    "data": {
        "metaData": {"totalCount": 68},
        "inventories": [
            {
                "travelsName": "Kadamba Transport Corporation Limited (KTCL)",
                "busType": "A/C Sleeper (2+1)",
                "departureTime": "2026-11-20 20:05:00",
                "arrivalTime": "2026-11-21 07:00:00",
                "journeyDurationMin": 655,
                "standardBpName": "Yeshwantpur",
                "standardDpName": "Panjim",
                "fareList": [1799],
                "availableSeats": 1,
                "totalSeats": 30,
                "isAc": True,
                "isSleeper": True,
                "totalRatings": 3.1,
            },
            {"travelsName": "Butterfly Holidays", "busType": "Seater", "fareList": [2699, 2299, "bad", True], "availableSeats": 6},
        ],
    },
}


class FakeClient:
    """Stand-in for httpx.AsyncClient: answers by endpoint name, or raises."""

    def __init__(self, search=None, cities=None, search_status=200, city_status=200, error=None):
        self.search = SEARCH_JSON if search is None else search
        self.cities = CITY_DOCS if cities is None else cities
        self.search_status, self.city_status, self.error = search_status, city_status, error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.error:
            raise self.error
        if url.endswith("/get_city_suggestions"):
            body = self.cities.get(params["query"].lower(), {"status": "success", "data": {"docs": []}})
            return httpx.Response(self.city_status, json=body)
        return httpx.Response(self.search_status, json=self.search)


@pytest.fixture
def setup(monkeypatch):
    """Fake key, fixed date, and a helper to install a fake HTTP client."""
    monkeypatch.setattr(bus.get_settings(), "parsebot_api_key", "fake-key")
    monkeypatch.setattr(bus.get_settings(), "redis_url", "")  # no cache unless a test asks for one (never the real .env)
    monkeypatch.setattr(bus, "_today", lambda: TODAY)

    def install(client):
        monkeypatch.setattr(bus.httpx, "AsyncClient", lambda **kw: client)
        return client

    return install


async def test_searches_and_shapes_result(setup):
    client = setup(FakeClient())
    result = await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    assert result["ok"] and result["total_buses_found"] == 68 and result["currency"] == "INR"
    first = result["buses"][0]
    assert first["operator"].startswith("Kadamba") and first["price_from_inr"] == 1799
    assert first["available_seats"] == 1 and first["departure_time"] == "2026-11-20 20:05:00"
    # Fares are cleaned: junk and booleans dropped, sorted lowest first.
    assert result["buses"][1]["fares_inr"] == [2299, 2699]
    assert "Verify before travel" in result["disclaimer"]
    # The city (not the airport) was chosen, and the search used both IDs and the date.
    search = next(c for c in client.calls if c["url"].endswith("/search_buses"))
    assert search["params"]["from_city_id"] == "122" and search["params"]["to_city_id"] == "210"
    assert search["params"]["doj"] == GOOD_DATE


async def test_key_and_snapshot_in_headers_not_url(setup):
    client = setup(FakeClient())
    await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    for call in client.calls:
        assert call["headers"]["X-API-Key"] == "fake-key" and "fake-key" not in call["url"]
        assert call["headers"]["API-Snapshot-Version"] == "11"
        assert "35a9b6fe-4009-43dd-89e2-993f11b60ad0" in call["url"]


@pytest.mark.parametrize(
    "src,dst,when",
    [
        ("", "Goa", GOOD_DATE),
        ("Bangalore", "   ", GOOD_DATE),
        ("x" * 61, "Goa", GOOD_DATE),
        ("Bang;alore", "Goa", GOOD_DATE),
        ("Bangalore", "Goa", "tomorrow"),
        ("Bangalore", "Goa", "20-11-2026"),
        ("Bangalore", "Goa", "2026-13-01"),
        ("Bangalore", "Goa", "2026-11-16"),  # yesterday
        ("Bangalore", "Goa", "2027-06-01"),  # too far ahead
        ("Bangalore", "bangalore", GOOD_DATE),  # same city
    ],
)
async def test_bad_arguments(setup, src, dst, when):
    result = await bus.search_buses(src, dst, when)
    assert result["ok"] is False and result["error"] == "invalid_arguments"


async def test_today_is_allowed(setup):
    setup(FakeClient())
    assert (await bus.search_buses("Bangalore", "Goa", "2026-11-17"))["ok"] is True


async def test_unknown_city(setup):
    setup(FakeClient())
    assert (await bus.search_buses("Bangalore", "Nowhereville", GOOD_DATE))["error"] == "city_not_found"


async def test_unknown_city_says_which_end_failed(setup):
    """failed_end tells the agent whether the destination (the gateway-flow signal) or the source had no city."""
    setup(FakeClient())
    assert (await bus.search_buses("Bangalore", "Nowhereville", GOOD_DATE))["failed_end"] == "destination"
    assert (await bus.search_buses("Nowhereville", "Goa", GOOD_DATE))["failed_end"] == "source"


async def test_no_buses(setup):
    setup(FakeClient(search={"status": "success", "data": {"inventories": []}}))
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "no_buses_found"


async def test_rate_limit_server_error_and_timeout(setup):
    install = setup
    install(FakeClient(search_status=429))
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "rate_limited"
    install(FakeClient(city_status=429))
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "rate_limited"
    install(FakeClient(search_status=500))
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "unavailable"
    install(FakeClient(error=httpx.TimeoutException("slow")))
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "unavailable"


async def test_error_envelope_and_bad_body_are_failures(setup):
    install = setup
    install(FakeClient(search={"status": "error", "error": {"code": "upstream_error"}}))
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "unavailable"
    install(FakeClient(search={"status": "success", "data": "nonsense"}))
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "unavailable"


async def test_buses_are_capped(setup):
    many = {"status": "success", "data": {"metaData": {"totalCount": 200}, "inventories": [{"travelsName": "X"}] * 50}}
    setup(FakeClient(search=many))
    result = await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    assert len(result["buses"]) == bus.MAX_BUSES and result["buses_truncated"] is True


async def test_missing_key(setup, monkeypatch):
    monkeypatch.setattr(bus.get_settings(), "parsebot_api_key", "")
    assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["error"] == "not_configured"


async def test_tool_registered_in_server():
    # The tool must be listed by the real MCP server, with all three parameters.
    import server

    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "search_buses")
    assert {"source_city", "destination_city", "journey_date"} <= set(tool.inputSchema["properties"])


# ---------------------------------------------------------------- optional date (tomorrow computed in code)
TOMORROW = "2026-11-18"  # TODAY + 1 day


def search_calls(client):
    """Only the search_buses requests (not the city lookups)."""
    return [c for c in client.calls if c["url"].endswith("/search_buses")]


@pytest.mark.parametrize("args", [{}, {"journey_date": None}, {"journey_date": ""}, {"journey_date": "   "}])
async def test_missing_date_searches_tomorrow(setup, args):
    client = setup(FakeClient())
    result = await bus.search_buses("Bangalore", "Goa", **args)
    assert result["ok"] is True and result["date"] == TOMORROW and result["date_assumed"] is True
    # The date sent upstream is the one computed in code, always as an explicit doj.
    assert [c["params"]["doj"] for c in search_calls(client)] == [TOMORROW]
    assert f"Showing results for {TOMORROW}" in result["message"] and "another date" in result["message"]


async def test_tomorrow_follows_the_clock_not_the_model(setup, monkeypatch):
    client = setup(FakeClient())
    monkeypatch.setattr(bus, "_today", lambda: date(2026, 12, 31))  # year rollover
    result = await bus.search_buses("Bangalore", "Goa")
    assert result["date"] == "2027-01-01" and search_calls(client)[0]["params"]["doj"] == "2027-01-01"


async def test_given_date_is_used_and_not_flagged_assumed(setup):
    client = setup(FakeClient())
    result = await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    assert result["date"] == GOOD_DATE and result["date_assumed"] is False
    assert search_calls(client)[0]["params"]["doj"] == GOOD_DATE
    assert result["message"] == f"Showing results for {GOOD_DATE}."


async def test_validation_still_applies_to_given_dates(setup):
    # Text is never turned into a date: "tomorrow" stays invalid, and the range rules are unchanged.
    setup(FakeClient())
    for bad in ("tomorrow", "2026-11-16", "2027-06-01", "18/11/2026"):
        assert (await bus.search_buses("Bangalore", "Goa", bad))["error"] == "invalid_arguments"
    assert (await bus.search_buses("Bangalore", "Goa", "2027-03-17"))["ok"] is True  # exactly 120 days ahead
    assert (await bus.search_buses("Bangalore", "Goa", "2027-03-18"))["error"] == "invalid_arguments"


async def test_results_are_labelled_third_party_and_date_specific(setup):
    setup(FakeClient())
    for when in (None, GOOD_DATE):
        result = await bus.search_buses("Bangalore", "Goa", when)
        assert "third-party" in result["disclaimer"] and "Verify before travel" in result["disclaimer"]
        assert "third-party" in result["fares_note"] and result["date"] in result["fares_note"]


async def test_no_buses_for_assumed_date_names_the_date(setup):
    setup(FakeClient(search={"status": "success", "data": {"inventories": []}}))
    result = await bus.search_buses("Bangalore", "Goa")
    assert result["error"] == "no_buses_found" and result["date"] == TOMORROW and result["date_assumed"] is True
    assert "another date" in result["message"] and TOMORROW in result["message"]


# ---------------------------------------------------------------- 30-minute cache (successes only)
class FakeRedis:
    """Stand-in for a Redis client: a dict with get and set (and the ttl each set used)."""

    def __init__(self, broken=False):
        self.data, self.sets, self.broken, self.closed = {}, [], broken, False

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis down")
        self.sets.append((key, ex))
        self.data[key] = value
        return True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def cache(setup, monkeypatch):
    """Redis 'set' to a fake address, backed by an in-memory fake."""
    monkeypatch.setattr(bus.get_settings(), "redis_url", "redis://fake-host:6379/0")
    fake = FakeRedis()
    monkeypatch.setattr(bus.redis, "from_url", lambda url, **kw: fake)
    fake.install = setup
    return fake


async def test_success_is_cached_for_30_minutes_and_reused(cache):
    first_client = cache.install(FakeClient())
    first = await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    assert first["ok"] and len(first_client.calls) == 3  # 2 city lookups + 1 search
    assert len(cache.sets) == 1 and cache.sets[0][1] == 1800 and bus.CACHE_TTL_SECONDS == 1800
    second_client = cache.install(FakeClient(error=AssertionError("no HTTP call on a cache hit")))
    second = await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    assert second["ok"] and second["buses"] == first["buses"] and second_client.calls == []


async def test_key_depends_on_cities_and_date_not_case_or_spaces(cache):
    cache.install(FakeClient())
    await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    await bus.search_buses("  BANGALORE ", "goa", GOOD_DATE)  # same search: a hit, no new set
    assert len(cache.sets) == 1
    await bus.search_buses("Bangalore", "Goa", "2026-11-21")  # other date: new entry
    await bus.search_buses("Goa", "Bangalore", GOOD_DATE)  # other direction: new entry
    assert len({k for k, _ in cache.sets}) == 3
    assert all(k.startswith("buses:v1:") and "bangalore" not in k.lower() for k, _ in cache.sets)


async def test_assumed_date_hit_keeps_its_own_flags(cache):
    cache.install(FakeClient())
    explicit = await bus.search_buses("Bangalore", "Goa", TOMORROW)
    assert explicit["date_assumed"] is False
    cache.install(FakeClient(error=AssertionError("cache hit expected")))
    omitted = await bus.search_buses("Bangalore", "Goa")  # same key as the explicit tomorrow
    assert omitted["date"] == TOMORROW and omitted["date_assumed"] is True and "Showing results for" in omitted["message"]
    again = await bus.search_buses("Bangalore", "Goa", TOMORROW)
    assert again["date_assumed"] is False  # the stored entry never carries the flag


async def test_failures_and_no_buses_are_never_cached(cache):
    for client in (
        FakeClient(search={"status": "success", "data": {"inventories": []}}),
        FakeClient(search_status=429),
        FakeClient(search_status=500),
        FakeClient(error=httpx.TimeoutException("slow")),
        FakeClient(cities={}),
        FakeClient(search={"status": "error", "error": {"code": "x"}}),
    ):
        cache.install(client)
        assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["ok"] is False
    assert cache.sets == [] and cache.data == {}


async def test_damaged_or_mismatched_entries_are_misses(cache):
    key = bus._cache_key("Bangalore", "Goa", GOOD_DATE)
    for bad in ("not json", json.dumps([1]), json.dumps({"ok": True}), json.dumps({"ok": True, "date": "2000-01-01", "buses": [{"operator": "x"}], "from": "a", "to": "b"})):
        cache.data = {key: bad}
        client = cache.install(FakeClient())
        assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["ok"] is True
        assert len(search_calls(client)) == 1  # went to the API instead


async def test_redis_down_warns_once_and_search_still_works(cache, caplog):
    cache.broken = True
    client = cache.install(FakeClient())
    with caplog.at_level(logging.DEBUG):
        result = await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    assert result["ok"] is True and len(client.calls) == 3
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "fake-host" not in warnings[0].getMessage()


async def test_redis_unset_is_silent_and_never_used(setup, monkeypatch, caplog):
    monkeypatch.setattr(bus.redis, "from_url", lambda *a, **k: pytest.fail("Redis must not be used"))
    setup(FakeClient())
    with caplog.at_level(logging.DEBUG):
        assert (await bus.search_buses("Bangalore", "Goa", GOOD_DATE))["ok"] is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_redis_timeouts_and_close(cache, monkeypatch):
    seen = {}

    def spy(url, **kw):
        seen.update(kw)
        return cache

    monkeypatch.setattr(bus.redis, "from_url", spy)
    cache.install(FakeClient())
    await bus.search_buses("Bangalore", "Goa", GOOD_DATE)
    assert seen == {"socket_connect_timeout": 2.0, "socket_timeout": 2.0} and cache.closed is True


async def test_key_never_in_logs_or_cache(cache, caplog):
    cache.install(FakeClient())
    with caplog.at_level(logging.DEBUG):
        await bus.search_buses("Bangalore", "Goa")
    assert "fake-key" not in caplog.text
    assert all("fake-key" not in v for v in cache.data.values()) and all("fake-key" not in k for k in cache.data)


# ---------------------------------------------------------------- tool description and schema
async def test_journey_date_is_optional_in_the_schema_and_description():
    import server

    tool = next(t for t in await server.mcp.list_tools() if t.name == "search_buses")
    assert "journey_date" not in tool.inputSchema.get("required", [])
    assert {"source_city", "destination_city"} <= set(tool.inputSchema["required"])
    text = tool.description
    assert "OPTIONAL" in text and "tomorrow" in text and "another date" in text and "third-party" in text
    assert text.count("1. ") == 1 and "2. " in text and "3. " in text  # keeps the three-part format
