# test_chat.py: tests for POST /chat. The agent is replaced by a fake, so no model call is made.

import pytest
from fastapi.testclient import TestClient

from app.agent.agent import AgentBusyError, AgentError, AgentUnavailableError
from app.config import get_settings
from app.main import app


@pytest.fixture
def client():
    """A test client; the context manager runs the app's startup and shutdown."""
    with TestClient(app) as c:
        yield c


def test_chat_returns_reply(client, monkeypatch):
    """A valid message returns the agent's reply, and the message is passed through trimmed."""
    seen = {}

    async def fake_run_agent(message, **memory):
        seen["message"] = message
        return "Try Palolem beach."

    # Replace the agent where the route looks it up.
    monkeypatch.setattr("app.api.chat.run_agent", fake_run_agent)
    r = client.post("/chat", json={"message": "  Plan Goa  "})
    assert r.status_code == 200
    assert r.json() == {"reply": "Try Palolem beach.", "images": [], "mode": None, "places": []}  # a plain text reply carries no photos
    assert seen["message"] == "Plan Goa"


@pytest.mark.parametrize("payload", [{"message": ""}, {"message": "   "}, {}])
def test_chat_rejects_empty_or_missing_message(client, payload):
    """An empty, blank or missing message is rejected with 422."""
    assert client.post("/chat", json=payload).status_code == 422


def test_chat_rejects_too_long_message(client):
    """A message over the configured maximum length is rejected with 422."""
    too_long = "a" * (get_settings().max_message_chars + 1)
    assert client.post("/chat", json={"message": too_long}).status_code == 422


def test_chat_agent_failure_returns_502(client, monkeypatch):
    """If the agent fails, the client gets a generic 502 with no internal details."""

    async def failing_run_agent(message, **memory):
        raise AgentError("OpenAI request failed")

    monkeypatch.setattr("app.api.chat.run_agent", failing_run_agent)
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 502
    assert "OpenAI" not in r.text


def test_chat_overloaded_model_returns_503(client, monkeypatch):
    """If the AI service is overloaded, the client gets 503 with a 'busy' message and a Retry-After hint."""

    async def busy_run_agent(message, **memory):
        raise AgentBusyError("OpenAI request failed", status_code=503)

    monkeypatch.setattr("app.api.chat.run_agent", busy_run_agent)
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 503
    assert "busy" in r.json()["detail"] and r.headers["retry-after"] == "60"
    assert "OpenAI" not in r.text


def test_chat_quota_used_up_returns_a_clear_not_available_message(client, monkeypatch):
    """If the AI service quota is used up, the client gets 503 'not available', with no Retry-After and no internals."""

    async def unavailable_run_agent(message, **memory):
        raise AgentUnavailableError("OpenAI quota used up", status_code=429, kind="quota")

    monkeypatch.setattr("app.api.chat.run_agent", unavailable_run_agent)
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 503
    assert r.json()["detail"] == "The assistant is not available right now. Please try again later."
    assert "retry-after" not in r.headers
    assert "OpenAI" not in r.text and "quota" not in r.text.lower()
