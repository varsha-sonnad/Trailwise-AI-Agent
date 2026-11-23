# rate_limit.py: a small per-key counter in Redis (a fixed window), used to limit how often a signed-in user may do something
# (for example email a trip: 5 per hour). The counter lives in Redis, never in process memory (stateless servers, rule 1).
# If Redis cannot be reached the caller is told (RateLimitUnavailable) and should refuse the action: a limit that silently
# stops working would let the action be repeated without bound. The Redis address is never logged.

import logging

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)

# Connect and reply timeouts for Redis, in seconds, so a dead Redis cannot hang a request.
REDIS_TIMEOUT_SECONDS = 2.0


class RateLimitUnavailable(Exception):
    """Redis could not be used, so the limit cannot be checked."""


async def hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Count one attempt under this key. Returns (allowed, seconds until the window resets).

    The first attempt starts a window of window_seconds; attempts beyond `limit` inside it are not allowed.
    """
    try:
        client = redis.from_url(
            get_settings().redis_url, socket_timeout=REDIS_TIMEOUT_SECONDS, socket_connect_timeout=REDIS_TIMEOUT_SECONDS
        )
        try:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, window_seconds)  # the window starts with the first attempt
            ttl = await client.ttl(key)
            if ttl is None or ttl < 0:
                # A counter without an expiry would block the user forever: give it one now.
                await client.expire(key, window_seconds)
                ttl = window_seconds
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 - any Redis problem means "cannot check"
        logger.warning("rate limit check failed: %s", type(exc).__name__)
        raise RateLimitUnavailable from exc
    return count <= limit, max(int(ttl), 1)
