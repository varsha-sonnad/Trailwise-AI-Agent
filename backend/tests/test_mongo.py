# test_mongo.py: tests for the shared MongoDB client, the startup ping and /ready.
# A fake client (see conftest.py) stands in for MongoDB, so no real Atlas connection is made.

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import MONGO_BAD_SCHEME_MESSAGE, MONGO_NOT_SET_MESSAGE, Settings, get_settings
from app.core import mongo
from app.main import app


def test_ready_ok_when_mongo_answers(fake_mongo):
    """/ready pings MongoDB and returns 200 when it answers."""
    with TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert "ping" in fake_mongo.commands


def test_ready_is_503_when_ping_fails(fake_mongo):
    """/ready returns 503 with a generic message when the ping fails."""
    fake_mongo.ok = False
    with TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"detail": "Not ready"}


def test_ready_is_503_when_ping_hangs(fake_mongo, monkeypatch):
    """A ping that never answers is cut off by the timeout instead of hanging /ready."""
    monkeypatch.setattr(mongo, "PING_TIMEOUT_SECONDS", 0.05)
    fake_mongo.hang = True
    with TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 503


def test_startup_warns_but_does_not_crash_when_mongo_is_down(fake_mongo, caplog):
    """A failed startup ping logs a warning and the app still starts; /health stays ok."""
    fake_mongo.ok = False
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    assert "startup ping" in caplog.text
    # The warning never contains the connection string or its password.
    assert "mongodb://" not in caplog.text and "mongodb+srv://" not in caplog.text


def test_startup_does_not_warn_when_mongo_is_up(caplog):
    """No warning when the startup ping succeeds."""
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            pass
    assert "startup ping" not in caplog.text


def test_client_is_created_once_and_closed_at_shutdown(fake_mongo):
    """The app holds one shared client on app.state, and closes it when the app stops."""
    with TestClient(app):
        assert app.state.mongo is fake_mongo
        assert fake_mongo.closed is False
    assert fake_mongo.closed is True


def test_client_is_closed_even_if_startup_ping_failed(fake_mongo):
    """Shutdown closes the client even when MongoDB was down at startup."""
    fake_mongo.ok = False
    with TestClient(app):
        pass
    assert fake_mongo.closed is True


def test_create_client_uses_settings_url_and_short_timeout(monkeypatch):
    """create_client passes the URL from settings (MONGODB_URI) and a short server timeout."""
    seen = {}

    def fake_async_client(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return "client"

    monkeypatch.setattr(mongo, "AsyncMongoClient", fake_async_client)
    settings = get_settings()
    assert mongo.create_client(settings) == "client"
    assert seen["url"] == settings.mongo_url
    assert seen["kwargs"]["serverSelectionTimeoutMS"] == mongo.SERVER_TIMEOUT_SECONDS * 1000


def test_create_client_is_lazy_and_makes_no_connection():
    """Building the real client does not touch the network (it connects on first use), and can be closed."""
    import asyncio

    async def build_and_close():
        client = mongo.create_client(get_settings())
        await client.close()

    asyncio.run(build_and_close())  # would hang or fail if it tried to connect


async def test_ping_returns_false_instead_of_raising(fake_mongo):
    """ping() turns any failure into False."""
    fake_mongo.ok = False
    assert await mongo.ping(fake_mongo) is False
    fake_mongo.ok = True
    assert await mongo.ping(fake_mongo) is True


def test_get_db_uses_settings_mongo_db(fake_mongo, monkeypatch):
    """get_db returns the database named by settings.mongo_db from the shared client."""
    monkeypatch.setattr(get_settings(), "mongo_db", "my_database")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(mongo=fake_mongo)))
    assert mongo.get_db(request) == {"fake_db": "my_database"}


def test_tests_never_use_a_real_client(fake_mongo):
    """Safety net: while the app runs in tests, its client is the fake one, not a real AsyncMongoClient."""
    with TestClient(app):
        assert type(app.state.mongo).__name__ == "FakeMongoClient"
    assert get_settings().mongo_url.startswith("mongodb://127.0.0.1:1/")  # dead port, never Atlas


