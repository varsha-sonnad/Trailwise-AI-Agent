# conftest.py: shared test setup. pytest loads this file automatically before running any test.

import os

# Environment values must be set before app.config is imported.
# Keep the audit log off in tests so they never write to the real log file (test_audit.py turns it on).
os.environ.setdefault("AUDIT_LOG_ENABLED", "false")
# The optional debug log is off too, whatever the real .env says (test_debug_log.py turns it on with a temp file).
os.environ.setdefault("DEBUG_LOG_FILE", "false")
os.environ.setdefault("LOG_MESSAGES", "false")
# MONGODB_URI is required (the app will not start without it), so tests give it a fake value: a dead local port.
# A test can therefore never reach the real Atlas cluster from the project's .env (an environment variable beats
# the .env file), and no real connection is needed. Tests also replace the client with a fake (see below).
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:1/trailwise-test")

# The OpenAI settings are pinned the same way: fake values, so a test never reads the real key or models from .env.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("OPENAI_MODEL", "test-model")
os.environ.setdefault("OPENAI_FALLBACK_MODEL", "")
os.environ.setdefault("OPENAI_TIMEOUT_SECONDS", "60")

import asyncio  # noqa: E402
import pytest  # noqa: E402 - must come after the environment is set up


@pytest.fixture(autouse=True)
def no_required_tools(monkeypatch):
    """Switch the explore required-tools check off for every test by default.

    The check forces an extra model round when the model skipped web_search, get_weather, geocode_places or
    get_place_images, which would break the scripted replies of the tests that are about something else (retries,
    fallback model, audit lines...). tests/test_explore_required.py puts the real setting back.
    """
    from app.agent import modes

    monkeypatch.setattr(modes, "REQUIRED_TOOLS", {})


class FakeAdmin:
    """Stands in for client.admin: its command() answers a ping, fails, or hangs, as the test decides."""

    def __init__(self, owner):
        self.owner = owner

    async def command(self, name):
        """Fake database command; only "ping" is used."""
        self.owner.commands.append(name)
        if self.owner.hang:
            await asyncio.sleep(60)  # longer than any ping timeout
        if not self.owner.ok:
            raise RuntimeError("fake MongoDB is down")
        return {"ok": 1}


class FakeMongoClient:
    """A fake AsyncMongoClient: no network. Set ok=False (ping fails) or hang=True (ping never answers)."""

    def __init__(self):
        self.ok = True
        self.hang = False
        self.closed = False
        self.commands = []
        self.admin = FakeAdmin(self)
        self.databases = {}

    def __getitem__(self, name):
        """client["name"] returns a (fake) database; the same one each time."""
        return self.databases.setdefault(name, {"fake_db": name})

    async def close(self):
        """Fake close: just remember it was called."""
        self.closed = True


@pytest.fixture(autouse=True)
def fake_mongo(monkeypatch):
    """Every test gets a fake MongoDB client instead of a real one (so tests never touch Atlas).

    The test can change its behaviour before starting the app, e.g. fake_mongo.ok = False.
    """
    fake = FakeMongoClient()
    monkeypatch.setattr("app.main.create_client", lambda settings: fake)
    return fake
