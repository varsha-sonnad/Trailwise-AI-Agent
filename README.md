# Trailwise

Trailwise is a chat-based AI travel planner. A user types a request in plain language, such as "Plan a 3-day trip to Goa from Bangalore", and an AI agent finds the information and builds the plan.

## Overview

Planning a trip normally means jumping between many sites for weather, transport, places and maps. Trailwise puts it all in one chat.

**Features**
- Explore destinations and nearby attractions
- Day-by-day itineraries
- Weather, images and maps
- Voice input (English, Hindi, Kannada) and email of trip plans
- Saved trips and chat history

**Not included:** booking, payments or tickets. Trailwise is for information and planning only.

**Transport data is indicative only.** There is no transport data source, so the agent never states a timing or price unless a tool returned it. The app shows a "verify before travel" disclaimer.

For the full design and build phases, see [`plan.md`](plan.md).

## Technology Stack

| Layer | Technology | Status |
|---|---|---|
| Backend | Python + FastAPI (async) | Skeleton created, smoke test passes |
| AI agent | Gemini via the Google Gemini API (function calling) | Planned |
| Database | MongoDB | In `docker-compose.yml` |
| Cache and sessions | Redis | In `docker-compose.yml` |
| Local email testing | Mailpit | In `docker-compose.yml` |
| Frontend | React + Vite | Chat UI with voice input exists; map, itinerary view and login are planned |
| Maps, weather, images | Leaflet, OpenWeatherMap, Pexels | Planned |
| Local infrastructure | Docker Compose | Done |

## Project Structure

