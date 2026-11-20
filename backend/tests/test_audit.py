# test_audit.py: tests for the audit log (app/core/audit.py) and the agent's audit trail.
# The model call and the MCP client are faked, and the log goes to a temp file, so no key or network is used.

import ast
import json
import logging
from pathlib import Path

import httpx2
import openai
import pytest

from app.agent import agent
from app.agent.agent import AgentError, run_agent
from app.core import audit
from tests.test_agent_tools import FakeMcp, make_response, script_model, text_reply, tool_reply
from tests.test_openai_retry import REQUEST, use_openai

# A fake key in the Google-style shape (AIza...): the generic key patterns must still redact it.
FAKE_KEY = "AIzaSyFAKEKEYFORTESTING1234567890abcd"
# A fake OpenAI key, and a short odd-looking one that no pattern would catch (only the exact-value redaction can).
FAKE_OPENAI_KEY = "sk-proj-FAKEOPENAIKEYFORTESTING1234567890abcdef"
ODD_OPENAI_KEY = "oddkey99"
# The shape of an OpenAI request id: 36 characters, long enough to look like an opaque token.
REQUEST_ID = "req_0123456789abcdef0123456789abcdef"
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
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm in Goa.")])
    await run_agent("Weather in Goa?")
    events = read_events(audit_on)
    names = [e["event"] for e in events]
    assert names == ["request_received", "mcp_discovery", "model_decision", "tool_call", "model_decision", "request_completed"]
    # Every line has a timestamp and the same request ID.
    assert all(e["ts"].endswith("+05:30") for e in events)  # India Standard Time
    assert len({e["request_id"] for e in events}) == 1 and events[0]["request_id"] != "-"
    decision, tool = events[2], events[3]
    assert decision["decision"] == "tool_calls" and decision["tool_names"] == ["get_weather"]
    assert tool["tool"] == "get_weather" and tool["status"] == "ok" and tool["argument_names"] == ["location"]
    assert events[-1]["status"] == "ok" and "duration_ms" in events[-1]


async def test_message_text_and_arguments_are_not_logged(monkeypatch, audit_on):
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Secretville"}), text_reply("Reply text.")])
    await run_agent(SECRET_MESSAGE)
    raw = audit_on.read_text(encoding="utf-8")
    assert "passport" not in raw and "X1234567" not in raw  # message text
    assert "Secretville" not in raw  # tool argument value
    assert "Reply text" not in raw  # reply text
    first = read_events(audit_on)[0]
    assert first["message_chars"] == len(SECRET_MESSAGE) and len(first["message_sha256"]) == 12


async def test_tool_error_and_refused_tool_are_recorded(monkeypatch, audit_on):
    FakeMcp.result = {"ok": False, "error": "unavailable", "message": "down"}
    script_model(monkeypatch, [
        tool_reply("get_weather", {"location": "Goa"}),
        tool_reply("delete_everything", {}),
        text_reply("Sorry."),
    ])
    await run_agent("hi")
    tools = [e for e in read_events(audit_on) if e["event"] == "tool_call"]
    assert (tools[0]["status"], tools[0]["error_code"]) == ("error", "unavailable")
    assert (tools[1]["status"], tools[1]["error_code"]) == ("refused", "unknown_tool")


async def test_model_failure_logs_type_not_message(monkeypatch, audit_on):
    async def failing(input_items, tools, system_prompt, tool_choice=None):
        # An SDK-style error whose text carries a key: only the type may reach the log.
        raise AgentError("OpenAI request failed") from RuntimeError(f"bad request key={FAKE_KEY}")

    monkeypatch.setattr(agent, "_generate", failing)
    with pytest.raises(AgentError):
        await run_agent("hi")
    raw = audit_on.read_text(encoding="utf-8")
    assert FAKE_KEY not in raw and "bad request" not in raw
    events = read_events(audit_on)
    assert events[-1]["event"] == "request_failed" and events[-1]["error_type"] == "RuntimeError"


async def test_mcp_down_is_recorded(monkeypatch, audit_on):
    FakeMcp.fail_connect = True
    script_model(monkeypatch, [text_reply("Plain answer.")])
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


def test_cached_tokens_count_is_kept_but_token_named_secrets_are_still_dropped():
    """cached_tokens is a plain count and is written; any other field with "token" in its name is still dropped."""
    clean = audit.sanitize({"cached_tokens": 1024, "access_token": "abc", "tokens": "x"})
    assert clean == {"cached_tokens": 1024}


def test_real_configured_key_is_redacted(monkeypatch):
    monkeypatch.setattr(audit.get_settings(), "openai_api_key", "short-key")
    assert audit.sanitize({"note": "oops short-key leaked"})["note"] == "oops [REDACTED] leaked"


