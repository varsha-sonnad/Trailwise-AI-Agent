# trip_email.py: builds and sends the "email my saved trip" message (POST /trips/{id}/email).
#   build_trip_email  builds the subject and a plain-text body from a SAVED trip, on the server. No model is called, and the
#                     server adds no timings, distances or prices of its own: the text is the saved plan as it was shown,
#                     plus a fixed "verify before travel" footer (project rule 4).
#   send_trip_email   sends it by SMTP (aiosmtplib) to ONE address: the verified email of the signed-in user's token.
# The SMTP password, the recipient address, the subject and the body are never logged (only the error type).

import asyncio
import logging
from email.message import EmailMessage

import aiosmtplib
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.config import get_settings

logger = logging.getLogger(__name__)

# Give up on the SMTP server after this many seconds.
SMTP_TIMEOUT_SECONDS = 15.0
# Longest subject line, in characters.
MAX_SUBJECT_CHARS = 150
# Added to every email by the server (CLAUDE.md rule 4).
FOOTER = (
    "--\n"
    "Suggested connections, verify before travel. Transport details (trains, buses, roads, timings, fares) are indicative\n"
    "only and come from third-party sources. Trailwise is for planning only and does not book anything.\n"
    "Sent by Trailwise at your request."
)
_EMAIL = TypeAdapter(EmailStr)


class EmailNotConfigured(Exception):
    """The SMTP sender is not set up on this server."""


class EmailSendFailed(Exception):
    """The SMTP server did not accept the message."""


def valid_address(value: object) -> str | None:
    """The address if it is a well-formed email address (no line breaks or spaces), else None."""
    if not isinstance(value, str) or len(value) > 254 or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    try:
        return _EMAIL.validate_python(value)
    except ValidationError:
        return None


def mask(address: str) -> str:
    """Show only the first letter of an address, for the response, e.g. v***@example.com."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def build_trip_email(trip: dict) -> tuple[str, str]:
    """The (subject, plain-text body) for a saved trip dict {title, places, summary, reply}."""
    title = " ".join(str(trip.get("title", "")).split()) or "Your trip"  # one line: no line breaks in a header
    subject = f"Your Trailwise plan: {title}"[:MAX_SUBJECT_CHARS]
    parts = [f"Your Trailwise trip plan: {title}"]
    names = [p.get("name") for p in trip.get("places", []) if isinstance(p, dict) and p.get("name")]
    if names:
        parts.append("Places: " + ", ".join(names))
    if trip.get("summary"):
        parts.append(str(trip["summary"]))
    parts.append(str(trip.get("reply", "")))
    parts.append(FOOTER)
    return subject, "\n\n".join(parts)


async def send_trip_email(recipient: str, subject: str, body: str) -> None:
    """Send the message to the recipient. Raises EmailNotConfigured or EmailSendFailed (never returns an error value)."""
    settings = get_settings()
    sender = valid_address(settings.smtp_email)
    if sender is None or not settings.smtp_host:
        raise EmailNotConfigured
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            # Log in as the SMTP account only when a password is set (Mailpit needs no login).
            username=settings.smtp_email if settings.smtp_password else None,
            password=settings.smtp_password or None,
            use_tls=settings.smtp_port == 465,
            start_tls=settings.smtp_starttls,
            timeout=SMTP_TIMEOUT_SECONDS,
        )
    except (aiosmtplib.SMTPException, OSError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("trip email failed: %s", type(exc).__name__)  # the type only: the text could hold addresses
        raise EmailSendFailed from exc
