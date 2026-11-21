# debug_log.py: an OPTIONAL plain-text debug log for local testing (off by default). The JSON audit log (audit.py) is the
# real record and is not changed by this file; this one is easier to read while you test.
# DEBUG_LOG_FILE=true writes a rotating file (5 MB, 3 backups), one line per event:
#     2026-11-20 14:03:07,123 | INFO | agent.py | TOOL | rid=ab12 round=1 tool=search_trains status=ok error_code=- ms=812
# Labels: REQUEST (mode, mode_reason), TOOL (round, tool, status, error_code, ms), RESULT (tools ok, failed and skipped, counts,
# reply_chars) and ERROR. LOG_MESSAGES=true (only together with DEBUG_LOG_FILE) adds QUESTION (first 150 characters) and ANSWER
# (first 300), with email addresses and phone numbers masked.
# Safety rules: it is fed by the same audit events, so tool arguments are never available to it; field values go through the
# audit sanitizer (secret-named fields dropped, key-like text redacted); question and answer text is also redacted; an email
# draft (the answer in email mode) is never written; and a problem with the log can never raise into the chat.

import contextvars
import json
import logging
import re
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import get_settings
from app.core.audit import _KEY_SETTINGS, _SECRET_VALUE, IST, _request_id, sanitize

logger = logging.getLogger(__name__)

# Size limits: 5 MB per file, 3 old files kept.
MAX_BYTES = 5_000_000
BACKUP_COUNT = 3
# How much of a question and of an answer LOG_MESSAGES may write (after masking).
QUESTION_CHARS = 150
ANSWER_CHARS = 300
# The mode whose answer is an email draft: never written.
EMAIL_MODE = "email"

# Masks: an email address, and a phone number (7 or more digits, with an optional +, spaces, dots, dashes or brackets).
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{5,}\d(?![\w])")

# The shared debug logger, created on first use.
_logger: logging.Logger | None = None
# What this request's tools did so far, for the RESULT line (per-request state, never stored user data).
_state: contextvars.ContextVar[dict | None] = contextvars.ContextVar("debug_log_state", default=None)


def enabled() -> bool:
    """True when DEBUG_LOG_FILE is on."""
    return bool(get_settings().debug_log_file)


def messages_enabled() -> bool:
    """True when question and answer text may be written: LOG_MESSAGES on AND DEBUG_LOG_FILE on."""
    settings = get_settings()
    return bool(settings.debug_log_file and settings.log_messages)


def reset_debug_logger() -> None:
    """Close and forget the debug logger so the next line rebuilds it (used by tests)."""
    global _logger
    if _logger is not None:
        for handler in list(_logger.handlers):
            handler.close()
            _logger.removeHandler(handler)
    _logger = None