```
Trailwise_AI_Traveler/
├── README.md             Project overview and structure (this file)
├── plan.md               Design, architecture and build phases
├── CLAUDE.md             Rules Claude Code follows in this project
├── claudeprompts.md      Log of prompts given to Claude Code and their outcomes
├── docker-compose.yml    Local services: MongoDB, Redis, Mailpit
├── .env.example          Template of settings (copy to .env in the project root)
├── .gitignore            Keeps secrets and build files out of git
├── .dockerignore         Keeps secrets, virtual environments and logs out of the Docker build
├── .claude/              Claude Code project setup
│   ├── settings.json     Permissions, blocked commands and the .env write-protection hook
│   └── skills/
│       ├── add-agent-tool/
│       │   └── SKILL.md  Step-by-step workflow for adding an agent tool
│       └── push-to-github/
│           └── SKILL.md  Safe stage, commit and push workflow (approval at each step)
├── frontend/             React + Vite web app (holds no API keys: it only talks to the backend)
│   ├── index.html        Vite entry page: an empty #root that src/main.jsx fills
│   ├── package.json      npm packages and scripts (dev, build, preview)
│   ├── package-lock.json Exact package versions (committed)
│   ├── vite.config.js    Vite settings: React plugin, dev server on port 5173 (the port the backend allows)
│   ├── .env.example      Non-secret settings (VITE_API_URL, the backend address)
│   ├── public/
│   │   └── favicon.svg   Tab icon
│   └── src/
│       ├── main.jsx      Starting point: mounts the app into #root
│       ├── App.jsx       App shell: header, page switcher, current page, disclaimer
│       ├── styles.css    All styles (light and dark mode)
│       ├── api/
│       │   ├── client.js     The only code that calls the backend (POST helper, ApiError, friendly network errors)
│       │   └── chat.js       sendMessage(): POST /chat and the 422, 502 and 503 messages
│       ├── hooks/
│       │   ├── useChat.js    Conversation state: messages, pending reply, send
│       │   └── useVoice.js   Voice input with the browser's speech recognition (English, Hindi, Kannada)
│       ├── components/
│       │   ├── ChatWindow.jsx    The chat area: message list plus input
│       │   ├── MessageList.jsx   Scrolling conversation with a "Thinking..." bubble
│       │   ├── MessageBubble.jsx One message; bot replies rendered as safe Markdown
│       │   ├── ChatInput.jsx     Message box and Send button (Enter sends, Shift+Enter new line)
│       │   ├── VoiceButton.jsx   Language picker and microphone button, with a typing fallback
│       │   └── Disclaimer.jsx    "Verify before travel" notice, always visible
│       └── pages/
│           ├── Home.jsx      The chat screen
│           ├── SavedTrips.jsx  Placeholder: saving trips needs accounts (not built yet)
│           └── Login.jsx     Placeholder: login is a later step (not built yet)
├── mcp_server/           MCP tool server (separate service, own .venv)
│   ├── server.py         Registers the tools and serves them over stdio
│   ├── config.py         Loads OPENWEATHER_API_KEY, GEMINI_API_KEY, GEMINI_SEARCH_MODEL and REDIS_URL from the root .env
│   ├── requirements.txt  Python packages for the MCP server
│   ├── pytest.ini        Test settings
│   ├── tools/
│   │   ├── weather.py    get_weather(location): validates, checks Redis, calls OpenWeatherMap
│   │   ├── web_search.py web_search(query): validates, checks Redis, asks Gemini with Google Search grounding
│   │   ├── train_tool.py get_train_details(train_number): validates, calls RailRadar, returns scheduled train details
│   │   ├── bus.py        search_buses(source_city, destination_city, journey_date): validates, looks up city IDs, searches redBus via Parse.bot
│   │   └── email_tool.py send_trip_email(subject, body, confirmed): validates, then sends a plain-text trip email by SMTP to one fixed address
│   └── tests/
│       ├── test_weather.py  Weather tool tests (API and Redis faked) and a registration check
│       ├── test_web_search.py  Web search tool tests (Gemini and Redis faked) and a registration check
│       ├── test_train_tool.py  Train tool tests (RailRadar faked) and a registration check
│       ├── test_bus.py  Bus tool tests (Parse.bot faked) and a registration check
│       ├── test_email_tool.py  Email tool tests (SMTP faked) and a registration check
│       └── test_no_key_in_logs.py  Regression test: the API key never appears in logs
└── backend/              FastAPI server
    ├── .venv/            Python virtual environment with installed packages (not committed)
    ├── Dockerfile        Image that runs the backend and its stdio MCP server (build context: project root)
    ├── requirements.txt  Python packages
    ├── pytest.ini        Test settings
    ├── app/
    │   ├── __init__.py   Marks app as a Python package
    │   ├── api/
    │   │   └── chat.py      The POST /chat route (thin: validate, call the agent, return)
    │   ├── agent/
    │   │   ├── __init__.py  Marks agent as a Python package
    │   │   ├── mcp_client.py  McpClient: starts the MCP server over stdio, lists its tools, calls them
    │   │   ├── agent.py     Discovers MCP tools, sends the user's message to Gemini with the system prompt and runs the capped tool loop
    │   │   └── prompts/
    │   │       ├── system.md  Basic Trailwise instructions: role, scope, response guidelines
    │   │       ├── explore.md  Explore mode prompt (discover places)
    │   │       ├── route.md  Route mode prompt (compare ways to travel)
    │   │       ├── itinerary.md  Itinerary mode prompt (day-by-day plans)
    │   │       └── email.md  Email mode prompt (draft and, only when asked, send a trip email)
    │   ├── config.py     Loads settings from the environment
    │   ├── core/
    │   │   └── audit.py     Audit log: JSON lines of agent activity, secrets and message text never written
    │   ├── models/
    │   │   └── chat.py      Request and response models for /chat
    │   └── main.py       Creates the app, defines /health and /ready
    └── tests/
        ├── conftest.py   Shared test setup (fake API key)
        ├── test_health.py  Smoke test for /health
        ├── test_agent_tools.py  Tests for the agent's tool loop (Gemini and MCP faked)
        ├── test_audit.py  Tests for the audit log: events recorded, no secrets or message text, never breaks chat
        ├── test_mcp_client.py  Integration tests: client connects to the real MCP server, discovers get_weather, calls it
        ├── test_gemini_retry.py  Tests for retrying temporary Gemini errors (Gemini faked)
        ├── test_model_fallback.py  Tests for switching to the fallback model (Gemini faked)
        └── test_chat.py    Tests for POST /chat (agent faked)
```

**Purpose of each item**

