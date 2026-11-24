# test_preferences.py: tests for GET and PUT /me/preferences. A fake in-memory database stands in for MongoDB
# and a throwaway RSA key signs the test tokens, so no Atlas or Keycloak is needed.

import json
import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.config import get_settings
from app.core import auth
from app.main import app

KID = "test-key"
GUEST_ID = "A" * 43


class FakeCollection:
    """A tiny in-memory collection: find_one, update_one(upsert) and create_index, filtered on exact field values."""

    def __init__(self):
        self.docs = []
        self.indexes = []
        self.queries = []  # every filter used, so tests can check that each was scoped to a user

    async def create_index(self, key, unique=False):
        """Remember the requested index."""
        self.indexes.append((key, unique))

    async def find_one(self, flt, projection=None):
        """Return a copy of the first match, with the projection's excluded fields removed."""
        self.queries.append(flt)
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return {k: v for k, v in d.items() if not (projection and projection.get(k) == 0)}
        return None

    async def update_one(self, flt, update, upsert=False):
        """Update the first match, or insert a new document when upsert is set."""
        self.queries.append(flt)
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update["$set"])
                return
        if upsert:
            self.docs.append({**flt, **update["$set"]})


class FakeDb:
    """A fake database: db["Preferences"] gives the same FakeCollection each time."""

    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FakeClient:
    """A fake client for the lifespan: any database name gives the one FakeDb; ping works."""

    def __init__(self):
        self.db = FakeDb()
        self.admin = self

    def __getitem__(self, name):
        return self.db

    async def command(self, name):
        return {"ok": 1}

    async def close(self):
        pass


@pytest.fixture(scope="module")
def private_key():
    """A throwaway RSA key pair used to sign test tokens."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def fake(monkeypatch):
    """The fake client the app will use instead of the conftest one."""
    c = FakeClient()
    monkeypatch.setattr("app.main.create_client", lambda settings: c)
    return c


@pytest.fixture
def client(private_key, monkeypatch, fake):
    """A test client with the fake database and the throwaway Keycloak key."""
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})

    async def fake_get_jwks(force_refresh=False):
        return {"keys": [jwk]}

    monkeypatch.setattr(auth, "get_jwks", fake_get_jwks)
    with TestClient(app) as c:
        yield c


def as_user(private_key, sub):
    """Authorization header for a valid token whose sub is the given user."""
    token = jwt.encode(
        {"iss": auth.issuer(), "aud": get_settings().keycloak_audience, "sub": sub, "exp": int(time.time()) + 300},
        private_key, algorithm="RS256", headers={"kid": KID},
    )
    return {"Authorization": f"Bearer {token}"}


GOOD = {"travel_mode": ["train", "bus"], "budget": "comfort", "interests": ["beach", "food"], "reply_language": "hi"}
EMPTY = {"travel_mode": [], "budget": None, "interests": [], "reply_language": None}


def test_empty_defaults_when_nothing_saved(client, private_key):
    assert client.get("/me/preferences", headers=as_user(private_key, "u1")).json() == EMPTY


def test_save_then_read_back(client, private_key):
    h = as_user(private_key, "u1")
    r = client.put("/me/preferences", json=GOOD, headers=h)
    assert r.status_code == 200 and r.json() == GOOD
    assert client.get("/me/preferences", headers=h).json() == GOOD


def test_put_replaces_and_missing_fields_reset(client, private_key):
    h = as_user(private_key, "u1")
    client.put("/me/preferences", json=GOOD, headers=h)
    client.put("/me/preferences", json={"budget": "premium"}, headers=h)
    assert client.get("/me/preferences", headers=h).json() == {**EMPTY, "budget": "premium"}


def test_duplicates_are_removed(client, private_key):
    r = client.put("/me/preferences", json={"interests": ["food", "food", "beach"]}, headers=as_user(private_key, "u1"))
    assert r.json()["interests"] == ["food", "beach"]


@pytest.mark.parametrize(
    "body",
    [
        {"travel_mode": ["flight"]},
        {"travel_mode": "train"},
        {"budget": "cheap"},
        {"interests": ["shopping"]},
        {"reply_language": "fr"},
        {"surprise": 1},
        {"sub": "someone-else"},
        {"user_id": "someone-else"},
    ],
)
def test_invalid_bodies_are_rejected_and_nothing_is_saved(client, private_key, fake, body):
    r = client.put("/me/preferences", json=body, headers=as_user(private_key, "u1"))
    assert r.status_code == 422
    assert fake.db["Preferences"].docs == []


def test_every_allowed_value_is_accepted(client, private_key):
    body = {
        "travel_mode": ["train", "bus"],
        "budget": "premium",
        "interests": ["beach", "mountains", "heritage", "spiritual", "adventure", "nature", "food", "culture", "relaxing", "wildlife"],
        "reply_language": "kn",
    }
    assert client.put("/me/preferences", json=body, headers=as_user(private_key, "u1")).status_code == 200


def test_no_token_guest_and_bad_token_are_401(client):
    for headers in ({}, {"X-Session-Id": GUEST_ID}, {"Authorization": "Bearer not.a.jwt"}):
        assert client.get("/me/preferences", headers=headers).status_code == 401
        assert client.put("/me/preferences", json=GOOD, headers=headers).status_code == 401


def test_one_user_cannot_read_or_write_another(client, private_key, fake):
    a, b = as_user(private_key, "user-a"), as_user(private_key, "user-b")
    client.put("/me/preferences", json=GOOD, headers=a)
    assert client.get("/me/preferences", headers=b).json() == EMPTY  # B sees nothing of A's
    client.put("/me/preferences", json={"budget": "premium"}, headers=b)
    assert client.get("/me/preferences", headers=a).json() == GOOD  # B's save did not touch A's
    assert client.get("/me/preferences", headers=b).json()["budget"] == "premium"
    docs = {d["sub"]: d for d in fake.db["Preferences"].docs}
    assert set(docs) == {"user-a", "user-b"}
    # Every database query was filtered on exactly one sub: no unscoped reads or writes.
    assert all(set(q) == {"sub"} for q in fake.db["Preferences"].queries)


def test_user_comes_from_the_token_not_the_body(client, private_key, fake):
    client.put("/me/preferences", json={"sub": "victim"}, headers=as_user(private_key, "attacker"))
    assert fake.db["Preferences"].docs == []  # rejected
    client.put("/me/preferences", json=GOOD, headers=as_user(private_key, "attacker"))
    assert [d["sub"] for d in fake.db["Preferences"].docs] == ["attacker"]


def test_unique_index_on_sub_is_created_at_startup(client, fake):
    assert ("sub", True) in fake.db["Preferences"].indexes


def test_index_failure_is_only_logged(monkeypatch, fake, caplog):
    async def boom(key, unique=False):
        raise RuntimeError("mongodb+srv://u:SECRET@h/db is down")

    monkeypatch.setattr(fake.db["Preferences"], "create_index", boom)
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200  # the app still starts
    assert "Preferences index" in caplog.text
    assert "SECRET" not in caplog.text and "mongodb" not in caplog.text


def test_database_failure_is_a_generic_503(client, private_key, fake, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("mongodb+srv://u:SECRET@h/db")

    monkeypatch.setattr(fake.db["Preferences"], "find_one", boom)
    r = client.get("/me/preferences", headers=as_user(private_key, "u1"))
    assert r.status_code == 503 and "SECRET" not in r.text
