# audit.py: the audit log. Records what the agent does (request, Gemini's tool decisions, MCP tool
# calls, status, timing, errors) as one JSON object per line in a rotating file.
# Safety rules built in: message text and tool arguments are never logged (only lengths, a short
# hash and argument names); secrets are dropped or redacted before writing; and logging can never
# raise into the chat, so an audit problem never breaks a reply.

import contextvars
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import get_settings

# Request ID for the chat in progress. A ContextVar is per-request state, not stored user data (rule 1).
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("audit_request_id", default="-")

# Field names that must never be written, whatever their value.
_SECRET_FIELD = re.compile(r"key|token|secret|authorization|password|credential|cookie", re.I)
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
        text = _SECRET_VALUE.sub("[REDACTED]", value)
        # Also redact the real Gemini key if it ever shows up, whatever shape it has.
        real_key = getattr(get_settings(), "gemini_api_key", "")
        if real_key:
            text = text.replace(real_key, "[REDACTED]")
        return text[:MAX_VALUE_CHARS]
    if isinstance(value, (list, tuple)):
        return [_clean_value(v) for v in list(value)[:20]]
    # Anything else (dicts, objects) is not logged: only its type name.
    return f"<{type(value).__name__}>"


def sanitize(fields: dict) -> dict:
    """Drop secret-named fields and clean every remaining value."""
    return {k: _clean_value(v) for k, v in fields.items() if not _SECRET_FIELD.search(k)}


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
    """
    try:
        if not get_settings().audit_log_enabled:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "request_id": _request_id.get(),
            "event": event,
            "status": status,
            **sanitize(fields),
        }
        _get_logger().info(json.dumps(record, ensure_ascii=False))
    except Exception as exc:
        logging.getLogger(__name__).warning("audit logging failed: %s", type(exc).__name__)