async def test_unwritable_log_does_not_break_the_chat(monkeypatch, tmp_path):
    # The log path is a folder, so opening it as a file fails; the reply must still come back.
    monkeypatch.setattr(audit.get_settings(), "audit_log_path", str(tmp_path))
    audit.reset_audit_logger()
    script_model(monkeypatch, [text_reply("Still works.")])
    assert await run_agent("hi") == "Still works."


async def test_disabled_log_writes_nothing(monkeypatch, audit_on):
    monkeypatch.setattr(audit.get_settings(), "audit_log_enabled", False)
    script_model(monkeypatch, [text_reply("Hello.")])
    await run_agent("hi")
    assert not audit_on.exists()


async def test_timestamps_are_in_ist_and_match_the_real_time(monkeypatch, audit_on):
    """The ts field is India Standard Time (UTC+5:30) and equals the real current moment, not a shifted clock."""
    from datetime import datetime, timedelta, timezone

    script_model(monkeypatch, [text_reply("Hello.")])
    before = datetime.now(timezone.utc)
    await run_agent("hi")
    after = datetime.now(timezone.utc)
    for event in read_events(audit_on):
        stamp = datetime.fromisoformat(event["ts"])
        assert stamp.utcoffset() == timedelta(hours=5, minutes=30)  # IST
        assert before - timedelta(seconds=1) <= stamp <= after + timedelta(seconds=1)  # the same instant as UTC now
        assert event["ts"][11:13] == f"{stamp.hour:02d}"  # the clock part shown is the IST wall-clock time


# ---------------------------------------------------------------- OpenAI key redaction

@pytest.mark.parametrize("key", [FAKE_OPENAI_KEY, ODD_OPENAI_KEY])
def test_real_openai_key_is_redacted_wherever_it_shows_up(monkeypatch, key):
    """The configured OpenAI key is replaced in strings, in lists and inside longer text, whatever its shape."""
    monkeypatch.setattr(audit.get_settings(), "openai_api_key", key)
    clean = audit.sanitize({
        "error": f"Incorrect API key provided: {key}. Check it.",
        "names": ["fine", f"x{key}y"],
        "note": key,
    })
    assert key not in json.dumps(clean)
    assert "[REDACTED]" in clean["error"] and clean["names"][0] == "fine" and clean["note"] == "[REDACTED]"


def test_openai_shaped_keys_are_redacted_even_when_not_configured():
    """A key that only looks like an OpenAI key (sk-...) is caught by the pattern, with no settings involved."""
    assert "[REDACTED]" in audit.sanitize({"note": f"oops {FAKE_OPENAI_KEY} leaked"})["note"]


def test_an_empty_configured_key_does_not_blank_every_value(monkeypatch):
    """An unset key (empty string) must not be 'redacted' everywhere."""
    monkeypatch.setattr(audit.get_settings(), "openai_api_key", "")
    assert audit.sanitize({"note": "plain text"})["note"] == "plain text"


# ---------------------------------------------------------------- request ids and token counts

def test_token_counts_and_the_openai_request_id_are_kept():
    """The usage counts (including cached) and OpenAI's request id are written; token-named secrets still are not."""
    clean = audit.sanitize({
        "input_tokens": 1500, "output_tokens": 20, "total_tokens": 1520, "cached_tokens": 1024,
        "openai_request_id": REQUEST_ID,
        "access_token": "abc", "refresh_token": "def", "tokens": "x",
    })
    assert clean == {"input_tokens": 1500, "output_tokens": 20, "total_tokens": 1520, "cached_tokens": 1024,
                     "openai_request_id": REQUEST_ID}


@pytest.mark.parametrize("value", ["req_0123456789abcdef0123456789abcdef; key=secret", "req_" + "a" * 80, "abc" * 20])
def test_a_value_that_is_not_a_request_id_is_cleaned_not_trusted(value):
    """The id field is exempt only when the value has the exact shape of an id; anything else is cleaned as usual."""
    out = audit.sanitize({"openai_request_id": value})["openai_request_id"]
    assert out != value and "secret" not in out


def test_usage_fields_reads_a_real_sdk_response():
    """usage_fields() takes the counts from usage and the request id from the response, ready for audit_event."""
    response = make_response([], cached=1024)
    response._request_id = REQUEST_ID
    assert audit.usage_fields(response) == {
        "openai_request_id": REQUEST_ID, "input_tokens": 1500, "output_tokens": 20, "total_tokens": 1520, "cached_tokens": 1024,
    }