def _get_logger() -> logging.Logger:
    """Build the debug logger once: a rotating file with the plain-text format, kept out of the root logger."""
    global _logger
    if _logger is None:
        path = Path(get_settings().debug_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        log = logging.getLogger("trailwise.debug")
        log.setLevel(logging.INFO)
        log.propagate = False  # debug lines never mix with the app's normal logs
        handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(src)s | %(message)s")
        formatter.converter = lambda ts: datetime.fromtimestamp(ts, IST).timetuple()  # India time, like the audit log
        handler.setFormatter(formatter)
        log.addHandler(handler)
        _logger = log
    return _logger


def _text(value) -> str:
    """One value as plain text on one line: lists joined with commas, None and empty as "-", whitespace squeezed."""
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    if value is None or value == "":
        return "-"
    return " ".join(str(value).split()).replace(" ", "_")


def _write(level: int, src: str, label: str, **fields) -> None:
    """Write one line: LABEL | rid=... key=value ... The values are sanitized first. Never raises."""
    try:
        clean = sanitize(fields)  # secret-named fields dropped, key-like text redacted, long text cut
        pairs = " ".join(f"{k}={_text(v)}" for k, v in clean.items())
        _get_logger().log(level, f"{label} | rid={_request_id.get()} {pairs}".rstrip(), extra={"src": src})
    except Exception as exc:  # noqa: BLE001 - the debug log must never break a chat
        logger.warning("debug logging failed: %s", type(exc).__name__)


def _names(items: list[str]) -> list[str]:
    """The tool names in order of first use, without repeats."""
    return list(dict.fromkeys(items))


def debug_from_audit(event: str, status: str, fields: dict, src: str) -> None:
    """Turn one audit event into its debug line (REQUEST, TOOL, RESULT or ERROR); other events write nothing.

    Called by audit_event with the file name of the code that raised the event. Never raises.
    """
    try:
        if not enabled():
            return
        if event == "request_received":
            _state.set({"ok": [], "failed": [], "skipped": []})  # a new request starts with a clean tally
            _write(logging.INFO, src, "REQUEST", mode=fields.get("mode"), mode_reason=fields.get("mode_reason"), last_mode=fields.get("last_mode"))
        elif event == "tool_call":
            tool = str(fields.get("tool") or "-")
            bucket = {"ok": "ok", "error": "failed"}.get(status, "skipped")  # a skipped duplicate or a refused call is "skipped"
            tally = _state.get() or {"ok": [], "failed": [], "skipped": []}
            tally[bucket].append(tool)
            _state.set(tally)
            level = logging.INFO if status == "ok" else logging.WARNING
            _write(level, src, "TOOL", round=fields.get("round"), tool=tool, status=status, error_code=fields.get("error_code"), ms=fields.get("duration_ms"))
        elif event == "request_completed":
            tally = _state.get() or {"ok": [], "failed": [], "skipped": []}
            _write(
                logging.INFO, src, "RESULT",
                tools_ok=_names(tally["ok"]), tools_failed=_names(tally["failed"]), tools_skipped=_names(tally["skipped"]),
                calls=len(tally["ok"]) + len(tally["failed"]) + len(tally["skipped"]),
                ok=len(tally["ok"]), failed=len(tally["failed"]), skipped=len(tally["skipped"]),
                reply_chars=fields.get("reply_chars"), ms=fields.get("duration_ms"),
            )
        elif event in ("request_failed", "request_timeout") or (event == "model_decision" and status == "error"):
            # Only the kind of error, never its text (which could carry request details).
            _write(
                logging.ERROR, src, "ERROR", event=event, round=fields.get("round"), error_type=fields.get("error_type"),
                error_kind=fields.get("error_kind"), http_status=fields.get("http_status"), cap_s=fields.get("cap_s"),
                ms=fields.get("duration_ms"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("debug logging failed: %s", type(exc).__name__)


def mask(text: str) -> str:
    """Hide email addresses and phone numbers in free text."""
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))


def _redact_secrets(text: str) -> str:
    """Remove the real configured API key and anything that looks like a key, token or password from free text."""
    settings = get_settings()
    for name in _KEY_SETTINGS:
        real_key = getattr(settings, name, "")
        if real_key:
            text = text.replace(real_key, "[REDACTED]")
    return _SECRET_VALUE.sub("[REDACTED]", text)


def _one_line(text: str, limit: int) -> str:
    """Free text made safe for the log: secrets redacted, emails and phones masked, then cut to `limit` characters."""
    return " ".join(mask(_redact_secrets(text)).split())[:limit]


def log_question(message: str) -> None:
    """QUESTION line: the first 150 characters of the user's message. Only when LOG_MESSAGES and DEBUG_LOG_FILE are on."""
    try:
        if not messages_enabled():
            return
        _get_logger().info(
            f"QUESTION | rid={_request_id.get()} chars={len(message)} text={json.dumps(_one_line(message, QUESTION_CHARS), ensure_ascii=False)}",
            extra={"src": Path(sys._getframe(1).f_code.co_filename).name},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("debug logging failed: %s", type(exc).__name__)


def log_answer(reply: str, mode: str | None) -> None:
    """ANSWER line: the first 300 characters of the reply. An email draft is never written (only that it was left out)."""
    try:
        if not messages_enabled():
            return
        src = Path(sys._getframe(1).f_code.co_filename).name
        if mode == EMAIL_MODE:
            body = f"omitted=email_draft chars={len(reply)}"
        else:
            body = f"chars={len(reply)} text={json.dumps(_one_line(reply, ANSWER_CHARS), ensure_ascii=False)}"
        _get_logger().info(f"ANSWER | rid={_request_id.get()} {body}", extra={"src": src})
    except Exception as exc:  # noqa: BLE001
        logger.warning("debug logging failed: %s", type(exc).__name__)


def warn_if_debug_logging() -> None:
    """Startup warning when DEBUG_LOG_FILE or LOG_MESSAGES is on: these are for local testing only."""
    settings = get_settings()
    if settings.debug_log_file:
        logger.warning("DEBUG_LOG_FILE is on: a plain-text debug log is written to %s. Use it for local testing only.", settings.debug_log_path)
    if settings.log_messages:
        if settings.debug_log_file:
            logger.warning(
                "LOG_MESSAGES is on: the first %d characters of each question and %d of each answer (emails and phone numbers masked) "
                "go into the debug log. Turn it off before sharing any log.", QUESTION_CHARS, ANSWER_CHARS,
            )
        else:
            logger.warning("LOG_MESSAGES is on but has no effect unless DEBUG_LOG_FILE is also true.")
