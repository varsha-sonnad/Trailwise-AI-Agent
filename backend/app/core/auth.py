# auth.py: checks who is calling. Registered users sign in with Keycloak and send the access token as
# "Authorization: Bearer <token>"; this module only VERIFIES that token (PyJWT). It never sees a password and
# never issues tokens. Guests send a random "X-Session-Id" instead.
# Keycloak's public keys are fetched with async httpx and cached in Redis (PyJWT's own key fetcher is blocking).

import logging
import re
from dataclasses import dataclass

import httpx
import jwt
import redis.asyncio as redis
from fastapi import Header, HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)

# How long Keycloak's public keys stay cached in Redis, in seconds.
JWKS_CACHE_SECONDS = 600
JWKS_CACHE_KEY = "keycloak:jwks"
# Guest session ids: URL-safe characters only, 22 to 64 long (a 16 to 48 byte random value, base64url).
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,64}$")


class AuthError(Exception):
    """The token is missing, malformed, expired or not meant for us. The message is for logs, never for users."""


@dataclass(frozen=True)
class Principal:
    """Who is calling: a signed-in user (id = Keycloak "sub") or a guest (id = session id)."""

    kind: str  # "user" or "guest"
    id: str
    email: str | None = None
    name: str | None = None
    # True only when the token says email_verified is exactly true (never inferred): emailing a trip needs it.
    email_verified: bool = False


def issuer() -> str:
    """The issuer every valid token must carry: the public Keycloak address plus the realm."""
    s = get_settings()
    return f"{s.keycloak_url.rstrip('/')}/realms/{s.keycloak_realm}"


def _jwks_url() -> str:
    """Where this server downloads Keycloak's public keys (the internal address when one is set)."""
    s = get_settings()
    base = (s.keycloak_internal_url or s.keycloak_url).rstrip("/")
    return f"{base}/realms/{s.keycloak_realm}/protocol/openid-connect/certs"


async def _cache_get() -> str | None:
    """Read the cached keys from Redis. A Redis problem is never fatal: return None and fetch instead."""
    try:
        client = redis.from_url(get_settings().redis_url)
        try:
            raw = await client.get(JWKS_CACHE_KEY)
        finally:
            await client.aclose()
        return raw.decode() if raw else None
    except Exception:  # noqa: BLE001 - the cache is optional
        logger.warning("Redis unavailable: fetching Keycloak keys without the cache")
        return None


async def _cache_set(raw: str) -> None:
    """Store the keys in Redis for a while; ignore any Redis problem."""
    try:
        client = redis.from_url(get_settings().redis_url)
        try:
            await client.set(JWKS_CACHE_KEY, raw, ex=JWKS_CACHE_SECONDS)
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001 - the cache is optional
        logger.warning("Redis unavailable: Keycloak keys not cached")


async def get_jwks(force_refresh: bool = False) -> dict:
    """Return Keycloak's public keys (JWKS), from the Redis cache when possible."""
    if not force_refresh:
        cached = await _cache_get()
        if cached:
            return httpx.Response(200, content=cached).json()
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            response = await http.get(_jwks_url())
            response.raise_for_status()
    except httpx.HTTPError as exc:
        # Keycloak is down or unreachable: nobody can be verified right now.
        raise AuthError(f"cannot fetch Keycloak keys: {type(exc).__name__}") from exc
    await _cache_set(response.text)
    return response.json()


async def _find_key(kid: str) -> dict:
    """Find the public key with this id; if it is not cached, refresh once (Keycloak may have rotated keys)."""
    for refresh in (False, True):
        for key in (await get_jwks(force_refresh=refresh)).get("keys", []):
            if key.get("kid") == kid:
                return key
    raise AuthError("unknown signing key")


async def verify_token(token: str) -> dict:
    """Verify a Keycloak access token and return its claims, or raise AuthError."""
    try:
        header = jwt.get_unverified_header(token)
        # Only RS256 is accepted: this blocks "alg: none" and HMAC-with-public-key tricks.
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise AuthError("unexpected token header")
        public_key = jwt.PyJWK.from_dict(await _find_key(header["kid"])).key
        return jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=get_settings().keycloak_audience,
            issuer=issuer(),
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {type(exc).__name__}") from exc


def _unauthorized() -> HTTPException:
    """The one generic 401 returned for any bad credential (no detail about why)."""
    return HTTPException(
        status_code=401,
        detail="Please sign in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_principal(
    authorization: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
) -> Principal | None:
    """FastAPI dependency: a user (valid token), a guest (valid session id) or None (nothing sent).

    A token that is present but bad is a 401: it is never quietly downgraded to a guest.
    """
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized()
        try:
            claims = await verify_token(token.strip())
        except AuthError as exc:
            logger.info("rejected token: %s", exc)  # reason goes to the log only
            raise _unauthorized() from exc
        return Principal(
            kind="user", id=claims["sub"], email=claims.get("email"), name=claims.get("name"),
            email_verified=claims.get("email_verified") is True,
        )
    if x_session_id is not None:
        if not SESSION_ID_PATTERN.match(x_session_id):
            raise HTTPException(status_code=400, detail="Invalid session id.")
        return Principal(kind="guest", id=x_session_id)
    return None


async def require_user(
    authorization: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
) -> Principal:
    """FastAPI dependency for routes that need a signed-in user: guests and anonymous callers get a 401."""
    principal = await get_optional_principal(authorization, x_session_id)
    if principal is None or principal.kind != "user":
        raise _unauthorized()
    return principal
