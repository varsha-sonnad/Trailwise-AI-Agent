# test_train_tool.py: tests for the train tool and its registration in server.py.
# The RailRadar API (httpx) is faked, so no key or network is needed.

import httpx
import pytest

from tools import train_tool

# A trimmed RailRadar response.
API_JSON = {
    "success": True,
    "data": {
        "train": {
            "number": "12919",
            "name": "Malwa Express",
            "type": "SF",
            "source": {"code": "SMVDK", "name": "Shri Mata Vaishno Devi Katra", "lat": 1, "lng": 2},
            "destination": {"code": "INDB", "name": "Indore Jn", "lat": 3, "lng": 4},
            "runDays": ["Mon", "Wed"],
            "distance": 1200,
            "duration": "22:10",
            "totalHalts": 2,
            "classes": ["SL", "3A"],
            "isPrsBookable": True,
        },
        "route": [
            {"station": {"code": "SMVDK", "name": "Katra"}, "departure": "10:00", "arrivalDay": 1, "platform": "1", "distance": 0},
            {"station": {"code": "INDB", "name": "Indore Jn"}, "arrival": "08:10", "arrivalDay": 2, "distance": 1200},
        ],
    },
}


class FakeClient:
    """Stand-in for httpx.AsyncClient that returns a canned response or raises."""

    def __init__(self, status=200, body=None, error=None):
        self.status, self.body, self.error = status, body, error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.body if self.body is not None else {})


@pytest.fixture
def setup(monkeypatch):
    """Fake key and a helper to install a fake HTTP client."""
    monkeypatch.setattr(train_tool.get_settings(), "railradar_api_key", "fake-key")

    def install(client):
        monkeypatch.setattr(train_tool.httpx, "AsyncClient", lambda **kw: client)
        return client

    return install


async def test_calls_api_and_shapes_result(setup):
    client = setup(FakeClient(body=API_JSON))
    result = await train_tool.get_train_details("12919")
    assert result["ok"] and result["train"]["name"] == "Malwa Express"
    assert result["stops"][0]["departure"] == "10:00" and len(result["stops"]) == 2
    # Real API host, key in the header (not the URL), and the disclaimer is attached.
    call = client.calls[0]
    assert call["url"] == "https://api.railradar.in/v1/trains/12919"
    assert call["headers"]["Authorization"] == "Bearer fake-key" and "fake-key" not in call["url"]
    assert "Verify before travel" in result["disclaimer"]


async def test_no_seat_or_price_fields(setup):
    setup(FakeClient(body=API_JSON))
    result = await train_tool.get_train_details("12919")
    text = str(result).lower()
    assert "seat_availability" not in text and "fare" not in text.replace("fares are not provided", "")


@pytest.mark.parametrize("bad", ["", "   ", "x" * 11, "12919/live", "1291 9", "../x", "a\nb", "१२९१९"])
async def test_bad_arguments(setup, bad):
    result = await train_tool.get_train_details(bad)
    assert result["ok"] is False and result["error"] == "invalid_arguments"


async def test_unknown_train(setup):
    setup(FakeClient(status=404))
    assert (await train_tool.get_train_details("00000"))["error"] == "train_not_found"


async def test_api_failure_and_timeout(setup):
    install = setup
    install(FakeClient(status=500))
    assert (await train_tool.get_train_details("12919"))["error"] == "unavailable"
    install(FakeClient(status=429))
    assert (await train_tool.get_train_details("12919"))["error"] == "unavailable"
    install(FakeClient(error=httpx.TimeoutException("slow")))
    assert (await train_tool.get_train_details("12919"))["error"] == "unavailable"


async def test_unexpected_body_is_a_failure(setup):
    setup(FakeClient(body={"success": True, "data": {}}))
    assert (await train_tool.get_train_details("12919"))["error"] == "unavailable"


async def test_stops_are_capped(setup):
    body = {"data": {"train": API_JSON["data"]["train"], "route": [{"station": {"code": "X"}}] * 100}}
    setup(FakeClient(body=body))
    result = await train_tool.get_train_details("12919")
    assert len(result["stops"]) == train_tool.MAX_STOPS and result["stops_truncated"] is True


async def test_missing_key(setup, monkeypatch):
    monkeypatch.setattr(train_tool.get_settings(), "railradar_api_key", "")
    assert (await train_tool.get_train_details("12919"))["error"] == "not_configured"


async def test_tool_registered_in_server():
    # The tool must be listed by the real MCP server, with a train_number parameter.
    import server

    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "get_train_details")
    assert "train_number" in tool.inputSchema["properties"]
