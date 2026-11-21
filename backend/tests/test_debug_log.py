# test_debug_log.py: tests for the optional plain-text debug log (app/core/debug_log.py): off by default, the line format,
# the REQUEST, TOOL, RESULT and ERROR lines, QUESTION and ANSWER only with LOG_MESSAGES, masking, no secrets, rotation,
# the startup warnings, the JSON audit log staying the same, and the git/docker/compose files. The model call and the
# MCP client are faked and everything is written to temp files, so no key, network or real log is used.

import json
import logging
import re
from pathlib import Path

import pytest

from app.agent import agent
from app.agent.agent import AgentError, run_agent
from app.config import get_settings
from app.core import audit, debug_log
from tests.test_agent_tools import FakeMcp, script_model, text_reply, tool_calls_reply, tool_reply

ROOT = Path(__file__).resolve().parents[2]
# "2026-11-20 14:03:07,123 | INFO | agent.py | LABEL | rest"
LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \| (INFO|WARNING|ERROR) \| [\w.]+\.py \| (\w+) \| (.*)$")
FAKE_KEY = "AIzaSyFAKEKEYFORTESTING1234567890abcd"


@pytest.fixture(autouse=True)
def debug_on(monkeypatch, tmp_path):
    """Turn the debug log on with a temp file (LOG_MESSAGES off) and use the fake MCP client. Yields the log path."""
    settings = get_settings()
    path = tmp_path / "logs" / "debug.log"
    monkeypatch.setattr(settings, "debug_log_file", True)
    monkeypatch.setattr(settings, "log_messages", False)
    monkeypatch.setattr(settings, "debug_log_path", str(path))
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    FakeMcp.result = {"ok": True, "temperature_c": 30}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    debug_log.reset_debug_logger()
    yield path
    debug_log.reset_debug_logger()  # release the file so pytest can clean the temp folder


def lines(path):
    """The debug file as a list of (level, label, rest) tuples; every line must match the documented format."""
    out = []
    for text in path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(text)
        assert match, f"bad format: {text!r}"
        out.append((match.group(1), match.group(2), match.group(3)))
    return out


def by_label(path, label):
    """The rest of every line with this label."""
    return [rest for _, name, rest in lines(path) if name == label]


# ---------------------------------------------------------------- off by default

def test_both_settings_default_to_false_and_the_path_is_under_logs():
    fields = type(get_settings()).model_fields
    assert fields["debug_log_file"].default is False and fields["log_messages"].default is False
    assert Path(fields["debug_log_path"].default).parts[-2:] == ("logs", "debug.log")


async def test_nothing_is_written_when_debug_log_file_is_off(monkeypatch, debug_on):
    monkeypatch.setattr(get_settings(), "debug_log_file", False)
    monkeypatch.setattr(get_settings(), "log_messages", True)  # LOG_MESSAGES alone does nothing
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm.")])
    await run_agent("Weather in Goa?")
    assert not debug_on.exists() and not debug_on.parent.exists()  # not even the folder


# ---------------------------------------------------------------- the lines

async def test_a_tool_run_writes_request_tool_and_result_lines_in_the_format(monkeypatch, debug_on):
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm in Goa.")])
    await run_agent("Weather in Goa?", last_mode="explore")
    rows = lines(debug_on)
    assert [label for _, label, _ in rows] == ["REQUEST", "TOOL", "RESULT"]
    request, tool, result = (rest for _, _, rest in rows)
    assert re.fullmatch(r"rid=[0-9a-f]{12} mode=explore mode_reason=previous_mode last_mode=explore", request)
    assert re.fullmatch(r"rid=[0-9a-f]{12} round=1 tool=get_weather status=ok error_code=- ms=\d+", tool)
    assert re.fullmatch(
        r"rid=[0-9a-f]{12} tools_ok=get_weather tools_failed=- tools_skipped=- calls=1 ok=1 failed=0 skipped=0 reply_chars=12 ms=\d+", result
    )
    assert all(level == "INFO" for level, _, _ in rows)


async def test_the_mode_and_the_rule_name_are_in_the_request_line(monkeypatch, debug_on):
    script_model(monkeypatch, [text_reply("Which city are you leaving from?")])
    await run_agent("how can I go to Bangalore from Gulbarga", last_mode="explore")
    (request,) = by_label(debug_on, "REQUEST")
    assert "mode=route mode_reason=route_how_to_go last_mode=explore" in request


async def test_failed_skipped_and_ok_tools_are_counted_in_the_result_line(monkeypatch, debug_on):
    FakeMcp.result = {"ok": False, "error": "unavailable", "message": "down"}
    same = ("get_weather", {"location": "Goa"})
    script_model(monkeypatch, [tool_calls_reply(same, same, ("get_place_images", {"place": "Goa", "region": "India"})), text_reply("Done.")])
    await run_agent("Weather in Goa?")
    tools = by_label(debug_on, "TOOL")
    assert len(tools) == 3  # two real calls and one skipped duplicate
    assert any("status=error error_code=unavailable" in t for t in tools) and any("status=skipped error_code=duplicate" in t for t in tools)
    assert [level for level, label, _ in lines(debug_on) if label == "TOOL"] == ["WARNING", "WARNING", "WARNING"]
    (result,) = by_label(debug_on, "RESULT")
    # get_weather really failed; the duplicate and get_place_images (not offered by this fake server, so refused) are "skipped".
    assert "tools_ok=- tools_failed=get_weather tools_skipped=get_weather,get_place_images" in result
    assert "calls=3 ok=0 failed=1 skipped=2" in result


async def test_a_failed_request_writes_an_error_line_with_the_kind_not_the_text(monkeypatch, debug_on):
    async def boom(*a, **k):
        raise AgentError("SECRET-DETAIL-FROM-THE-PROVIDER", status_code=503, kind="overloaded")

    monkeypatch.setattr(agent, "_generate", boom)
    with pytest.raises(AgentError):
        await run_agent("Weather in Goa?")
    errors = [(level, rest) for level, label, rest in lines(debug_on) if label == "ERROR"]
    assert errors and all(level == "ERROR" for level, _ in errors)
    assert any("event=request_failed" in rest and "error_type=" in rest and "http_status=503" in rest and "error_kind=overloaded" in rest for _, rest in errors)
    assert "SECRET-DETAIL" not in debug_on.read_text(encoding="utf-8")
    assert "RESULT" not in [label for _, label, _ in lines(debug_on)]  # a failed request has no RESULT line


async def test_a_timeout_writes_an_error_line(monkeypatch, debug_on):
    monkeypatch.setattr(get_settings(), "chat_request_timeout_seconds", 0.05)
    import asyncio

    async def slow(*a, **k):
        await asyncio.sleep(1)

    monkeypatch.setattr(agent, "_generate", slow)
    await run_agent("Weather in Goa?")
    errors = by_label(debug_on, "ERROR")
    assert any("event=request_timeout" in e and "cap_s=0.05" in e for e in errors)


async def test_the_file_column_is_the_file_that_raised_the_event(monkeypatch, debug_on):
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm.")])
    await run_agent("Weather in Goa?")
    sources = {text.split(" | ")[2] for text in debug_on.read_text(encoding="utf-8").splitlines()}
    assert sources == {"agent.py"}
    audit.audit_event("tool_call", tool="x", round=1)  # raised from this test file
    assert "test_debug_log.py" in debug_on.read_text(encoding="utf-8").splitlines()[-1]


def test_the_time_is_india_time(debug_on):
    from datetime import datetime

    audit.audit_event("request_received", mode="explore", mode_reason="default")
    stamp = debug_on.read_text(encoding="utf-8").split(" | ")[0]
    written = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S,%f")
    now = datetime.now(audit.IST).replace(tzinfo=None)
    assert abs((now - written).total_seconds()) < 60


# ---------------------------------------------------------------- QUESTION and ANSWER (LOG_MESSAGES)

async def test_question_and_answer_lines_need_log_messages(monkeypatch, debug_on):
    script_model(monkeypatch, [text_reply("Which city?")])
    await run_agent("how can I go to Bangalore")
    assert "QUESTION" not in [label for _, label, _ in lines(debug_on)] and "ANSWER" not in [label for _, label, _ in lines(debug_on)]
    assert "Bangalore" not in debug_on.read_text(encoding="utf-8")  # no message text anywhere without LOG_MESSAGES


async def test_question_and_answer_are_cut_to_150_and_300_characters(monkeypatch, debug_on):
    monkeypatch.setattr(get_settings(), "log_messages", True)
    question = "hello world " * 30  # words with spaces: a long unbroken string would (rightly) be redacted as a token
    answer = "Which city? " + "abc def " * 100
    script_model(monkeypatch, [text_reply(answer)])
    await run_agent(question)
    (q,) = by_label(debug_on, "QUESTION")
    (a,) = by_label(debug_on, "ANSWER")
    q_text, a_text = json.loads(re.search(r"text=(\".*\")$", q).group(1)), json.loads(re.search(r"text=(\".*\")$", a).group(1))
    assert len(q_text) == 150 and q_text == question[:150]
    assert len(a_text) == 300 and a_text == answer[:300]
    assert f"chars={len(question)}" in q and f"chars={len(answer)}" in a
    order = [label for _, label, _ in lines(debug_on)]
    assert order == ["REQUEST", "QUESTION", "RESULT", "ANSWER"]


async def test_emails_and_phone_numbers_are_masked(monkeypatch, debug_on):
    monkeypatch.setattr(get_settings(), "log_messages", True)
    script_model(monkeypatch, [text_reply("Call +91 98765 43210 or write to guide.office@example.co.in, or 080-2345-6789.")])
    await run_agent("my mail is jane.doe+trip@example.com and my phone is (080) 2345 6789, pin 560001")
    text = debug_on.read_text(encoding="utf-8")
    for leaked in ("jane.doe", "example.com", "98765", "43210", "guide.office", "example.co.in", "2345", "6789"):
        assert leaked not in text
    assert "[email]" in text and "[phone]" in text and "pin 560001" in text  # a short number is not a phone number


async def test_secrets_in_a_question_or_answer_are_redacted(monkeypatch, debug_on):
    monkeypatch.setattr(get_settings(), "log_messages", True)
    real_key = get_settings().openai_api_key
    script_model(monkeypatch, [text_reply(f"The key is {real_key} and password=hunter2hunter2")])
    await run_agent(f"use {FAKE_KEY} and token: abc123 please")
    text = debug_on.read_text(encoding="utf-8")
    assert FAKE_KEY not in text and real_key not in text and "hunter2" not in text and "abc123" not in text
    assert "[REDACTED]" in text


async def test_an_email_draft_is_never_written(monkeypatch, debug_on):
    monkeypatch.setattr(get_settings(), "log_messages", True)
    draft = "Subject: Your Goa trip\n\nHi, here is the plan: Day 1 ..."
    script_model(monkeypatch, [text_reply(draft)])
    await run_agent("Email me this plan")
    (a,) = by_label(debug_on, "ANSWER")
    assert "omitted=email_draft" in a and f"chars={len(draft)}" in a
    assert "Your Goa trip" not in debug_on.read_text(encoding="utf-8")


async def test_tool_arguments_never_reach_the_debug_log(monkeypatch, debug_on):
    script_model(monkeypatch, [tool_reply("get_weather", {"location": "Zzyzx-Secret-Town", "api_key": "sk-abcdefghijklmnop"}), text_reply("Warm.")])
    await run_agent("Weather in Zzyzx?")
    text = debug_on.read_text(encoding="utf-8")
    assert "Zzyzx" not in text and "sk-abcdefghijklmnop" not in text


def test_secret_named_fields_are_dropped_and_key_like_values_redacted(debug_on):
    debug_log._write(logging.INFO, "x.py", "TOOL", api_key="plain", password="hunter2", token="t0k3n", note="key=abc123 and AIzaSyFAKEKEYFORTESTING1234567890abcd")
    text = debug_on.read_text(encoding="utf-8")
    assert "plain" not in text and "hunter2" not in text and "t0k3n" not in text and "abc123" not in text and "AIzaSy" not in text


# ---------------------------------------------------------------- the file itself

def test_the_file_rotates_at_5_mb_and_keeps_3_backups(debug_on):
    audit.audit_event("request_received", mode="explore")
    handler = debug_log._get_logger().handlers[0]
    assert handler.maxBytes == 5_000_000 and handler.backupCount == 3 and handler.baseFilename == str(debug_on)


def test_a_log_that_cannot_be_written_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(get_settings(), "debug_log_path", str(blocker / "sub" / "debug.log"))  # its "folder" is a file
    debug_log.reset_debug_logger()
    audit.audit_event("request_received", mode="explore")  # must not raise
    debug_log.log_question("hello")
    debug_log.log_answer("hello", "explore")


# ---------------------------------------------------------------- the JSON audit log is unchanged

async def test_the_json_audit_log_is_the_same_with_the_debug_log_on_or_off(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    results = {}
    for name, on in (("off", False), ("on", True)):
        monkeypatch.setattr(settings, "debug_log_file", on)
        monkeypatch.setattr(settings, "log_messages", on)
        monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / f"audit_{name}.log"))
        audit.reset_audit_logger()
        script_model(monkeypatch, [tool_reply("get_weather", {"location": "Goa"}), text_reply("Warm in Goa.")])
        await run_agent("Weather in Goa?")
        audit.reset_audit_logger()
        events = [json.loads(l) for l in (tmp_path / f"audit_{name}.log").read_text(encoding="utf-8").splitlines()]
        for event in events:
            for volatile in ("ts", "request_id", "duration_ms", "message_sha256"):
                event.pop(volatile, None)
        results[name] = events
    assert results["on"] == results["off"]
    assert "mode_reason" not in json.dumps([e for e in results["on"] if e["event"] != "request_received"])
    # No question or answer text and no debug-only label ever enters the JSON audit file.
    text = (tmp_path / "audit_on.log").read_text(encoding="utf-8")
    assert "Weather in Goa" not in text and "Warm in Goa" not in text and "QUESTION" not in text and "ANSWER" not in text


# ---------------------------------------------------------------- startup warnings

@pytest.mark.parametrize("debug, messages, expected", [
    (True, False, ["DEBUG_LOG_FILE is on"]),
    (True, True, ["DEBUG_LOG_FILE is on", "LOG_MESSAGES is on: the first 150"]),
    (False, True, ["LOG_MESSAGES is on but has no effect"]),
    (False, False, []),
])
def test_startup_warning_when_either_setting_is_on(monkeypatch, caplog, debug, messages, expected):
    monkeypatch.setattr(get_settings(), "debug_log_file", debug)
    monkeypatch.setattr(get_settings(), "log_messages", messages)
    with caplog.at_level(logging.WARNING):
        debug_log.warn_if_debug_logging()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == len(expected) and all(any(e in w for w in warnings) for e in expected)


def test_the_app_start_logs_the_warning(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(get_settings(), "log_messages", True)
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    assert any("DEBUG_LOG_FILE is on" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- git, docker and compose files

def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_gitignore_and_dockerignore_list_the_debug_files():
    for name in (".gitignore", ".dockerignore"):
        entries = read(name).splitlines()
        assert "debug-logs/" in entries and "docker-compose.override.yml" in entries


def test_only_the_override_file_mounts_the_debug_logs():
    override = read("docker-compose.override.yml")
    assert "./debug-logs:/app/logs" in override
    assert "debug-logs" not in read("docker-compose.yml") and "/app/logs" not in read("docker-compose.yml")


def test_env_example_has_both_placeholders_set_to_false():
    lines_ = read(".env.example").splitlines()
    assert "DEBUG_LOG_FILE=false" in lines_ and "LOG_MESSAGES=false" in lines_


def test_the_dockerfile_creates_the_logs_folder_for_the_non_root_user():
    dockerfile = read("backend/Dockerfile")
    assert "/app/logs" in dockerfile and dockerfile.index("/app/logs") < dockerfile.index("chown -R trailwise")
