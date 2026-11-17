# test_audit.py: tests for the audit log (app/core/audit.py) and the agent's audit trail.
# Gemini and the MCP client are faked, and the log goes to a temp file, so no key or network is used.

import json

import pytest

from app.agent import agent
from app.agent.agent import AgentError, run_agent
from app.core import audit
from tests.test_agent_tools import FakeMcp, script_gemini, text_reply, tool_reply

FAKE_KEY = "AIzaSyFAKEKEYFORTESTING1234567890abcd"
SECRET_MESSAGE = "my passport number is X1234567"


@pytest.fixture(autouse=True)
def audit_on(monkeypatch, tmp_path):
    """Turn the audit log on, point it at a temp file and use the fake MCP client."""
    settings = audit.get_settings()
    path = tmp_path / "audit.log"
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(path))
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    FakeMcp.result = {"ok": True, "temperature_c": 30}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    audit.reset_audit_logger()
    yield path
    audit.reset_audit_logger()  # release the file so pytest can clean the temp folder


def read_events(path):
    """Read the audit file back as a list of dicts (one per line)."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_full_tool_run_is_recorded(monkeypatch, audit_on):
    script_gemini(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm in Goa.")])
    await run_agent("Weather in Goa?")
    events = read_events(audit_on)
    names = [e["event"] for e in events]
    assert names == ["request_received", "mcp_discovery", "model_decision", "tool_call", "model_decision", "request_completed"]
    # Every line has a timestamp and the same request ID.
    assert all(e["ts"].endswith("+00:00") for e in events)
    assert len({e["request_id"] for e in events}) == 1 and events[0]["request_id"] != "-"
    decision, tool = events[2], events[3]
    assert decision["decision"] == "tool_calls" and decision["tool_names"] == ["get_weather"]
    assert tool["tool"] == "get_weather" and tool["status"] == "ok" and tool["argument_names"] == ["location"]
    assert events[-1]["status"] == "ok" and "duration_ms" in events[-1]


async def test_message_text_and_arguments_are_not_logged(monkeypatch, audit_on):
    script_gemini(monkeypatch, [tool_reply("get_weather", {"location": "Secretville"}), text_reply("Reply text.")])
    await run_agent(SECRET_MESSAGE)
    raw = audit_on.read_text(encoding="utf-8")
    assert "passport" not in raw and "X1234567" not in raw  # message text
    assert "Secretville" not in raw  # tool argument value
    assert "Reply text" not in raw  # reply text
    first = read_events(audit_on)[0]
    assert first["message_chars"] == len(SECRET_MESSAGE) and len(first["message_sha256"]) == 12


async def test_tool_error_and_refused_tool_are_recorded(monkeypatch, audit_on):
    FakeMcp.result = {"ok": False, "error": "unavailable", "message": "down"}
    script_gemini(monkeypatch, [
        tool_reply("get_weather", {"location": "Goa"}),
        tool_reply("delete_everything", {}),
        text_reply("Sorry."),
    ])
    await run_agent("hi")
    tools = [e for e in read_events(audit_on) if e["event"] == "tool_call"]
    assert (tools[0]["status"], tools[0]["error_code"]) == ("error", "unavailable")
    assert (tools[1]["status"], tools[1]["error_code"]) == ("refused", "unknown_tool")


async def test_gemini_failure_logs_type_not_message(monkeypatch, audit_on):
    async def failing(contents, declarations):
        # An SDK-style error whose text carries a key: only the type may reach the log.
        raise AgentError("Gemini request failed") from RuntimeError(f"bad request key={FAKE_KEY}")

    monkeypatch.setattr(agent, "_generate", failing)
    with pytest.raises(AgentError):
        await run_agent("hi")
    raw = audit_on.read_text(encoding="utf-8")
    assert FAKE_KEY not in raw and "bad request" not in raw
    events = read_events(audit_on)
    assert events[-1]["event"] == "request_failed" and events[-1]["error_type"] == "RuntimeError"


async def test_mcp_down_is_recorded(monkeypatch, audit_on):
    FakeMcp.fail_connect = True
    script_gemini(monkeypatch, [text_reply("Plain answer.")])
    await run_agent("hi")
    discovery = next(e for e in read_events(audit_on) if e["event"] == "mcp_discovery")
    assert discovery["status"] == "error" and discovery["error_type"] == "McpClientError"


def test_sanitize_drops_secret_fields_and_redacts_values():
    clean = audit.sanitize({
        "api_key": "abc",
        "Authorization": "Bearer xyz",
        "note": f"failed with {FAKE_KEY}",
        "pair": "token=abc123",
        "ok_field": "fine",
        "obj": {"nested": "dict"},
    })
    assert "api_key" not in clean and "Authorization" not in clean
    assert FAKE_KEY not in clean["note"] and "abc123" not in clean["pair"]
    assert clean["ok_field"] == "fine" and clean["obj"] == "<dict>"


def test_real_configured_key_is_redacted(monkeypatch):
    monkeypatch.setattr(audit.get_settings(), "gemini_api_key", "short-key")
    assert audit.sanitize({"note": "oops short-key leaked"})["note"] == "oops [REDACTED] leaked"


async def test_unwritable_log_does_not_break_the_chat(monkeypatch, tmp_path):
    # The log path is a folder, so opening it as a file fails; the reply must still come back.
    monkeypatch.setattr(audit.get_settings(), "audit_log_path", str(tmp_path))
    audit.reset_audit_logger()
    script_gemini(monkeypatch, [text_reply("Still works.")])
    assert await run_agent("hi") == "Still works."


async def test_disabled_log_writes_nothing(monkeypatch, audit_on):
    monkeypatch.setattr(audit.get_settings(), "audit_log_enabled", False)
    script_gemini(monkeypatch, [text_reply("Hello.")])
    await run_agent("hi")
    assert not audit_on.exists()
