# test_email_tool.py: tests for the email tool and its registration in server.py.
# The SMTP client (aiosmtplib.send) is faked, so no mail server, password or network is needed.

import asyncio
import logging

import aiosmtplib
import pytest

from tools import email_tool

SENDER = "trailwise@example.com"
RECIPIENT = "traveller@example.com"
PASSWORD = "SMTP-SECRET-PASSWORD-123"


class FakeSend:
    """Stand-in for aiosmtplib.send: records the message and options, or raises."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def __call__(self, message, **options):
        self.calls.append({"message": message, "options": options})
        if self.error:
            raise self.error


@pytest.fixture
def setup(monkeypatch):
    """Fake SMTP settings and a helper to install a fake sender."""
    settings = email_tool.get_settings()
    for name, value in {
        "smtp_host": "smtp.test", "smtp_port": 1025, "smtp_password": "",
        "smtp_starttls": False, "smtp_email": SENDER, "verified_recipient": RECIPIENT,
    }.items():
        monkeypatch.setattr(settings, name, value)

    def install(sender=None):
        sender = sender or FakeSend()
        monkeypatch.setattr(email_tool.aiosmtplib, "send", sender)
        return sender

    return install


async def test_sends_to_the_fixed_recipient_with_footer(setup):
    sender = setup()
    result = await email_tool.send_trip_email("Goa plan", "Day 1: beach.\nDay 2: fort.", True)
    assert result["ok"] and result["sent"] is True
    # The reply masks the address; the real message went to the configured one only.
    assert result["to"] == "t***@example.com" and RECIPIENT not in str(result)
    message = sender.calls[0]["message"]
    assert message["To"] == RECIPIENT and message["From"] == SENDER and message["Subject"] == "Goa plan"
    body = message.get_content()
    assert "Day 1: beach." in body and "Verify before travel" in body  # footer added by the tool
    options = sender.calls[0]["options"]
    assert options["hostname"] == "smtp.test" and options["port"] == 1025
    assert options["username"] is None and options["password"] is None  # no login when not set
    assert options["use_tls"] is False and options["start_tls"] is False


async def test_login_and_tls_options(setup, monkeypatch):
    sender = setup()
    settings = email_tool.get_settings()
    monkeypatch.setattr(settings, "smtp_password", PASSWORD)
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_starttls", True)
    await email_tool.send_trip_email("Hi", "Body", True)
    options = sender.calls[-1]["options"]
    # With a password set, the tool logs in as the SMTP account (smtp_email).
    assert options["username"] == SENDER and options["password"] == PASSWORD
    assert options["start_tls"] is True and options["use_tls"] is False
    # Port 465 means TLS from the start.
    monkeypatch.setattr(settings, "smtp_port", 465)
    await email_tool.send_trip_email("Hi", "Body", True)
    assert sender.calls[-1]["options"]["use_tls"] is True


async def test_recipient_defaults_to_the_smtp_account(setup, monkeypatch):
    # With no VERIFIED_RECIPIENT, the email goes to the SMTP account itself (the only address in .env).
    sender = setup()
    monkeypatch.setattr(email_tool.get_settings(), "verified_recipient", "")
    result = await email_tool.send_trip_email("Plan", "Body", True)
    assert result["ok"] is True and sender.calls[0]["message"]["To"] == SENDER
    assert sender.calls[0]["message"]["From"] == SENDER


async def test_not_confirmed_sends_nothing(setup):
    sender = setup()
    result = await email_tool.send_trip_email("Goa plan", "Body", False)
    assert result["error"] == "not_confirmed" and sender.calls == []


@pytest.mark.parametrize(
    "subject,body,confirmed",
    [
        ("", "Body", True),
        ("   ", "Body", True),
        ("x" * 151, "Body", True),
        ("Hi\nBcc: evil@example.com", "Body", True),  # header injection
        ("Hi\r\nBcc: evil@example.com", "Body", True),
        ("Subject", "", True),
        ("Subject", "x" * 5001, True),
        ("Subject", "bad\x00body", True),
        ("Subject", "Body", "true"),  # not a real boolean
        ("Subject", "Body", 1),
    ],
)
async def test_bad_arguments(setup, subject, body, confirmed):
    sender = setup()
    result = await email_tool.send_trip_email(subject, body, confirmed)
    assert result["ok"] is False and result["error"] == "invalid_arguments" and sender.calls == []


async def test_multiline_body_with_tabs_is_allowed(setup):
    setup()
    assert (await email_tool.send_trip_email("Plan", "Day 1\n\tBeach\r\nDay 2", True))["ok"] is True


@pytest.mark.parametrize("name,value", [
    ("smtp_email", ""), ("smtp_email", "not-an-address"),
    ("verified_recipient", "a@b@c"), ("smtp_host", ""),
])
async def test_not_configured(setup, monkeypatch, name, value):
    sender = setup()
    monkeypatch.setattr(email_tool.get_settings(), name, value)
    result = await email_tool.send_trip_email("Plan", "Body", True)
    assert result["error"] == "not_configured" and sender.calls == []


@pytest.mark.parametrize("error", [
    aiosmtplib.SMTPException("down"),
    aiosmtplib.SMTPAuthenticationError(535, "bad login"),
    ConnectionRefusedError("refused"),
    asyncio.TimeoutError(),
])
async def test_smtp_failures_become_unavailable(setup, error):
    setup(FakeSend(error=error))
    result = await email_tool.send_trip_email("Plan", "Body", True)
    assert result["ok"] is False and result["error"] == "unavailable"


async def test_secrets_and_addresses_are_not_logged_or_returned(setup, monkeypatch, caplog):
    # The SMTP error text carries the password and the address, the worst case.
    monkeypatch.setattr(email_tool.get_settings(), "smtp_password", PASSWORD)
    setup(FakeSend(error=aiosmtplib.SMTPException(f"login failed for {RECIPIENT} password={PASSWORD}")))
    with caplog.at_level(logging.INFO):
        result = await email_tool.send_trip_email("Plan", "Secret trip body", True)
    assert result["ok"] is False
    assert PASSWORD not in caplog.text and RECIPIENT not in caplog.text and "Secret trip body" not in caplog.text
    assert PASSWORD not in str(result) and RECIPIENT not in str(result)


async def test_tool_registered_without_a_recipient_parameter():
    # The real MCP server lists the tool, and the model has no way to name a recipient.
    import server

    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "send_trip_email")
    assert set(tool.inputSchema["properties"]) == {"subject", "body", "confirmed"}
