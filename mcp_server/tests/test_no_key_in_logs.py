# test_no_key_in_logs.py: regression test. The API key must never appear in log output.
# httpx logs request URLs at INFO and the weather key travels in the URL, so server.py
# keeps the httpx/httpcore loggers at WARNING. This test runs the real tool code through a
# real httpx client (with a fake transport, so no network) and checks the captured logs.

import logging

import httpx

import server  # noqa: F401  (importing the server applies the logging setup)
from tools import weather, web_search

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


def test_http_loggers_are_quiet():
    # The setup in server.py must keep both HTTP libraries above INFO.
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING
