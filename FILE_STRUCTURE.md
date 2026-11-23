# Trailwise AI Traveler: File Structure

A simple map of the project as it is now, plus what is planned. Items marked **(to add)** do not exist yet.

---

## Project root structure

```
Trailwise_AI_Traveler/
├── .claude/
│   ├── skills/
│   │   └── add-agent-tool/
│   │       └── SKILL.md       # Playbook for adding a new agent tool
│   │   └── push-to-github/
│   │       └── SKILL.md       # exists: Safe stage, commit and push workflow
│   └── settings.json          # Claude Code permissions and hooks
├── backend/                   # FastAPI server (backend/Dockerfile exists: image with backend + mcp_server)
├── frontend/                  # React + Vite app
├── keycloak/                  # exists: Keycloak setup (login server): the realm file imported on first start, and the Trailwise login theme
│   ├── trailwise-realm.json   # exists: realm "trailwise" (self-registration, email verification, public PKCE client, user-profile field order, login theme). No secrets
│   └── themes/trailwise/login/ # exists: theme.properties (extends keycloak.v2) and resources/css/trailwise.css (sunset page, rounded card, orange buttons)
├── mcp_server/                # exists (weather, web_search, search_trains, search_buses, send_trip_email, geocode_places, get_route and get_place_images tools): Tools the AI agent calls
├── nginx/                     # (to add) Load balancer and HTTPS
├── evals/                     # (to add) Test prompts for AI answer quality
├── .env                       # Real keys (never commit)
├── .env.example               # exists. Fake placeholder keys, safe to commit
├── .gitignore                 # Keeps .env, .venv, node_modules out of Git
├── docker-compose.override.yml # exists (local only, git-ignored): mounts ./debug-logs at /app/logs
├── .dockerignore              # exists: Keeps secrets, .venv and logs out of the Docker build
├── ARCHITECTURE.md            # How the system is built
├── CLAUDE.md                  # Always-on rules for Claude Code
├── claudeprompts.md           # Saved prompts for Claude Code
├── docker-compose.yml         # Runs Redis, Mailpit, Keycloak, backend and frontend (MongoDB is Atlas, not a container)
├── FILE_STRUCTURE.md          # This file
├── plan.md                    # Project plan
├── docs/agent.md              # exists: how one request flows through the agent, every tool, enforced vs advised rules, example traces
├── docs/redis.md              # exists: every Redis key, what is never stored, behaviour when Redis is down, current status
├── docs/prompts.md            # exists: prompt versioning rules (heading, version bumps, tags, rebuild, rollback)
└── README.md                  # How to set up and run
```

| Item | What it is for |
|---|---|
| `.claude/settings.json` | Controls what Claude Code may run or read |
| `.claude/skills/add-agent-tool/` | Step-by-step playbook Claude follows when adding a tool |
| `CLAUDE.md` | Project rules Claude reads every session |
| `.env` | Real API keys and passwords. Must stay out of Git |
| `.env.example` | Template other people copy to make their own `.env` |
| `docker-compose.yml` | Starts the local services: Redis, Mailpit, Keycloak, the backend and the frontend (the database is MongoDB Atlas) |
| `keycloak/` | Keycloak realm file and the Trailwise login theme (mounted into the Keycloak container) |
| `ARCHITECTURE.md`, `plan.md` | Design and plan documents |

---

## BACKEND

