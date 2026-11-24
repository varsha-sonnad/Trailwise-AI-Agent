# Redis in Trailwise

Written 2026-11-22 by reading the code (`backend/app`, `mcp_server`, `docker-compose.yml`, `.env.example`); nothing was run, and the real `.env` was not read. Anything I could not confirm from the code is marked **(unverified)**. No addresses, passwords or keys appear here.

## 1. Current status

**Redis is still in the project and still used.** Removing it was proposed (a list of the affected files and three open choices was written up in chat on 2026-11-22), but it has **not been approved and nothing has been changed**. There is **no MongoDB cache** in the code today. If Redis is later removed or replaced by a MongoDB cache, this file must be rewritten; the proposal and the pending decisions are summarised at the end (section 6).

What Redis is used for: a **cache** for the MCP tools (so repeated questions do not call an outside service again), the **shared one-request-per-second slot** for the geocoder, a **one-minute marker** after a Pexels 429, a **cache of Keycloak's public keys**, and a **per-user counter** for trip emails. Nothing else.

## 2. Where Redis is configured

| Item | Where | Notes |
|---|---|---|
| Service for local testing | `docker-compose.yml` service `redis` (`redis:7`, container `trailwise-redis`) | Port 6379 is published to the host; no password and **no volume** (data is lost when the container is removed; whether it survives a plain restart depends on the image's defaults **(unverified)**). Local testing only |
| Backend address | `backend/app/config.py` `Settings.redis_url`, default `redis://localhost:6379/0` (env var `REDIS_URL`) | The backend **always** has an address (the default), so it always tries Redis |
| MCP server address | `mcp_server/config.py` `Settings.redis_url`, default `""` (env var `REDIS_URL`) | Empty or unset means **no Redis at all, silently**. `.env.example` sets it, so copying that file turns it on |
| Packages | `backend/requirements.txt`, `mcp_server/requirements.txt` (`redis>=5,<6`) | Async client `redis.asyncio` |
| Inside Docker | the `.env.example` comment says to use the service name (`redis://redis:6379/0`) | `docker-compose.yml` itself does not set `REDIS_URL` |

## 3. Every Redis key

All keys are written and read by the code named in the table. Values are JSON text unless stated. "Success only" means failures, partial results and `found: false` are **not** written.

### 3.1 MCP tools (`mcp_server/tools/`) — each tool has its own small `_Store` class

| Key | Writer / reader | Content | TTL |
|---|---|---|---|
| `geocode:v1:<sha256 of context\|name>` | `geocode_tool.py` `_Store.set` / `.get` | `{"lat", "lng"}` of one matched place. Success only | 30 days (`CACHE_TTL_SECONDS`) |
| `geocode:throttle` | `geocode_tool.py` `_Store.acquire_slot` (`SET NX PX`) | the text `1`; whoever creates it may call the geocoder, so at most **1 request/second across all processes** (Nominatim's usage rule) | 1 second (`MIN_INTERVAL_SECONDS`) |
| `route:v1:<sha256 of mode\|rounded points>` | `routes_tool.py` `_Store.set` / `.get` | `{"route": [[lat, lng], ...]}` (up to 300 points). Success only | 6 hours |
| `images:v2:<sha256 of place\|region>` | `images_tool.py` `_Store.set` / `.get` | `{"images": [...]}` — up to 3 Pexels photos with credits. Only complete results with `found: true` | 24 hours |
| `images:v2:rate_limited` | `images_tool.py` (set after a Pexels 429; read before each call) | the text `1`; while it exists calls fail fast | 1 minute |
| `web_search:v1:<sha256 of model\|lower-cased query>` | `web_search.py` `_Store.set` / `.get` | `{"ok", "answer", "sources", "source"}` — the answer text and source titles/URLs. Success only | 3 hours |
| `buses:v1:<sha256 of source\|destination\|date>` | `bus.py` `_Store.set` / `.get` | the bus list for that search (operator, bus type, times, fares, seats, …). Real successes only; a cached value that fails the shape check (`_valid_cached`) is ignored | 30 minutes |
| `trains:v1:station:<sha256 of normalised city>` | `train_tool.py` `_find_station` | a list of candidate stations `{code, name, popularity}`. Real answers only | 30 days |
| `trains:v1:search:<from code>:<to code>:<date or "any">` | `train_tool.py` `_between` (key built by `_search_key`) | the list of trains for that pair/date. **Empty results are not cached** | 6 hours |

Reading is always done on the way in ("cache first", `CLAUDE.md` rule 3): a hit makes **no** outside call. A cache miss, an unreachable Redis and an invalid stored value all mean "call the outside service".

### 3.2 Backend (`backend/app/`)

| Key | Writer / reader | Content | TTL |
|---|---|---|---|
| `keycloak:jwks` | `core/auth.py` `_cache_set` / `_cache_get` (called by `get_jwks`) | the **raw JSON of Keycloak's public signing keys** (JWKS) | 10 minutes (`JWKS_CACHE_SECONDS`) |
| `ratelimit:trip-email:<Keycloak user id>` | `core/rate_limit.py` `hit` (called by `api/trips.py` `email_trip`) | an integer counter (`INCR`): how many trip-email attempts this user made in the current window | 1 hour, set on the first attempt (`EMAIL_WINDOW_SECONDS`); limit 5 (`EMAIL_LIMIT`) |

The second key contains the user's Keycloak `sub` (an opaque id) in plain text; the first key and every MCP key contain no user identifier (their keys are hashes of the *question or route*, and cannot be reversed to text by anyone who only sees the keys).

## 4. What is never stored in Redis

Verified by reading every `redis` use above:

- **Chat messages, history, `last_mode`, `last_place`**: they live only in the browser and travel with each request (`docs/agent.md`, 2.10). The agent stores nothing between requests.
- **Guest chats.** The target design in `CLAUDE.md` mentions "guest chats go in Redis with a TTL", but **no such key exists** (not built).
- **Trips and explore history**: MongoDB (`backend/app/services/trips_store.py`).
- **Access tokens, passwords, API keys, SMTP password**: never written. The only Keycloak data is the *public* key set.
- **Email addresses, subjects and bodies** of trip emails.
- **Failed results**: errors, `found: false`, partial image sets, empty train lists and no-bus results are not cached.
- **The question text itself.** Cache keys are SHA-256 hashes. The cached *values* are answers derived from questions (a web-search answer, a place's coordinates) but do not contain the question.
- **Third-party API keys** and request URLs that carry them: tools send keys only in headers and never cache or log them.

## 5. What happens when Redis is down

| Component | Behaviour | Verified by |
|---|---|---|
| **MCP tools, `REDIS_URL` empty/unset** | Redis is not used at all, no warning (`_Store.__init__` sets `warned` and returns). All tools work uncached | code |
| **MCP tools, URL set but Redis unreachable** | One short warning per tool call (for example "geocode: Redis unavailable, continuing without the cache") with **no address or password**; reads count as a miss, writes are ignored, the tool carries on (connect and reply timeouts 2 s, `REDIS_TIMEOUT_SECONDS`) | code |
| **Geocoder throttle** | With no shared slot the tool falls back to an **in-process** timer (`_Limiter.wait`, `_now`). The `_Limiter` is created per `geocode_places` call (`_geocode`), so the fallback only spaces the requests **inside one call**; two calls at nearly the same time are no longer spaced apart (across calls or processes). Whether that can break Nominatim's rule in practice is **(unverified)** | code (`_Limiter.wait`) |
| **Pexels 429 marker** | Not readable/writable → "not rate-limited": every call may hit Pexels, and a 429 is handled per call | code (`images_tool.py`) |
| **Backend Keycloak key cache** | `_cache_get` / `_cache_set` swallow the error, log "Redis unavailable: fetching Keycloak keys without the cache" (or "…keys not cached"), and the keys are fetched from Keycloak with async `httpx` (5 s timeout). Logins keep working. `auth.py` passes no explicit Redis timeout, so how long a dead Redis blocks that call is **(unverified)** | code |
| **Trip-email limit** | **Fails closed**: `rate_limit.hit` raises `RateLimitUnavailable` and `POST /trips/{id}/email` answers 503 `email_unavailable`; **nothing is sent** | code, `tests/test_trip_email.py` |
| **Tests** | Tests never use a real Redis: they fake it (`FakeRedis` in `backend/tests/test_trip_email.py`; fake stores in `mcp_server/tests/`) | code |

Consequence of losing the cache: every call goes to the outside service. The quotas that matter are written in the tool files and `claudeprompts.md`: RailRadar's free plan is 100 requests a month (station lookups were cached 30 days to protect it; at most 3 requests per search), Parse.bot about 100 calls a day for buses, a model call for every web search, and the public Nominatim/Pexels limits. Whether those limits would be reached in practice depends on traffic **(unverified)**.

## 6. If Redis is removed or replaced (not done; decisions pending)

The proposal written up in chat (2026-11-22), still awaiting approval:

- **Files**: delete `backend/app/core/rate_limit.py`; edit backend `config.py`, `core/auth.py`, `api/trips.py`, `requirements.txt` and `tests/test_trip_email.py`; edit the six MCP tool files, `mcp_server/config.py`, `requirements.txt` and seven MCP test files; edit `docker-compose.yml` and `.env.example`; update the docs (`CLAUDE.md` rules 1 and 3, `README.md`, `ARCHITECTURE.md`, `FILE_STRUCTURE.md`, `plan.md`, `claudeprompts.md`, `.claude/skills/add-agent-tool/SKILL.md`, and this file).
- **Open choices**: (A) remove the cache entirely, or (B) replace it with a MongoDB cache with TTL indexes (recommended in the proposal); keep the trip-email limit in MongoDB or drop it; cache Keycloak's public keys in process memory or fetch every time; allow rewriting `CLAUDE.md` rules 1 and 3.
- **If a MongoDB cache is built**, this file should then describe: the collection name(s), the document shape per tool (same keys as section 3, stored as `_id`/`key`), the TTL index (`expireAfterSeconds`) per kind of entry, and that Mongo's TTL cleanup runs about once a minute (so an entry can outlive its TTL by up to a minute).

Until then, treat sections 1 to 5 as the truth.
