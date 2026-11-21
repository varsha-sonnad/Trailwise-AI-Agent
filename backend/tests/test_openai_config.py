# test_openai_config.py: tests for the OpenAI settings in app/config.py (the required key, defaults, values from the
# environment, the timeout limits) and that the OpenAI SDK is installed. Settings are built with _env_file=None, so the real .env
# is never read; only environment variables set inside each test count.

import pytest
from pydantic import ValidationError

from app.config import Settings

# The environment variables these tests control; each test starts from a clean slate for them.
OPENAI_VARS = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_FALLBACK_MODEL", "OPENAI_TIMEOUT_SECONDS")


@pytest.fixture(autouse=True)
def clean_openai_env(monkeypatch):
    """Remove the fake OpenAI values that conftest.py pins, so each test sets exactly what it needs."""
    for name in OPENAI_VARS:
        monkeypatch.delenv(name, raising=False)


def key_set(monkeypatch):
    """Give the required OpenAI key a fake value (tests that are not about the key need it to build Settings)."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")


def make_settings() -> Settings:
    """Build Settings from the environment only (never the real .env file)."""
    return Settings(_env_file=None)


def test_defaults_when_only_the_key_is_set(monkeypatch):
    """Both models default to empty (no default model names); the timeout defaults to 60 seconds."""
    key_set(monkeypatch)
    s = make_settings()
    assert s.openai_model == ""
    assert s.openai_fallback_model == ""
    assert s.openai_timeout_seconds == 60.0


def test_values_come_from_the_environment(monkeypatch):
    """All four settings are read from their environment variables."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_MODEL", "main-model")
    monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "backup-model")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "30.5")
    s = make_settings()
    assert s.openai_api_key == "fake-key"
    assert s.openai_model == "main-model"
    assert s.openai_fallback_model == "backup-model"
    assert s.openai_timeout_seconds == 30.5


def test_the_key_is_required(monkeypatch):
    """The app must not start without OPENAI_API_KEY: missing and empty values are both rejected."""
    with pytest.raises(ValidationError):
        make_settings()
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(ValidationError):
        make_settings()
    assert Settings.model_fields["openai_api_key"].is_required()


@pytest.mark.parametrize("bad", ["0", "-1", "601", "abc"])
def test_bad_timeouts_are_rejected(monkeypatch, bad):
    """The timeout must be a number above 0 and at most 600."""
    key_set(monkeypatch)
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", bad)
    with pytest.raises(ValidationError):
        make_settings()


@pytest.mark.parametrize("good", ["0.5", "60", "600"])
def test_good_timeouts_are_accepted(monkeypatch, good):
    """Values inside the allowed range (including the upper limit) are accepted."""
    key_set(monkeypatch)
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", good)
    assert make_settings().openai_timeout_seconds == float(good)


def test_conftest_pins_fake_values_without_touching_the_real_env():
    """conftest.py sets fake values with setdefault; they are what get_settings() sees in the other tests."""
    import os

    # The autouse fixture above removed them for this test, so check the source of truth: conftest's text.
    from pathlib import Path

    text = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    for name in OPENAI_VARS:
        assert f'"{name}"' in text
    assert "test-openai-key" in text
    assert os.environ.get("OPENAI_API_KEY") is None  # removed by the fixture, so nothing real can leak in here


def test_the_openai_sdk_is_installed_with_the_names_the_migration_needs():
    """The SDK imports and offers the async client and the Responses API the later steps will use."""
    import openai

    assert hasattr(openai, "AsyncOpenAI")
    client = openai.AsyncOpenAI(api_key="fake-key", max_retries=0)
    assert hasattr(client, "responses")