# ---- MONGODB_URI is required: clear startup errors that never show the value ----


@pytest.fixture
def clean_settings(monkeypatch):
    """Settings built from scratch: no .env file is read, and no Mongo variable is set unless the test sets it.

    Without this, the project's real .env (which holds the Atlas string) would supply the value and hide the problem.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGO_URL", raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()  # do not leak the test's settings into other tests


def test_missing_uri_stops_startup_with_a_clear_error(clean_settings):
    """No MONGODB_URI at all: RuntimeError with the plain-language message."""
    with pytest.raises(RuntimeError) as err:
        get_settings()
    assert str(err.value) == MONGO_NOT_SET_MESSAGE


def test_empty_uri_stops_startup_with_the_same_error(clean_settings):
    """An empty MONGODB_URI counts as missing."""
    clean_settings.setenv("MONGODB_URI", "")
    with pytest.raises(RuntimeError) as err:
        get_settings()
    assert str(err.value) == MONGO_NOT_SET_MESSAGE


@pytest.mark.parametrize("bad", ["postgres://user:TOPSECRET@host/db", "http://example.com", "just-some-text", "mongo://u:TOPSECRET@h"])
def test_wrong_scheme_is_rejected_and_the_value_is_never_printed(clean_settings, bad):
    """Anything that is not mongodb:// or mongodb+srv:// is rejected; neither the message nor the traceback shows it."""
    import traceback

    clean_settings.setenv("MONGODB_URI", bad)
    with pytest.raises(RuntimeError) as err:
        get_settings()
    assert str(err.value) == MONGO_BAD_SCHEME_MESSAGE
    shown = "".join(traceback.format_exception(err.value))  # message plus the whole chained traceback
    assert "TOPSECRET" not in shown and bad not in shown
    assert err.value.__suppress_context__ is True  # the original pydantic error (which holds the value) is hidden


@pytest.mark.parametrize("good", ["mongodb+srv://u:p@cluster0.example.mongodb.net/trailwise", "mongodb://u:p@host1:27017,host2:27017/trailwise?replicaSet=rs0"])
def test_both_atlas_string_formats_are_accepted(clean_settings, good):
    """Atlas gives mongodb+srv:// strings, and also standard mongodb:// strings: both work."""
    clean_settings.setenv("MONGODB_URI", good)
    assert get_settings().mongo_url == good


def test_mongo_url_alias_still_works(clean_settings):
    """MONGO_URL is still accepted as a fallback name for MONGODB_URI."""
    clean_settings.setenv("MONGO_URL", "mongodb+srv://u:p@c.example.mongodb.net/db")
    assert get_settings().mongo_url == "mongodb+srv://u:p@c.example.mongodb.net/db"


def test_mongodb_uri_wins_over_the_alias(clean_settings):
    """If both are set, MONGODB_URI is used."""
    clean_settings.setenv("MONGODB_URI", "mongodb+srv://first@c.example.mongodb.net/db")
    clean_settings.setenv("MONGO_URL", "mongodb://second@h/db")
    assert get_settings().mongo_url.startswith("mongodb+srv://first@")


def test_other_missing_settings_keep_their_own_error(clean_settings):
    """A bad non-MongoDB setting is still reported by pydantic itself, not as a MongoDB error."""
    from pydantic import ValidationError

    clean_settings.setenv("MONGODB_URI", "mongodb+srv://u:p@c.example.mongodb.net/db")
    clean_settings.setenv("MAX_MESSAGE_CHARS", "not-a-number")
    with pytest.raises(ValidationError):
        get_settings()


def test_there_is_no_local_default_any_more():
    """The Settings class itself has no default for mongo_url (so nothing silently falls back to a local database)."""
    assert Settings.model_fields["mongo_url"].is_required()
