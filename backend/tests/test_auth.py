# test_auth.py: tests for the Keycloak token check (GET /me and the guest/user handling on POST /chat).
# No Keycloak is needed: the tests sign tokens with a throwaway RSA key and replace the key download.

import json
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
GUEST_ID = "A" * 43  # looks like a 32-byte base64url session id


@pytest.fixture(scope="module")
def private_key():
    """A throwaway RSA key pair used to sign test tokens."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def client(private_key, monkeypatch):
    """A test client whose Keycloak key download returns the throwaway public key."""
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})

    async def fake_get_jwks(force_refresh=False):
        return {"keys": [jwk]}

    monkeypatch.setattr(auth, "get_jwks", fake_get_jwks)
    with TestClient(app) as c:
        yield c


def make_token(private_key, **overrides):
    """Sign a token that is valid unless a claim is overridden (a value of None removes the claim)."""
    claims = {
        "iss": auth.issuer(),
        "aud": get_settings().keycloak_audience,
        "sub": "user-123",
        "email": "a@example.com",
        "name": "Asha",
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})


def bearer(token):
    """The Authorization header for a token."""
    return {"Authorization": f"Bearer {token}"}


def test_me_with_valid_token(client, private_key):
    """A valid token returns the user's id, email and name."""
    r = client.get("/me", headers=bearer(make_token(private_key)))
    assert r.status_code == 200
    assert r.json() == {"id": "user-123", "email": "a@example.com", "name": "Asha"}


def test_me_without_credentials_is_401(client):
    """No token: 401."""
    assert client.get("/me").status_code == 401


def test_me_for_a_guest_is_401(client):
    """A guest session id is not enough for /me."""
    assert client.get("/me", headers={"X-Session-Id": GUEST_ID}).status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 10},  # expired
        {"iss": "http://evil.example/realms/trailwise"},  # wrong issuer
        {"aud": "someone-else"},  # not meant for this API
        {"sub": None},  # no subject
        {"exp": None},  # never expires
    ],
)
def test_bad_claims_are_401(client, private_key, overrides):
    """Expired, wrong issuer, wrong audience and missing claims are all rejected."""
    r = client.get("/me", headers=bearer(make_token(private_key, **overrides)))
    assert r.status_code == 401


def test_token_signed_by_another_key_is_401(client):
    """A token signed with a key Keycloak does not know is rejected."""
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert client.get("/me", headers=bearer(make_token(other))).status_code == 401


def test_unknown_key_id_is_401(client, private_key):
    """A token naming a key id that Keycloak does not publish is rejected."""
    token = jwt.encode({"iss": auth.issuer(), "sub": "x", "aud": "trailwise-api", "exp": int(time.time()) + 60},
                       private_key, algorithm="RS256", headers={"kid": "nope"})
    assert client.get("/me", headers=bearer(token)).status_code == 401


def test_hmac_and_none_algorithms_are_401(client):
    """Tokens using HS256 or no signature at all are never accepted."""
    claims = {"iss": auth.issuer(), "sub": "x", "aud": "trailwise-api", "exp": int(time.time()) + 60}
    hs = jwt.encode(claims, "secret" * 8, algorithm="HS256", headers={"kid": KID})
    none = jwt.encode(claims, None, algorithm="none", headers={"kid": KID})
    assert client.get("/me", headers=bearer(hs)).status_code == 401
    assert client.get("/me", headers=bearer(none)).status_code == 401


def test_malformed_authorization_header_is_401(client):
    """Garbage in the Authorization header is a 401, not a crash."""
    assert client.get("/me", headers={"Authorization": "Bearer"}).status_code == 401
    assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_keycloak_down_is_401(client, monkeypatch, private_key):
    """If Keycloak's keys cannot be fetched, nobody is verified (401), and nothing crashes."""

    async def broken(force_refresh=False):
        raise auth.AuthError("cannot fetch Keycloak keys")

    monkeypatch.setattr(auth, "get_jwks", broken)
    assert client.get("/me", headers=bearer(make_token(private_key))).status_code == 401


def test_chat_rejects_bad_token_but_allows_guest_and_anonymous(client, private_key, monkeypatch):
    """/chat: a bad token is 401, a guest id or no credentials still work, a bad session id is 400."""

    async def fake_run_agent(message, **memory):
        return "ok"

    monkeypatch.setattr("app.api.chat.run_agent", fake_run_agent)
    body = {"message": "hi"}
    assert client.post("/chat", json=body, headers=bearer("not.a.jwt")).status_code == 401
    assert client.post("/chat", json=body, headers=bearer(make_token(private_key))).status_code == 200
    assert client.post("/chat", json=body, headers={"X-Session-Id": GUEST_ID}).status_code == 200
    assert client.post("/chat", json=body).status_code == 200
    assert client.post("/chat", json=body, headers={"X-Session-Id": "short"}).status_code == 400


def test_get_jwks_uses_internal_url(monkeypatch):
    """The key download uses the internal Keycloak address when one is set."""
    monkeypatch.setenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080")
    get_settings.cache_clear()
    try:
        assert auth._jwks_url() == "http://keycloak:8080/realms/trailwise/protocol/openid-connect/certs"
    finally:
        monkeypatch.delenv("KEYCLOAK_INTERNAL_URL")
        get_settings.cache_clear()
