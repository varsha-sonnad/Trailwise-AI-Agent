# test_weather.py: tests for the weather tool and its registration in server.py.
# The weather API (httpx) is faked, so no key or network is needed.

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
