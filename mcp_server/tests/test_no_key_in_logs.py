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


async def test_openai_key_is_not_logged_by_web_search(monkeypatch, caplog):
    # A fake OpenAI key and a search model (set below). Next, OpenAI clients that fail with an error message that carries the key, the worst case:
    # first a plain exception, then the SDK's own API error (whose message the tool logs in shortened form).
    import httpx2
    import openai

    def make_client(error):
        async def failing_create(**kwargs):
            raise error

        return type("C", (), {"responses": type("R", (), {"create": staticmethod(failing_create)})})

    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    api_error = openai.RateLimitError(
        f"Rate limit reached for key={FAKE_KEY}, see https://platform.openai.com/?key={FAKE_KEY}",
        response=httpx2.Response(429, request=request, json={}), body={"code": "rate_limit_exceeded"},
    )
    monkeypatch.setattr(web_search.get_settings(), "openai_api_key", FAKE_KEY)
    monkeypatch.setattr(web_search.get_settings(), "openai_search_model", "search-model")
    monkeypatch.setattr(web_search.get_settings(), "redis_url", "")  # no cache: the failure path must really run

    results = []
    with caplog.at_level(logging.INFO):
        for error in (RuntimeError(f"request failed for key={FAKE_KEY}"), api_error):
            client = make_client(error)
            monkeypatch.setattr(web_search, "_get_client", lambda client=client: client)
            results.append(await web_search.web_search("events in Lisbon"))

    assert [r["ok"] for r in results] == [False, False]  # the failure paths really ran
    assert [r["error"] for r in results] == ["unavailable", "quota_exceeded"]
    # Neither the logs nor the replies to the agent may contain the key.
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in str(results)
    assert "http_status=429" in caplog.text  # the useful part of the failure is still logged


async def test_railradar_key_is_not_logged_by_train_tool(monkeypatch, caplog):
    # A fake RailRadar key, no Redis, and a real httpx client with a fake transport (no network).
    monkeypatch.setattr(train_tool.get_settings(), "railradar_api_key", FAKE_KEY)
    monkeypatch.setattr(train_tool.get_settings(), "redis_url", "")
    real_client = httpx.AsyncClient
    seen = []

    def answer(request):
        seen.append((str(request.url), request.headers.get("Authorization")))
        if request.url.path.endswith("/stations"):
            return httpx.Response(200, json={"data": [{"code": "PUNE" if request.url.params["q"] == "Pune" else "CSMT", "name": "Station", "popularity": 5, "isActive": True}]})
        item = {"train": {"number": "12123", "name": "Test", "runDays": ["mon"]}, "from": {"code": "PUNE", "name": "Pune", "departure": "06:00", "day": 1},
                "to": {"code": "CSMT", "name": "Mumbai", "arrival": "09:00", "day": 1}, "distance": 190, "duration": 180, "totalHaltsBetween": 2}
        return httpx.Response(200, json={"data": {"trains": [item], "count": 1}})

    monkeypatch.setattr(train_tool.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))

    # Success path, then a failing path (500) and a quota path (429), all with INFO logging captured.
    with caplog.at_level(logging.INFO):
        ok = await train_tool.search_trains("Pune", "Mumbai")
        failing = httpx.MockTransport(lambda request: httpx.Response(500))
        monkeypatch.setattr(train_tool.httpx, "AsyncClient", lambda **kw: real_client(transport=failing, **kw))
        failed = await train_tool.search_trains("Pune", "Mumbai")
        quota = httpx.MockTransport(lambda request: httpx.Response(429))
        monkeypatch.setattr(train_tool.httpx, "AsyncClient", lambda **kw: real_client(transport=quota, **kw))
        limited = await train_tool.search_trains("Pune", "Mumbai")

    assert ok["ok"] is True and failed["ok"] is False and limited["error"] == "quota_exceeded"  # all three paths really ran
    assert seen and all(auth == f"Bearer {FAKE_KEY}" and FAKE_KEY not in url for url, auth in seen)  # the key only in the header
    # Neither the logs nor the replies may contain the key.
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in str(ok) and FAKE_KEY not in str(failed) and FAKE_KEY not in str(limited)


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
