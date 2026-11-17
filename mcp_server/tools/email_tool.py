# email_tool.py: the email tool. Sends a trip plan (subject + plain-text body) by SMTP to ONE fixed address.
# Flow: validate the arguments -> check the user confirmed -> check the SMTP settings -> send.
# Safety: the tool has NO recipient argument. The only address it can send to comes from the project-root
# .env (VERIFIED_RECIPIENT, or the SMTP_EMAIL account itself when that is not set), so the model (or a
# prompt injection) cannot choose who gets the email. Until login exists, that address stands in for
# "the user's verified address". Sender and login name are SMTP_EMAIL, the password is SMTP_PASSWORD.
# No authentication and no rate limit yet (a later step). It never raises into the agent loop: every
# failure becomes {"ok": False, "error": ...}. The SMTP password, the message body and the full
# recipient address are never logged. (This file is email_tool.py, not email.py, so it does not shadow
# Python's standard "email" package.)

import asyncio
import logging
from email.message import EmailMessage

import aiosmtplib
from pydantic import BaseModel, EmailStr, StrictBool, TypeAdapter, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# Give up on the SMTP server after this many seconds.
SMTP_TIMEOUT_SECONDS = 15.0
# Length limits for the model-supplied text.
MAX_SUBJECT_CHARS = 150
MAX_BODY_CHARS = 5000
# Added to every email by the tool itself (CLAUDE.md rule 4), whatever the model wrote.
FOOTER = "\n\n--\nSent by Trailwise. Transport details are indicative only. Verify before travel."
# Checks that an address is well formed (needs the email-validator package).
_EMAIL = TypeAdapter(EmailStr)


def _has_control_chars(text: str, allowed: str = "") -> bool:
    """True if the text has control characters (line breaks, NUL, ...) other than the allowed ones."""
    return any((ord(ch) < 32 or ord(ch) == 127) and ch not in allowed for ch in text)


class EmailArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    subject: str
    body: str
    # StrictBool: only a real true/false counts, not the text "yes" or "true".
    confirmed: StrictBool

    @field_validator("subject")
    @classmethod
    def clean_subject(cls, value: str) -> str:
        """1 to 150 characters on one line. A line break would let a caller inject extra mail headers."""
        value = value.strip()
        if not 1 <= len(value) <= MAX_SUBJECT_CHARS:
            raise ValueError("subject must be 1 to 150 characters")
        if _has_control_chars(value):
            raise ValueError("subject must be a single line")
        return value

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        """1 to 5000 characters of plain text. Line breaks and tabs are fine; other control characters are not."""
        value = value.strip()
        if not 1 <= len(value) <= MAX_BODY_CHARS:
            raise ValueError("body must be 1 to 5000 characters")
        if _has_control_chars(value, allowed="\n\r\t"):
            raise ValueError("body contains invalid characters")
        return value


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _mask(address: str) -> str:
    """Show only the first letter of an address, for the reply, e.g. v***@example.com."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


async def send_trip_email(subject: str, body: str, confirmed: bool) -> dict:
    """Email a trip plan to the user's own verified address (it cannot be sent anywhere else).

    Only call this after the user clearly asked for the email AND confirmed the subject and body you
    showed them; then pass confirmed=true. Plain text only. Returns {"ok": True, ...} when sent, or
    {"ok": False, "error": ..., "message": ...} when it cannot.
    """
    # 1. Validate the model-supplied arguments.
    try:
        args = EmailArgs(subject=subject, body=body, confirmed=confirmed)
    except ValidationError:
        return _error(
            "invalid_arguments",
            "Give a one-line subject (1 to 150 characters), a plain-text body (1 to 5000 characters) and confirmed=true or false.",
        )

    # 2. The user must have confirmed the draft (a prompt-level safeguard: the fixed recipient is the real one).
    if not args.confirmed:
        return _error("not_confirmed", "Show the user the subject and body and ask them to confirm before sending.")

    # 3. The sender, the recipient and the SMTP server come from the project-root .env.
    #    The sender is the SMTP account; the recipient is VERIFIED_RECIPIENT, or that same account if unset.
    settings = get_settings()
    try:
        sender = _EMAIL.validate_python(settings.smtp_email)
        recipient = _EMAIL.validate_python(settings.verified_recipient or settings.smtp_email)
    except ValidationError:
        return _error("not_configured", "Email is not configured.")
    if not settings.smtp_host:
        return _error("not_configured", "Email is not configured.")

    # 4. Build a plain-text message. The tool adds the footer itself.
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = args.subject
    message.set_content(args.body + FOOTER)

    # 5. Send it. Port 465 means TLS from the start; otherwise STARTTLS is used only if enabled.
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
    except (aiosmtplib.SMTPException, OSError, asyncio.TimeoutError) as exc:
        # Only the error type is logged: the message text could carry credentials or addresses.
        logger.warning("email send failed: %s", type(exc).__name__)
        return _error("unavailable", "The email could not be sent right now.")

    return {
        "ok": True,
        "sent": True,
        "to": _mask(recipient),
        "subject": args.subject,
        "message": "The email was sent to the user's verified address.",
        "source": "smtp",
    }
