# test_no_key_in_logs.py: regression test. The API key must never appear in log output.
# httpx logs request URLs at INFO and the weather key travels in the URL, so server.py
# keeps the httpx/httpcore loggers at WARNING. This test runs the real tool code through a
# real httpx client (with a fake transport, so no network) and checks the captured logs.

import logging
from datetime import date

import httpx

import server  # noqa: F401  (importing the server applies the logging setup)
from tools import bus, train_tool, weather, web_search

FAKE_KEY = "SECRET-TEST-KEY-123"


async def test_api_key_is_not_logged(monkeypatch, caplog):
    # A fake key.
    monkeypatch.setattr(weather.get_settings(), "openweather_api_key", FAKE_KEY)

    # A real httpx client whose transport answers locally instead of using the network.
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"name": "Goa"}))
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))

    # Capture everything from INFO up, the level at which httpx would log the URL.
    with caplog.at_level(logging.INFO):
        result = await weather.get_weather("Goa")

    assert result["ok"] is True  # the request really went through the real client
    assert FAKE_KEY not in caplog.text


async def test_gemini_key_is_not_logged_by_web_search(monkeypatch, caplog):
    # A fake Gemini key (set below). Next, a Gemini client that fails with an error message that carries the key, the worst case.
    async def failing_generate(**kwargs):
        raise RuntimeError(f"request failed for key={FAKE_KEY}")

    fake_client = type("C", (), {"aio": type("A", (), {"models": type("M", (), {"generate_content": staticmethod(failing_generate)})})})
    monkeypatch.setattr(web_search, "_get_client", lambda: fake_client)
    monkeypatch.setattr(web_search.get_settings(), "gemini_api_key", FAKE_KEY)

    with caplog.at_level(logging.INFO):
        result = await web_search.web_search("events in Lisbon")

    assert result["ok"] is False  # the failure path really ran
    # Neither the logs nor the reply to the agent may contain the key.
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in str(result)


async def test_railradar_key_is_not_logged_by_train_tool(monkeypatch, caplog):
    # A fake RailRadar key, and a real httpx client with a fake transport (no network).
    monkeypatch.setattr(train_tool.get_settings(), "railradar_api_key", FAKE_KEY)
    real_client = httpx.AsyncClient
    body = {"data": {"train": {"number": "12919", "name": "Test"}, "route": []}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    monkeypatch.setattr(train_tool.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))

    # Success path, then a failing path (500), both with INFO logging captured.
    with caplog.at_level(logging.INFO):
        ok = await train_tool.get_train_details("12919")
        failing = httpx.MockTransport(lambda request: httpx.Response(500))
        monkeypatch.setattr(train_tool.httpx, "AsyncClient", lambda **kw: real_client(transport=failing, **kw))
        failed = await train_tool.get_train_details("12919")

    assert ok["ok"] is True and failed["ok"] is False  # both paths really ran
    # Neither the logs nor the replies may contain the key.
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in str(ok) and FAKE_KEY not in str(failed)


async def test_parsebot_key_is_not_logged_by_bus_tool(monkeypatch, caplog):
    # A fake Parse.bot key, and a real httpx client with a fake transport (no network).
    monkeypatch.setattr(bus.get_settings(), "parsebot_api_key", FAKE_KEY)
    monkeypatch.setattr(bus, "_today", lambda: date(2026, 11, 17))
    real_client = httpx.AsyncClient

    def answer(request):
        if request.url.path.endswith("/get_city_suggestions"):
            return httpx.Response(200, json={"status": "success", "data": {"docs": [{"ID": 1, "locationType": "CITY"}]}})
        return httpx.Response(200, json={"status": "success", "data": {"inventories": [{"travelsName": "X"}]}})

    monkeypatch.setattr(bus.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))

    # Success path, then a failing path (500), both with INFO logging captured.
    with caplog.at_level(logging.INFO):
        ok = await bus.search_buses("Bangalore", "Goa", "2026-11-20")
        failing = httpx.MockTransport(lambda request: httpx.Response(500))
        monkeypatch.setattr(bus.httpx, "AsyncClient", lambda **kw: real_client(transport=failing, **kw))
        failed = await bus.search_buses("Bangalore", "Goa", "2026-11-20")

    assert ok["ok"] is True and failed["ok"] is False  # both paths really ran
    # Neither the logs nor the replies may contain the key.
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in str(ok) and FAKE_KEY not in str(failed)


def test_http_loggers_are_quiet():
    # The setup in server.py must keep both HTTP libraries above INFO.
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING
