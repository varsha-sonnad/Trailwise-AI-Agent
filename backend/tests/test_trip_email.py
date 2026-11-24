# test_trip_email.py: tests for POST /trips/{id}/email (email a SAVED trip to the verified address in the token).
# Fake MongoDB, fake sign-in (the claims are chosen per test), fake Redis and a fake SMTP send: nothing leaves the machine.

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import auth, rate_limit
from app.core.auth import Principal, get_optional_principal, require_user
from app.core.mongo import get_db
from app.main import app
from app.services import trip_email
from tests.test_trips import FakeDb, trip

OWNER_EMAIL = "alice@example.com"


class FakeRedis:
    """An in-memory Redis with the three calls rate_limit uses (a shared dict, no expiry clock)."""

    store: dict = {}
    fail = False

    async def incr(self, key):
        if FakeRedis.fail:
            raise ConnectionError("redis://secret-host is down")
        FakeRedis.store[key] = FakeRedis.store.get(key, 0) + 1
        return FakeRedis.store[key]

    async def expire(self, key, seconds):
        return True

    async def ttl(self, key):
        return 1800

    async def aclose(self):
        return None


@pytest.fixture
def api(monkeypatch):
    """A client with a fake database, a fake Redis, a recorded fake SMTP send, and api.login(id, email=, verified=)."""
    db = FakeDb()
    state = {"user": None, "email": OWNER_EMAIL, "verified": True}
    sent = []
    FakeRedis.store, FakeRedis.fail = {}, False

    async def fake_require_user():
        if state["user"] is None:
            raise auth._unauthorized()
        return Principal(kind="user", id=state["user"], email=state["email"], email_verified=state["verified"])

    async def fake_send(recipient, subject, body):
        sent.append({"to": recipient, "subject": subject, "body": body})

    monkeypatch.setattr(rate_limit.redis, "from_url", lambda *a, **k: FakeRedis())
    monkeypatch.setattr(trip_email, "send_trip_email", fake_send)
    app.dependency_overrides[require_user] = fake_require_user
    app.dependency_overrides[get_optional_principal] = fake_require_user
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        client.sent, client.db, client.state = sent, db, state
        client.login = lambda user, email=OWNER_EMAIL, verified=True: state.update(user=user, email=email, verified=verified)
        yield client
    app.dependency_overrides.clear()


def saved(api, user="alice", **over):
    """Sign in as this user and save a trip; returns its id."""
    api.login(user)
    return api.post("/trips", json=trip(**over)).json()["id"]


def error(response):
    """The detail object of a failed email response."""
    return response.json()["detail"]


# ------------------------------------------------------------------ who may email what

def test_guests_and_anonymous_get_401(api):
    assert api.post("/trips/" + "a" * 32 + "/email").status_code == 401
    assert api.sent == []


def test_a_saved_trip_is_sent_to_the_token_email_only(api):
    trip_id = saved(api, title="Hampi weekend", summary="Ruins", reply="Day 1: sunrise at the temple.")
    r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 200 and r.json() == {"sent": True, "to": "a***@example.com"}
    assert len(api.sent) == 1 and api.sent[0]["to"] == OWNER_EMAIL
    assert api.sent[0]["subject"] == "Your Trailwise plan: Hampi weekend"
    body = api.sent[0]["body"]
    assert "Day 1: sunrise at the temple." in body and "Places: Hampi" in body and "Ruins" in body
    assert "verify before travel" in body.lower() and "does not book" in body


def test_the_recipient_cannot_be_chosen_by_the_request(api):
    trip_id = saved(api)
    api.post(f"/trips/{trip_id}/email?to=evil@example.com&email=evil@example.com", json={"to": "evil@example.com", "email": "evil@example.com"})
    assert [m["to"] for m in api.sent] == [OWNER_EMAIL]


def test_an_unverified_email_is_refused_and_does_not_count(api):
    trip_id = saved(api)
    api.login("alice", verified=False)
    r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 403 and error(r) == {"sent": False, "error": "email_not_verified", "message": error(r)["message"]}
    assert api.sent == [] and FakeRedis.store == {}  # nothing sent, no attempt counted


@pytest.mark.parametrize("address", [None, "", "not-an-email", "a@b.com\nBcc: x@y.com", "a b@example.com"])
def test_a_missing_or_malformed_token_email_is_refused(api, address):
    trip_id = saved(api)
    api.login("alice", email=address, verified=True)
    r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 403 and error(r)["error"] == "email_not_verified" and api.sent == []


@pytest.mark.parametrize("claims, expected", [
    ({"email_verified": True}, True), ({"email_verified": False}, False), ({"email_verified": "true"}, False),
    ({"email_verified": 1}, False), ({}, False),
])
async def test_email_verified_must_be_exactly_true_in_the_token(monkeypatch, claims, expected):
    async def fake_verify(token):
        return {"sub": "alice", "email": OWNER_EMAIL, **claims}

    monkeypatch.setattr(auth, "verify_token", fake_verify)
    principal = await auth.get_optional_principal(authorization="Bearer abc", x_session_id=None)
    assert principal.email_verified is expected


# ------------------------------------------------------------------ the trip must exist and be yours

def test_another_users_trip_is_a_404_and_sends_nothing(api):
    trip_id = saved(api, user="alice")
    api.login("bob", email="bob@example.com")
    assert api.post(f"/trips/{trip_id}/email").status_code == 404
    assert api.sent == [] and FakeRedis.store == {}


@pytest.mark.parametrize("trip_id", ["f" * 32, "not-an-id", "%24ne"])
def test_an_unsaved_or_malformed_trip_id_is_a_404(api, trip_id):
    api.login("alice")
    assert api.post(f"/trips/{trip_id}/email").status_code == 404 and api.sent == []


def test_a_database_failure_is_a_generic_503(api):
    from pymongo.errors import PyMongoError

    async def boom(*a, **k):
        raise PyMongoError("mongodb+srv://user:secret@host")

    trip_id = saved(api)
    api.db["trips"].find_one = boom
    r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 503 and "secret" not in r.text and api.sent == []


# ------------------------------------------------------------------ the limit: 5 per user per hour

def test_the_sixth_email_in_an_hour_is_refused_with_retry_after(api):
    trip_id = saved(api)
    for _ in range(5):
        assert api.post(f"/trips/{trip_id}/email").status_code == 200
    r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 429 and error(r)["error"] == "rate_limited" and error(r)["sent"] is False
    assert r.headers["Retry-After"] == "1800" and len(api.sent) == 5


def test_the_limit_is_per_user(api):
    trip_id = saved(api, user="alice")
    for _ in range(6):
        api.post(f"/trips/{trip_id}/email")
    bob_trip = saved(api, user="bob")
    api.login("bob", email="bob@example.com")
    assert api.post(f"/trips/{bob_trip}/email").status_code == 200
    assert [m["to"] for m in api.sent].count("bob@example.com") == 1


def test_when_redis_is_down_nothing_is_sent(api, caplog):
    trip_id = saved(api)
    FakeRedis.fail = True
    with caplog.at_level("WARNING"):
        r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 503 and error(r)["error"] == "email_unavailable" and api.sent == []
    assert "secret-host" not in caplog.text and "ConnectionError" in caplog.text


# ------------------------------------------------------------------ a truthful result

def test_a_send_failure_says_sent_false_with_a_specific_error(api, monkeypatch):
    async def failing(recipient, subject, body):
        raise trip_email.EmailSendFailed

    trip_id = saved(api)
    monkeypatch.setattr(trip_email, "send_trip_email", failing)
    r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 502 and error(r)["sent"] is False and error(r)["error"] == "email_send_failed"


def test_missing_smtp_setup_says_so(api, monkeypatch):
    async def unset(recipient, subject, body):
        raise trip_email.EmailNotConfigured

    trip_id = saved(api)
    monkeypatch.setattr(trip_email, "send_trip_email", unset)
    r = api.post(f"/trips/{trip_id}/email")
    assert r.status_code == 503 and error(r)["error"] == "email_not_configured"


# ------------------------------------------------------------------ the message and the SMTP call

def test_the_body_is_built_only_from_the_saved_trip():
    saved_trip = {"title": "Pune to Goa", "places": [{"name": "Pune"}, {"name": "Goa"}], "summary": "", "reply": "Take the train."}
    subject, body = trip_email.build_trip_email(saved_trip)
    assert subject == "Your Trailwise plan: Pune to Goa"
    assert body == "\n\n".join(["Your Trailwise trip plan: Pune to Goa", "Places: Pune, Goa", "Take the train.", trip_email.FOOTER])
    assert "verify before travel" in body.lower()


def test_a_title_with_line_breaks_cannot_inject_headers():
    subject, _ = trip_email.build_trip_email({"title": "Trip\r\nBcc: x@y.com", "reply": "r"})
    assert "\n" not in subject and "\r" not in subject and len(subject) <= trip_email.MAX_SUBJECT_CHARS


async def test_the_smtp_message_goes_to_the_recipient_from_the_sender(monkeypatch):
    seen = {}

    async def fake_smtp(message, **kwargs):
        seen["message"], seen["kwargs"] = message, kwargs

    monkeypatch.setattr(trip_email.aiosmtplib, "send", fake_smtp)
    monkeypatch.setattr(trip_email, "get_settings", lambda: SimpleNamespace(
        smtp_email="noreply@example.com", smtp_password="pw", smtp_host="mail.example.com", smtp_port=587, smtp_starttls=True))
    await trip_email.send_trip_email("alice@example.com", "Subject", "Body text")
    message = seen["message"]
    assert message["To"] == "alice@example.com" and message["From"] == "noreply@example.com" and message["Cc"] is None
    assert message.get_content_type() == "text/plain" and "Body text" in message.get_content()
    assert seen["kwargs"]["username"] == "noreply@example.com" and seen["kwargs"]["start_tls"] is True and seen["kwargs"]["use_tls"] is False


async def test_smtp_failures_become_send_failed_and_log_only_the_type(monkeypatch, caplog):
    async def fake_smtp(message, **kwargs):
        raise OSError("connect to mail.secret.example failed for alice@example.com")

    monkeypatch.setattr(trip_email.aiosmtplib, "send", fake_smtp)
    monkeypatch.setattr(trip_email, "get_settings", lambda: SimpleNamespace(
        smtp_email="noreply@example.com", smtp_password="", smtp_host="localhost", smtp_port=1025, smtp_starttls=False))
    with caplog.at_level("WARNING"), pytest.raises(trip_email.EmailSendFailed):
        await trip_email.send_trip_email("alice@example.com", "Subject", "Body text")
    assert "OSError" in caplog.text and "alice@example.com" not in caplog.text and "secret" not in caplog.text


@pytest.mark.parametrize("sender", ["", "not-an-address"])
async def test_an_invalid_sender_means_not_configured(monkeypatch, sender):
    monkeypatch.setattr(trip_email, "get_settings", lambda: SimpleNamespace(
        smtp_email=sender, smtp_password="", smtp_host="localhost", smtp_port=1025, smtp_starttls=False))
    with pytest.raises(trip_email.EmailNotConfigured):
        await trip_email.send_trip_email("alice@example.com", "Subject", "Body")


# ------------------------------------------------------------------ the rate-limit helper itself

async def test_hit_counts_attempts_and_reports_the_window(monkeypatch):
    FakeRedis.store, FakeRedis.fail = {}, False
    monkeypatch.setattr(rate_limit.redis, "from_url", lambda *a, **k: FakeRedis())
    results = [await rate_limit.hit("k", 2, 3600) for _ in range(3)]
    assert [allowed for allowed, _ in results] == [True, True, False] and results[0][1] == 1800
