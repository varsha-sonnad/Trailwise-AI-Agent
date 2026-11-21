# audit.py: the audit log. Records what the agent does (request, the model's tool decisions, MCP tool
# calls, status, timing, token counts, OpenAI request ids, errors) as one JSON object per line in a rotating file.
# Safety rules built in: message text and tool arguments are never logged (only lengths, a short
# hash and argument names); secrets are dropped or redacted before writing; and logging can never
# raise into the chat, so an audit problem never breaks a reply.

import contextvars
import hashlib
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import get_settings

# Timestamps are written in India Standard Time (IST, UTC+5:30). A fixed offset is used on purpose: it needs no time zone
# database in the Docker image, and India has no daylight saving. Each line keeps its offset ("+05:30"), so it is
# unambiguous even next to older lines that were written in UTC ("+00:00").
IST = timezone(timedelta(hours=5, minutes=30), "IST")

# Request ID for the chat in progress. A ContextVar is per-request state, not stored user data (rule 1).
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("audit_request_id", default="-")

# Field names that must never be written, whatever their value.
_SECRET_FIELD = re.compile(r"key|token|secret|authorization|password|credential|cookie", re.I)
# Plain counts whose names contain "token" but are not secrets (token usage of one model call).
_SAFE_FIELDS = frozenset({"input_tokens", "output_tokens", "total_tokens", "cached_tokens"})
# OpenAI's own request id ("req_" and letters or digits). It is not our request_id. Its value is long enough to look
# like an opaque secret token, so a value that has exactly this shape is kept as it is; any other value is cleaned.
_OPENAI_REQUEST_ID = re.compile(r"req_[0-9A-Za-z]{8,64}")
_ID_FIELDS = frozenset({"openai_request_id"})
# The settings that hold real API keys: their values are redacted wherever they show up in a logged value.
_KEY_SETTINGS = ("openai_api_key",)
# Value shapes that look like secrets: Google/OpenAI-style keys, key=value pairs, long opaque tokens.
_SECRET_VALUE = re.compile(
    r"AIza[0-9A-Za-z_\-]{10,}|sk-[0-9A-Za-z_\-]{10,}|(?:key|token|secret|password)\s*[=:]\s*\S+|[A-Za-z0-9_\-]{32,}",
    re.I,
)
# Longest string kept in one field; longer values are cut.
MAX_VALUE_CHARS = 200

# The shared audit logger and its file handler, created on first use.
_logger: logging.Logger | None = None


def new_request_id() -> str:
    """Start a new request: create its ID, make it current and return it."""
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def message_fingerprint(message: str) -> dict:
    """Describe a user message without storing it: its length and a short SHA-256 prefix."""
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:12]
    return {"message_chars": len(message), "message_sha256": digest}


def _clean_value(value):
    """Make one field value safe: keep simple types, redact secret-like text, cut long text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value
        # First redact the real configured key (the OpenAI key) if it ever shows up, whatever shape they have.
        settings = get_settings()
        for name in _KEY_SETTINGS:
            real_key = getattr(settings, name, "")
            if real_key:
                text = text.replace(real_key, "[REDACTED]")
        # Then anything that merely looks like a secret.
        text = _SECRET_VALUE.sub("[REDACTED]", text)
        return text[:MAX_VALUE_CHARS]
    if isinstance(value, (list, tuple)):
        return [_clean_value(v) for v in list(value)[:20]]
    # Anything else (dicts, objects) is not logged: only its type name.
    return f"<{type(value).__name__}>"


def sanitize(fields: dict) -> dict:
    """Drop secret-named fields (except the safe counts) and clean every remaining value."""
    clean = {}
    for k, v in fields.items():
        if k not in _SAFE_FIELDS and _SECRET_FIELD.search(k):
            continue
        # An OpenAI request id with the expected shape is kept as it is; anything else in that field is cleaned.
        if k in _ID_FIELDS and isinstance(v, str) and _OPENAI_REQUEST_ID.fullmatch(v):
            clean[k] = v
        else:
            clean[k] = _clean_value(v)
    return clean


def _count(value) -> int | None:
    """A token count, or None when the value is not a plain non-negative integer."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def openai_request_id(source) -> str | None:
    """OpenAI's request id from a response (its `_request_id`) or an API error (its `request_id`), or None.

    Only a value shaped like a real id ("req_...") is returned, so nothing else can slip into the log through this door.
    """
    for name in ("_request_id", "request_id"):
        value = getattr(source, name, None)
        if isinstance(value, str) and _OPENAI_REQUEST_ID.fullmatch(value):
            return value
    return None


def usage_fields(response) -> dict:
    """Audit fields for one model response: OpenAI's request id and the token counts, ready for audit_event(**fields).

    Counts come from response.usage (input_tokens, output_tokens, total_tokens, and input_tokens_details.cached_tokens).
    A missing or odd value is left out, except cached_tokens, which is 0 when unknown. Never raises.
    """
    fields: dict = {}
    request_id = openai_request_id(response)
    if request_id:
        fields["openai_request_id"] = request_id
    usage = getattr(response, "usage", None)
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        count = _count(getattr(usage, name, None))
        if count is not None:
            fields[name] = count
    fields["cached_tokens"] = _count(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None)) or 0
    return fields


def reset_audit_logger() -> None:
    """Close and forget the audit logger so the next event rebuilds it (used by tests)."""
    global _logger
    if _logger is not None:
        for handler in list(_logger.handlers):
            handler.close()
            _logger.removeHandler(handler)
    _logger = None


def _get_logger() -> logging.Logger:
    """Build the audit logger once: a rotating file, its own logger, kept out of the root logger."""
    global _logger
    if _logger is None:
        settings = get_settings()
        path = Path(settings.audit_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("trailwise.audit")
        logger.setLevel(logging.INFO)
        logger.propagate = False  # audit lines never mix with the app's normal logs
        # 5 MB per file, 5 old files kept.
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))  # the message is already JSON
        logger.addHandler(handler)
        _logger = logger
    return _logger


def audit_event(event: str, status: str = "ok", **fields) -> None:
    """Write one audit line: timestamp, request ID, event name, status and the cleaned fields.

    Never raises: if the log cannot be written, the failure is noted in the app log (type only).
    The same event also feeds the optional plain-text debug log (debug_log.py), which has its own switch and cannot change
    anything written here.
    """
    try:
        from app.core.debug_log import debug_from_audit  # imported here: debug_log itself imports this module

        debug_from_audit(event, status, fields, Path(sys._getframe(1).f_code.co_filename).name)
    except Exception as exc:
        logging.getLogger(__name__).warning("debug logging failed: %s", type(exc).__name__)
    try:
        if not get_settings().audit_log_enabled:
            return
        record = {
            "ts": datetime.now(IST).isoformat(),  # IST, e.g. 2026-11-19T10:13:42.621234+05:30
            "request_id": _request_id.get(),
            "event": event,
            "status": status,
            **sanitize(fields),
        }
        _get_logger().info(json.dumps(record, ensure_ascii=False))
    except Exception as exc:
        logging.getLogger(__name__).warning("audit logging failed: %s", type(exc).__name__)
