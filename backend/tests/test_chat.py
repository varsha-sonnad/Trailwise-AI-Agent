# test_chat.py: tests for POST /chat. The agent is replaced by a fake, so no Gemini call is made.

import pytest
from fastapi.testclient import TestClient

from app.agent.agent import AgentError
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

    async def fake_run_agent(message):
        seen["message"] = message
        return "Try Palolem beach."

    # Replace the agent where the route looks it up.
    monkeypatch.setattr("app.api.chat.run_agent", fake_run_agent)
    r = client.post("/chat", json={"message": "  Plan Goa  "})
    assert r.status_code == 200
    assert r.json() == {"reply": "Try Palolem beach."}
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

    async def failing_run_agent(message):
        raise AgentError("Gemini request failed")

    monkeypatch.setattr("app.api.chat.run_agent", failing_run_agent)
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 502
    assert "Gemini" not in r.text