def test_usage_fields_with_nothing_reported_gives_zero_cached_and_never_raises():
    """No usage block, no request id: only cached_tokens (0). Odd values are skipped, not crashed on."""
    assert audit.usage_fields(make_response([])) == {"cached_tokens": 0}
    assert audit.usage_fields(None) == {"cached_tokens": 0}
    assert audit.usage_fields(object()) == {"cached_tokens": 0}
    odd = make_response([], cached=5)
    odd._request_id = "something else"
    assert "openai_request_id" not in audit.usage_fields(odd)


def test_openai_request_id_from_an_api_error():
    """An API error carries the id as request_id (taken from the x-request-id header); anything else gives None."""
    response = httpx2.Response(429, request=REQUEST, headers={"x-request-id": REQUEST_ID}, json={"error": {}})
    assert audit.openai_request_id(openai.RateLimitError("x", response=response, body=None)) == REQUEST_ID
    assert audit.openai_request_id(RuntimeError("x")) is None


def test_usage_fields_are_written_to_the_audit_file(audit_on):
    """Through the real writer: the request id and all four counts reach the file as written."""
    response = make_response([], cached=1024)
    response._request_id = REQUEST_ID
    audit.audit_event("model_decision", decision="final_answer", **audit.usage_fields(response))
    line = json.loads(audit_on.read_text(encoding="utf-8").splitlines()[-1])
    assert line["openai_request_id"] == REQUEST_ID
    assert (line["input_tokens"], line["output_tokens"], line["total_tokens"], line["cached_tokens"]) == (1500, 20, 1520, 1024)


# ---------------------------------------------------------------- no key in any log

async def test_no_key_appears_in_the_audit_file_or_any_log_after_a_failed_call(monkeypatch, audit_on, caplog):
    """A failing OpenAI call whose error text carries the key: neither the audit file nor any captured log has it."""
    monkeypatch.setattr(audit.get_settings(), "openai_api_key", FAKE_OPENAI_KEY)
    caplog.set_level(logging.DEBUG)  # capture every logger at every level
    text = f"Incorrect API key provided: {FAKE_OPENAI_KEY}. Authorization: Bearer {FAKE_OPENAI_KEY}"
    response = httpx2.Response(401, request=REQUEST, headers={"x-request-id": REQUEST_ID},
                               json={"error": {"code": "invalid_api_key", "message": text}})
    error = openai.AuthenticationError(text, response=response, body={"code": "invalid_api_key", "message": text})
    use_openai(monkeypatch, [error])
    with pytest.raises(AgentError):
        await run_agent("hi")
    raw = audit_on.read_text(encoding="utf-8")
    assert FAKE_OPENAI_KEY not in raw and "Bearer" not in raw
    assert FAKE_OPENAI_KEY not in caplog.text
    last = read_events(audit_on)[-1]
    assert last["event"] == "request_failed" and last["http_status"] == 401 and last["error_kind"] == "auth"


async def test_no_key_appears_in_the_audit_file_or_any_log_after_a_good_call(monkeypatch, audit_on, caplog):
    """A normal tool run with the key configured: the key is nowhere in the audit file or the captured logs."""
    monkeypatch.setattr(audit.get_settings(), "openai_api_key", FAKE_OPENAI_KEY)
    caplog.set_level(logging.DEBUG)
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm in Goa.")])
    await run_agent("Weather in Goa?")
    assert FAKE_OPENAI_KEY not in audit_on.read_text(encoding="utf-8")
    assert FAKE_OPENAI_KEY not in caplog.text


def test_even_a_key_put_in_an_audit_field_by_mistake_is_not_written(monkeypatch, audit_on):
    """Belt and braces: if code ever passes the key (or a key-named field) to audit_event, the file still has no key."""
    monkeypatch.setattr(audit.get_settings(), "openai_api_key", ODD_OPENAI_KEY)
    audit.audit_event("probe", note=f"value {ODD_OPENAI_KEY}", openai_api_key=ODD_OPENAI_KEY, extra=[ODD_OPENAI_KEY])
    raw = audit_on.read_text(encoding="utf-8")
    assert ODD_OPENAI_KEY not in raw and "openai_api_key" not in raw


def test_the_openai_and_http_loggers_are_set_to_warning_in_the_mcp_server():
    """mcp_server/server.py keeps the openai, httpx, httpx2 and httpcore loggers at WARNING (read from its source)."""
    source = (Path(__file__).resolve().parents[2] / "mcp_server" / "server.py").read_text(encoding="utf-8")
    quiet = set()
    for node in ast.walk(ast.parse(source)):
        # The module-level loop: for _name in (...): logging.getLogger(_name).setLevel(logging.WARNING)
        if isinstance(node, ast.For) and "setLevel(logging.WARNING)" in ast.unparse(node):
            quiet |= {c.value for c in ast.walk(node.iter) if isinstance(c, ast.Constant)}
    assert {"openai", "httpx", "httpx2", "httpcore"} <= quiet
