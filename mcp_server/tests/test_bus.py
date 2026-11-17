# test_bus.py: tests for the bus tool and its registration in server.py.
# The Parse.bot API (httpx) is faked, so no key, credits or network are needed.

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