| Item | Purpose |
|---|---|
| `README.md` | The front page of the project. Its structure section is updated whenever a file or folder is added. |
| `plan.md` | The project plan: problem statement, tech stack, architecture, flow diagrams, build phases, security, testing and a launch checklist. |
| `CLAUDE.md` | Instructions Claude Code reads every session: stack, coding rules, commands and working rules. |
| `claudeprompts.md` | A record of each prompt given to Claude Code, its outcome, and the decisions made so far. |
| `docker-compose.yml` | Starts the local services: MongoDB (port 27017), Redis (6379) and Mailpit (SMTP 1025, web UI 8025). |
| `.env.example` | Template of the settings the backend needs, with fake values. Copy it to `.env` in the project root and add your Gemini API key. |
| `.claude/settings.json` | Project-level Claude Code settings: allowed commands (pytest, uvicorn, docker compose, read-only git), denied ones (rm -rf, force push, reading or editing `.env`) and a hook that blocks writes to `.env` files. |
| `.claude/skills/add-agent-tool/SKILL.md` | Workflow Claude Code follows when adding an agent tool: async, cache first, validated arguments, transport disclaimer, tests, comments and doc updates. |
| `.claude/skills/push-to-github/SKILL.md` | Workflow Claude Code follows to push to GitHub: verify the `origin` URL and branch, scan for secrets, run tests, then stage, commit and push only after your explicit approval. Pull requests only when asked. Works with the Git Safety Rules in `CLAUDE.md`. |
| `backend/Dockerfile` | Builds one image (`python:3.12-slim`) holding the backend and `mcp_server/`, laid out as `/app/backend` and `/app/mcp_server` so the backend can start the MCP server as a stdio subprocess. No secrets inside: `.env` is mounted at run time. Runs as a non-root user and serves on port 8000. For local Docker testing, not production. |
| `.dockerignore` | Project-root ignore list for the Docker build: `.env`, `.git`, `.claude`, both `.venv/` folders, caches, logs, zips, root-level `.md` docs and `frontend/`. It does not ignore `backend/app/agent/prompts/system.md`, which the agent needs. |
| `.gitignore` | Stops `.env`, virtual environments, caches, build output and `logs/` (audit log) from being committed. |
| `frontend/` | The React + Vite chat app. It replaces the first plain HTML page (its styles and logic were moved into `src/`). Run it with `npm install` then `npm run dev` from `frontend/` and open http://localhost:5173 (needs the backend on http://localhost:8000; the dev server uses port 5173 strictly because that is the only origin the backend's CORS allows). It holds no API keys. |
| `frontend/index.html`, `package.json`, `vite.config.js`, `.env.example`, `public/favicon.svg` | The Vite entry page, the npm setup, the dev server settings (port 5173, React plugin), the non-secret setting `VITE_API_URL` (default `http://localhost:8000`) and the tab icon. `package-lock.json` pins exact versions; `node_modules/` and `dist/` are ignored by git. |
| `frontend/src/main.jsx`, `App.jsx`, `styles.css` | Mounts the app, then the shell: header, a small page switcher (no router library yet), the current page and the always-visible "verify before travel" disclaimer. The chat stays mounted when you switch pages, so the conversation is kept. |
| `frontend/src/api/client.js`, `chat.js` | All backend calls: `postJson` adds the base URL and turns network failures into a friendly "Cannot reach the server" error; `sendMessage` calls `POST /chat` and maps 422, 502 and 503 to clear messages. A 503 means Gemini is busy. |
| `frontend/src/hooks/useChat.js`, `useVoice.js` | `useChat` holds the messages and pending state (a conversation lives only in the browser tab: the backend has no history yet). `useVoice` wraps the browser's speech recognition for English (en-IN), Hindi (hi-IN) and Kannada (kn-IN); support is uneven, best in Chrome, so typing is always available. |
| `frontend/src/components/` | `ChatWindow`, `MessageList`, `MessageBubble`, `ChatInput`, `VoiceButton` and `Disclaimer`. Bot replies are rendered with `react-markdown` with raw HTML dropped (never `dangerouslySetInnerHTML`), and links open in a new tab with `rel="noopener noreferrer"`. Spoken text is added to the message box for the user to check before pressing Send. |
| `frontend/src/pages/Home.jsx`, `SavedTrips.jsx`, `Login.jsx` | The chat screen, and two placeholders ("coming soon") for saved trips and login. No authentication or credentials form exists. |
| `backend/` | The FastAPI server. The agent and its tools will be added here. |
| `backend/.venv/` | The Python virtual environment holding the installed packages. Ignored by git. |
| `backend/requirements.txt` | The Python packages the backend needs. |
| `backend/pytest.ini` | Settings for pytest (where tests are, async mode). |
| `backend/app/config.py` | Settings loaded from the environment. The app won't start without the Gemini API key. |
| `backend/app/agent/agent.py` | `run_agent(message)`: loads the system prompt, calls Gemini asynchronously and returns the reply. Raises `AgentError` if Gemini fails or returns nothing. A Gemini call that fails with a temporary server error (500, 503, 504) is retried up to 2 times (pauses of 2 s and 5 s); 429, 401, 403, 400 and timeouts are not retried. Each call times out after 20 s. If `GEMINI_FALLBACK_MODEL` is set (and differs from `GEMINI_MODEL`) and the main model still fails with 500, 503 or 504 after its retries, the request switches to the fallback model (with the same retries) and stays on it for its remaining tool rounds; 4xx errors and timeouts never trigger the switch, and each request starts on the main model again. If Gemini is still overloaded (503) after all that it raises `AgentBusyError`. The HTTP status code of a failure is written to the audit log (`http_status`, plus a `model_retry` event for each retry). No modes, tools or history yet. |
| `backend/app/agent/prompts/system.md` | The system prompt: Trailwise's role, scope (planning only, no booking), honesty rules (transport is indicative only) and response guidelines. |
| `backend/app/agent/prompts/explore.md`, `route.md`, `itinerary.md`, `email.md` | The four mode prompts from `plan.md`, each with a prompt ID and version (for example `trailwise.route`, version 1). Each adds to `system.md` and never weakens it. Explore: 3 to 6 suggestions, indicative transport only. Route: `search_buses` with a `YYYY-MM-DD` date, `get_train_details` only with a train number, no train seat claims, qualitative advice for other transport, always "Verify before travel". Itinerary: 1 to 14 days, Morning, Afternoon and Evening blocks, no clock times or prices, optional JSON output. Email: a draft only, sent only on explicit request to the verified address after confirmation. They use only tools that exist. Not loaded by any code yet: nothing selects a mode until `modes.py` is built. |
| `backend/app/agent/mcp_client.py` | `McpClient` (async context manager): launches `mcp_server/server.py` with the server's own `.venv`, then `list_tools()` and `call_tool(name, args)`. Calls return a dict; failures become an error object. Used by `agent.py` for tool discovery and calls. |
| `backend/tests/test_mcp_client.py` | Starts the real server: checks connection, discovery of `get_weather`, and error objects for a bad argument and an unknown tool (no weather API call). |
| `backend/tests/test_agent_tools.py` | Tool loop tests with fake Gemini and MCP: tool discovered, called and result used; unknown tool refused; tool error passed on; 5-round cap; fallback when MCP is down or discovery fails; empty reply. |
| `backend/app/api/chat.py` | `POST /chat`: takes a message, calls `run_agent`, returns `{reply}`. An agent failure becomes a generic 502; an overloaded Gemini (HTTP 503 after retries) becomes a 503 "busy, try again in a minute" with a `Retry-After` header. |
| `backend/app/models/chat.py` | `ChatRequest` (message trimmed, 1 to `max_message_chars` characters) and `ChatResponse` (reply). |
| `backend/app/main.py` | Creates the FastAPI app. `/health` says the server is up, `/ready` checks Redis and MongoDB. |
| `mcp_server/` | Separate MCP tool server. Run from `mcp_server/` with `python server.py` (stdio) and test with `pytest`. Not yet connected to the Gemini agent. |
| `mcp_server/tools/weather.py` | `get_weather(location)`: arguments validated, Redis cache first (20 min TTL, skipped if Redis is down), then OpenWeatherMap with a 10 s timeout. Returns structured fields or an error object, never raises. |
| `mcp_server/tools/web_search.py` | `web_search(query)`: query validated (3 to 200 characters), Redis cache first (1 hour TTL, skipped if Redis is down), then one Gemini call with only Google Search grounding and a 20 s timeout. Returns a capped `answer`, up to 5 `sources` (title and URL) and the `queries` run, or an error object, never raises. Uses `GEMINI_SEARCH_MODEL` (default `gemini-3.5-flash`). Grounding may need a paid quota: on the free tier it can return 429, which becomes `unavailable`. Not a source for transport timings or prices. |
| `mcp_server/tools/train_tool.py` | `get_train_details(train_number)`: number validated (1 to 10 letters, digits or hyphens), then one RailRadar call (`GET https://api.railradar.in/v1/trains/{number}`, key in the `Authorization: Bearer` header, 10 s timeout). Returns name, type, route ends, run days, classes, a bookable flag and up to 60 scheduled stops, or an error object (`invalid_arguments`, `not_configured`, `train_not_found`, `unavailable`), never raises. Scheduled data only: RailRadar has no seat availability API, so no seat counts or fares. Every result carries a "verify before travel" notice. No cache yet. Needs `RAILRADAR_API_KEY` in the root `.env` (free plan: 100 requests a month). |
| `mcp_server/tests/test_train_tool.py` | Fake RailRadar: shaped result, header-only key, no seat or fare fields, bad arguments, 404, 500, 429 and timeout, empty body, stop cap, missing key, and registration in the server. |
| `mcp_server/tools/bus.py` | `search_buses(source_city, destination_city, journey_date)`: city names (1 to 60 characters) and a `YYYY-MM-DD` date (today up to 120 days ahead, India time) are validated, then 3 Parse.bot calls on the redBus scraper `35a9b6fe-...` pinned to snapshot version 11 (header `API-Snapshot-Version`): `get_city_suggestions` for each city (in parallel), then `search_buses`. The key goes in the `X-API-Key` header, never the URL. Returns up to 20 buses with operator, bus type, departure and arrival times, duration, boarding and dropping points, fares in INR, available and total seats, AC and sleeper flags and rating (fields the source did not return stay empty), or an error object (`invalid_arguments`, `not_configured`, `city_not_found`, `no_buses_found`, `rate_limited`, `unavailable`), never raises. Third-party scraped data, with a "verify before travel" notice; it cannot book. No cache yet. Needs `PARSEBOT_API_KEY` in the root `.env`; the plan allows about 100 calls a day, so about 33 searches. |
| `mcp_server/tests/test_bus.py` | Fake Parse.bot: shaped result, city (not airport) chosen, key and snapshot header only in headers, bad cities and dates (past, too far ahead, same city), unknown city, no buses, 429 on either call, 500, timeout, error envelope, cap of 20, missing key, and registration in the server. |
| `mcp_server/tools/email_tool.py` | `send_trip_email(subject, body, confirmed)`: sends a plain-text trip email over async SMTP (`aiosmtplib`, 15 s timeout). There is no recipient argument: it can only send to one address from the root `.env`: `VERIFIED_RECIPIENT`, or the `SMTP_EMAIL` account itself when that is not set (a stand-in for the user's verified address until login exists), so a prompt injection cannot redirect it. Subject 1 to 150 characters on one line (blocks header injection), body 1 to 5000 characters, `confirmed` must be a real `true` (a prompt-level safeguard backing the rule "show the draft and ask first"). Sender and recipient are checked with `EmailStr`. The tool adds a "Verify before travel" footer itself. Returns the masked address on success, or an error object (`invalid_arguments`, `not_confirmed`, `not_configured`, `unavailable`), never raises. The SMTP password, body and full address are never logged. Settings from `.env`: `SMTP_EMAIL` (the account: it is the From address and the login name) and `SMTP_PASSWORD` (the names in `.env_example`), optional `VERIFIED_RECIPIENT`, and the server settings `SMTP_HOST` (default `localhost`), `SMTP_PORT` (1025) and `SMTP_STARTTLS` (false). The server defaults match Mailpit in `docker-compose.yml` (no password, no login), so local tests send nothing outside your machine; for a real provider set the host, port 587 and `SMTP_STARTTLS=true`. The tool logs in only when `SMTP_PASSWORD` is set. Not built yet: authentication and a rate limit (a Redis counter is needed because the MCP server starts a new process per request). Inside the Docker container the subprocess reads `SMTP_HOST` from `.env`, so use `mailpit` there. |
| `mcp_server/tests/test_email_tool.py` | Fake SMTP: fixed recipient and footer, login and TLS options (port 465), not confirmed, bad subject or body (including header injection and a non-boolean `confirmed`), not configured, SMTP errors and timeouts, no password or address in logs or replies, and registration with no recipient parameter. |
| `mcp_server/tests/test_web_search.py` | Fake Gemini and Redis: cache miss and hit, bad arguments, Gemini failure and 429, empty answer, caps, missing key, and registration in the server. |
| `mcp_server/server.py` | Creates the `FastMCP` server and registers `get_weather`, `web_search`, `get_train_details`, `search_buses` and `send_trip_email`. Keeps the `httpx`, `httpcore`, `httpx2` and `google_genai` loggers at WARNING so API keys are never logged. |
| `mcp_server/tests/test_no_key_in_logs.py` | Runs the weather tool through a real httpx client (fake transport), and the web search failure path, and fails if the API key shows up in captured logs or replies. |
| `backend/app/core/audit.py` | The audit log. `run_agent` writes one JSON line per event to `backend/logs/audit.log` (rotating, 5 MB x 5, ignored by git): `request_received`, `mcp_discovery`, `model_decision` (tool names Gemini chose), `tool_call`, then `request_completed` or `request_failed`. Each line has a UTC timestamp, a request ID, a status and durations. Message text, tool arguments and replies are never logged (only lengths, a short hash and argument names); secret-named fields are dropped, key-like values redacted, errors logged by type only. A logging failure never breaks the chat. Settings: `AUDIT_LOG_ENABLED`, `AUDIT_LOG_PATH`. |
| `backend/tests/test_audit.py` | Audit tests with fake Gemini and MCP and a temp log file: full run recorded, no message text or arguments or keys in the log, tool error and refused tool recorded, unwritable log does not break chat, disabled log writes nothing. |
| `backend/tests/conftest.py` | Test setup that supplies a fake API key and turns the audit log off by default. |
| `backend/tests/test_gemini_retry.py` | Fake Gemini client: 503 then success (retried, retries logged), 500 and 504 retried, gives up after 2 retries with a "busy" error that carries the status code, 429, 401, 403 and 400 not retried, timeouts not retried, status code recorded in the audit log, and the 20 s timeout setting. |
| `backend/tests/test_model_fallback.py` | Fake Gemini client and fixed model names: fallback used after the main model's retries (on 503, 500 and 504), later tool rounds stay on it, each request starts on the main model, no fallback on 429, 401, 403, 400 or timeouts, off when unset or equal to the main model, both models failing gives the busy error, and the `model_fallback` audit event. |
| `backend/tests/test_health.py` | Smoke test for `/health`. |
| `backend/tests/test_chat.py` | Tests for `/chat` with a fake agent: good reply, empty, blank, missing or too-long message (422) and agent failure (502). |

**Planned, not created yet**

| Item | Purpose |
|---|---|
| Map, itinerary view and streaming in `frontend/` | `MapView` (Leaflet, needs a map-tile provider decision), `ItineraryView` (needs structured itineraries from the backend) and a streaming hook (needs SSE in the backend). The chat, voice input and the page shell exist. |

## Getting Started

Start the local services:

```
docker compose up -d
```

Mailpit's web UI is at http://localhost:8025. Docker is not needed for `/chat`; it is used by `/ready` and by features still to come (chat history, saved trips).

Run the backend (from `backend/`, after creating the virtual environment, installing `requirements.txt` and setting `GEMINI_API_KEY` in the project-root `.env`; see `.env.example`):

```
uvicorn app.main:app --reload
```

Run the chat app (from `frontend/`, in a second terminal; needs Node.js), then open http://localhost:5173:

```
npm install
npm run dev
```

`npm run build` checks that it compiles. The backend must be on http://localhost:8000 (change `VITE_API_URL` in `frontend/.env.local` if not).

Run the backend in Docker instead (from the project root; needs the project-root `.env`, which is mounted read-only into the container, never copied into the image):

```
docker compose build backend
docker compose up -d backend
docker compose exec backend pytest
```

The API is then at http://localhost:8000. The chat page is still served from your machine as above. Stop it with `docker compose stop backend`.