```
backend/
├── .venv/                     # Python virtual environment (not committed)
├── .pytest_cache/             # Test cache (not committed)
├── app/                       # All application code
│   ├── main.py                # Starts FastAPI, /health endpoint
│   ├── config.py              # Reads settings and keys from .env
│   ├── api/                   # Routes (endpoints): me (exists, Keycloak user, DELETE /me/data), chat, trips (exists)
│   ├── agent/                 # AI agent, modes, prompts
│   ├── services/              # Weather, images, places, routes, transport, email
│   │   ├── trip_email.py      # exists: builds (server-side, no model) and sends the email of a saved trip
│   │   └── trips_store.py     # exists: MongoDB reads and writes for trips and explore history (owner_id in every query)
│   ├── db/                    # (planned) redis.py connection; the MongoDB client lives in core/mongo.py
│   ├── models/                # Pydantic schemas for requests and responses (chat.py, trips.py: exist)
│   └── core/                  # Security, rate limit, logging
│       ├── audit.py           # exists: audit log of agent activity (JSON lines, no secrets or message text)
│       ├── rate_limit.py      # exists: per-key counter in Redis (5 trip emails per user per hour)
│       ├── debug_log.py       # exists: optional plain-text debug log for local testing (DEBUG_LOG_FILE, LOG_MESSAGES)
│       └── mongo.py           # exists: the shared AsyncMongoClient (MongoDB Atlas), ping, get_db(request)
├── tests/                     # pytest tests (test_auth.py: exists, Keycloak token check tests; test_audit.py: exists, audit log tests; test_debug_log.py: exists, debug log tests; test_openai_retry.py: exists, OpenAI retry tests; test_model_fallback.py: exists, fallback model tests; test_openai_adapter.py: exists, adapter helper tests; test_explore_required.py: exists, explore required-tools tests; test_force_web_search.py: exists, forced first web_search tests; test_chat_memory.py: exists, per-session memory tests; test_train_prompts.py: exists, train prompt rules and old-name tests; test_chat_images.py: exists, photos with a reply tests; test_required_modes.py: exists, itinerary and route required tools, the guard and the overall time cap tests; test_mcp_timeouts.py: exists, MCP call limit and search timeout reply tests; test_openai_config.py: exists, OpenAI settings tests; test_mongo.py: exists, MongoDB client and /ready tests with a fake client; test_gateway_flow.py: exists, gateway flow (remote destinations) tests; test_trips.py: exists, trips, explore history and DELETE /me/data tests with a fake MongoDB; test_trip_email.py: exists, emailing a saved trip tests; conftest.py: fake MongoDB client and fake MONGODB_URI for every test)
├── pytest.ini                 # pytest settings
├── requirements.txt           # Python packages
└── Dockerfile                 # (to add) Container image for the backend
```

The subfolders inside `app/` follow the plan. If yours differ, update this section to match your real folders.

**Simple rule:** routes in `api/` stay thin. They validate input, call a function, and return. Real work lives in `agent/` and `services/`.

## Agent: backend/app/agent/

It goes inside the backend, in the app/ folder. Create these files:

backend/app/agent/
├── __init__.py       # exists
├── agent.py          # exists: sends the message to OpenAI (Responses API) and runs the MCP tool loop (capped at 6 rounds; the first call is forced to be web_search)
├── openai_adapter.py # exists: pure helpers for the OpenAI Responses API (tool definitions, allowed_tools choice, argument parsing, function_call_output, cached tokens, error mapping)
├── legs.py           # exists: gateway-flow helpers (geocoded-pair checks, destination-not-found signal, map legs)
├── modes.py          # exists: select_mode() (regex rules), FORCED_TOOLS / REQUIRED_TOOLS / required_tools() and build_system_prompt() (system.md + the mode file, 12-char hash)
├── mcp_client.py     # exists: McpClient, launches the MCP server over stdio, lists and calls tools
└── prompts/
    ├── system.md     # exists: main system prompt
    ├── explore.md    # exists: Explore mode prompt (not loaded by code yet)
    ├── route.md      # exists: Route mode prompt (not loaded by code yet)
    ├── itinerary.md  # exists: Itinerary mode prompt (not loaded by code yet)
    └── email.md      # exists: Email mode prompt (not loaded by code yet)
### MCP server (partly built)

```
mcp_server/
├── server.py                  # exists: registers all tools, serves over stdio
├── config.py                  # exists: settings from the root .env (OpenAI settings, MongoDB, Keycloak, audit log; weather and other tool keys are in mcp_server/config.py)
├── tools/                     # One file per tool
│   ├── weather.py             # exists: get_weather(location), validated
│   ├── routes_tool.py         # exists: get_route(points, mode), driving only, up to 10 points, map points only (no distances or times), Redis cache (6 hours)
│   ├── geocode_tool.py        # exists: geocode_places(names, context), up to 12 places, Redis cache (30 days), 1 request per second
│   ├── images_tool.py         # exists: get_place_images(place, region, count), three searches (landmark, temple, landscape), people and place filters, up to 3 Pexels photos with credit, Redis cache images:v2: (24 hours, found=true only)
│   └── web_search.py          # exists: web_search(query), OpenAI built-in web search, validated, capped, sources returned, cached 3 hours
├── tests/
│   ├── test_weather.py        # exists: weather tool and registration tests
│   ├── test_web_search.py     # exists: web search tool and registration tests (OpenAI and Redis faked)
│   ├── test_routes.py         # exists: route tool and registration tests (HTTP and Redis faked)
│   ├── test_geocode.py        # exists: geocode tool and registration tests (HTTP and Redis faked)
│   ├── test_images.py         # exists: image tool and registration tests (HTTP and Redis faked)
│   └── test_no_key_in_logs.py # exists: API key must never appear in logs
├── pytest.ini                 # exists
├── requirements.txt           # exists
└── Dockerfile                 # planned
```

---

## FRONTEND

```
frontend/
├── reference/                 # exists: README.md only (screenshots are git-ignored design references, never shipped)
├── src/
│   ├── components/            # exists: replyActions.js (which buttons appear under a reply), ReplyActions.jsx (Save trip, Email plan, Plan a trip to <place>), LoginPrompt.jsx (guest login popup), NavBar, HomeDashboard, PlacePanel (Explore side panel), ImageRow (photos under a chat reply), ChatSplit (two-column chat + map layout, owns the panel state), MapPanel (the map panel: a right-hand column on desktop, a full-screen overlay on phones), PlaceMap (the Leaflet map inside it), mapData.js (pure helpers for the map), ChatWindow, MessageList, MessageBubble, ChatInput, VoiceButton, Disclaimer. (to add: ItineraryView). tests/map-panel.test.mjs: exists, Node tests for the map panel
│   ├── pages/                 # exists: Home (public landing: sunset hero + destination cards), AppHome (app page at /app, for guests and logged-in users), Chat, Explore (the Explore section of /app: search box, place grid, side panel; UI only with a mock search) and SavedTrips (placeholder, no longer linked from the public site). Login.jsx is unused: AuthModal replaced it (Keycloak sign-in planned)
│   ├── api/                   # exists: client.js, chat.js (POST /chat), trips.js (save a trip, email a saved trip)
│   ├── data/                  # exists: places.js (hardcoded places, chips and a mock place search for the /app Home dashboard and Explore page), mockReturningUser.js (MOCK data, TODO until the backend has endpoints)
│   ├── auth/                  # exists: session.js (Keycloak sign-in, guest session id, auth headers)
│   ├── hooks/                 # exists: useAuth, useChat, chatMemory.js (pure memory rules), useVoice. (to add: streaming hook, needs SSE in the backend)
│   ├── App.jsx                # exists
│   ├── main.jsx               # exists
│   └── styles.css             # exists (not in the original plan)
├── public/                    # exists: favicon.svg, places/ (six placeholder illustrations for the /app Home cards)
├── index.html                 # exists: the Vite entry page
├── nginx.conf                 # exists (not in the original plan): serves index.html for unknown paths such as /app
├── Dockerfile                 # exists (not in the original plan): builds the app, serves it with non-root nginx
├── .dockerignore              # exists (not in the original plan): keeps node_modules, dist and .env out of the build
├── package.json               # exists (package-lock.json is committed too)
├── vite.config.js             # exists (not in the original plan): React plugin, dev port 5173
└── .env.example               # exists: VITE_API_URL, non-secret settings only
```

The React + Vite app exists and chats through `POST /chat`, with voice input (English, Hindi, Kannada). Run it with `npm install` and `npm run dev` in `frontend/`. The map, the itinerary view and streaming wait for backend pieces that are not built yet.

**Simple rule:** the frontend never holds API keys. It only talks to the backend.

---

## Where new things go

| I want to add... | Put it here |
|---|---|
| A new API endpoint | `backend/app/api/` |
| Request or response shape | `backend/app/models/` |
| A new outside API (weather, transport, etc.) | `backend/app/services/` |
| A new tool for the agent | `mcp_server/tools/`, then register it in `mcp_server/server.py` (use the `add-agent-tool` skill) |
| Change agent behavior or prompt | `backend/app/agent/` |
| A database connection or collection | `backend/app/db/` |
| A new cache rule (key and TTL) | The service that uses it, in `backend/app/services/` |
| Keycloak token check (PyJWT) and guest session logic | `backend/app/core/` (exists: `auth.py`). Passwords and login screens live in Keycloak, not our code |
| A new setting or API key | `backend/app/config.py` and `.env.example` |
| A backend test | `backend/tests/` |
| A new UI piece | `frontend/src/components/` |
| A new screen | `frontend/src/pages/` |
| A call to the backend from React | `frontend/src/api/` |
| Reusable React logic | `frontend/src/hooks/` |
| A test prompt for AI quality | `evals/` |
| A new Claude Code playbook | `.claude/skills/<name>/SKILL.md` |
| A new rule for Claude Code | `CLAUDE.md` |
| A new document | project root (or a `docs/` folder if you create one) |

**Quick check before you add a file:** ask "is this a route, a tool, a service, a model, or a UI piece?" The answer tells you the folder.

---

## Must stay out of Git

`.env`, `backend/.venv/`, `.pytest_cache/`, `__pycache__/`, `node_modules/`, `dist/`, `*.log`

Check with `git status`. If `.env` shows up, run `git rm --cached .env`, commit, and rotate every key in it.
