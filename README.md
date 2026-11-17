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
| Frontend | React + Vite | Planned |
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
├── .claude/              Claude Code project setup
│   ├── settings.json     Permissions, blocked commands and the .env write-protection hook
│   └── skills/
│       ├── add-agent-tool/
│       │   └── SKILL.md  Step-by-step workflow for adding an agent tool
│       └── push-to-github/
│           └── SKILL.md  Safe stage, commit and push workflow (approval at each step)
├── frontend/             Web page
│   └── index.html        Chat page (plain HTML, CSS and JS) that calls POST /chat
├── mcp_server/           MCP tool server (separate service, own .venv)
│   ├── server.py         Registers the tools and serves them over stdio
│   ├── config.py         Loads OPENWEATHER_API_KEY, GEMINI_API_KEY, GEMINI_SEARCH_MODEL and REDIS_URL from the root .env
│   ├── requirements.txt  Python packages for the MCP server
│   ├── pytest.ini        Test settings
│   ├── tools/
│   │   ├── weather.py    get_weather(location): validates, checks Redis, calls OpenWeatherMap
│   │   └── web_search.py web_search(query): validates, checks Redis, asks Gemini with Google Search grounding
│   └── tests/
│       ├── test_weather.py  Weather tool tests (API and Redis faked) and a registration check
│       ├── test_web_search.py  Web search tool tests (Gemini and Redis faked) and a registration check
│       └── test_no_key_in_logs.py  Regression test: the API key never appears in logs
└── backend/              FastAPI server
    ├── .venv/            Python virtual environment with installed packages (not committed)
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
    │   │       └── system.md  Basic Trailwise instructions: role, scope, response guidelines
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
| `.gitignore` | Stops `.env`, virtual environments, caches, build output and `logs/` (audit log) from being committed. |
| `frontend/index.html` | The chat page: a message box and conversation view that sends `POST http://localhost:8000/chat` and shows the reply or a clear error. No build step; serve it with `python -m http.server 5173` from `frontend/` and open http://localhost:5173. |
| `backend/` | The FastAPI server. The agent and its tools will be added here. |
| `backend/.venv/` | The Python virtual environment holding the installed packages. Ignored by git. |
| `backend/requirements.txt` | The Python packages the backend needs. |
| `backend/pytest.ini` | Settings for pytest (where tests are, async mode). |
| `backend/app/config.py` | Settings loaded from the environment. The app won't start without the Gemini API key. |
| `backend/app/agent/agent.py` | `run_agent(message)`: loads the system prompt, calls Gemini asynchronously and returns the reply. Raises `AgentError` if Gemini fails or returns nothing. No modes, tools or history yet. |
| `backend/app/agent/prompts/system.md` | The system prompt: Trailwise's role, scope (planning only, no booking), honesty rules (transport is indicative only) and response guidelines. |
| `backend/app/agent/mcp_client.py` | `McpClient` (async context manager): launches `mcp_server/server.py` with the server's own `.venv`, then `list_tools()` and `call_tool(name, args)`. Calls return a dict; failures become an error object. Used by `agent.py` for tool discovery and calls. |
| `backend/tests/test_mcp_client.py` | Starts the real server: checks connection, discovery of `get_weather`, and error objects for a bad argument and an unknown tool (no weather API call). |
| `backend/tests/test_agent_tools.py` | Tool loop tests with fake Gemini and MCP: tool discovered, called and result used; unknown tool refused; tool error passed on; 5-round cap; fallback when MCP is down or discovery fails; empty reply. |
| `backend/app/api/chat.py` | `POST /chat`: takes a message, calls `run_agent`, returns `{reply}`. An agent failure becomes a generic 502. |
| `backend/app/models/chat.py` | `ChatRequest` (message trimmed, 1 to `max_message_chars` characters) and `ChatResponse` (reply). |
| `backend/app/main.py` | Creates the FastAPI app. `/health` says the server is up, `/ready` checks Redis and MongoDB. |
| `mcp_server/` | Separate MCP tool server. Run from `mcp_server/` with `python server.py` (stdio) and test with `pytest`. Not yet connected to the Gemini agent. |
| `mcp_server/tools/weather.py` | `get_weather(location)`: arguments validated, Redis cache first (20 min TTL, skipped if Redis is down), then OpenWeatherMap with a 10 s timeout. Returns structured fields or an error object, never raises. |
| `mcp_server/tools/web_search.py` | `web_search(query)`: query validated (3 to 200 characters), Redis cache first (1 hour TTL, skipped if Redis is down), then one Gemini call with only Google Search grounding and a 20 s timeout. Returns a capped `answer`, up to 5 `sources` (title and URL) and the `queries` run, or an error object, never raises. Uses `GEMINI_SEARCH_MODEL` (default `gemini-3.5-flash`). Grounding may need a paid quota: on the free tier it can return 429, which becomes `unavailable`. Not a source for transport timings or prices. |
| `mcp_server/tests/test_web_search.py` | Fake Gemini and Redis: cache miss and hit, bad arguments, Gemini failure and 429, empty answer, caps, missing key, and registration in the server. |
| `mcp_server/server.py` | Creates the `FastMCP` server and registers `get_weather` and `web_search`. Keeps the `httpx`, `httpcore`, `httpx2` and `google_genai` loggers at WARNING so API keys are never logged. |
| `mcp_server/tests/test_no_key_in_logs.py` | Runs the weather tool through a real httpx client (fake transport), and the web search failure path, and fails if the API key shows up in captured logs or replies. |
| `backend/app/core/audit.py` | The audit log. `run_agent` writes one JSON line per event to `backend/logs/audit.log` (rotating, 5 MB x 5, ignored by git): `request_received`, `mcp_discovery`, `model_decision` (tool names Gemini chose), `tool_call`, then `request_completed` or `request_failed`. Each line has a UTC timestamp, a request ID, a status and durations. Message text, tool arguments and replies are never logged (only lengths, a short hash and argument names); secret-named fields are dropped, key-like values redacted, errors logged by type only. A logging failure never breaks the chat. Settings: `AUDIT_LOG_ENABLED`, `AUDIT_LOG_PATH`. |
| `backend/tests/test_audit.py` | Audit tests with fake Gemini and MCP and a temp log file: full run recorded, no message text or arguments or keys in the log, tool error and refused tool recorded, unwritable log does not break chat, disabled log writes nothing. |
| `backend/tests/conftest.py` | Test setup that supplies a fake API key and turns the audit log off by default. |
| `backend/tests/test_health.py` | Smoke test for `/health`. |
| `backend/tests/test_chat.py` | Tests for `/chat` with a fake agent: good reply, empty, blank, missing or too-long message (422) and agent failure (502). |

**Planned, not created yet**

| Item | Purpose |
|---|---|
| React + Vite app in `frontend/` | The full chat, map and itinerary UI. Today `frontend/` has only the plain `index.html` chat page. |

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

Run the chat page (from `frontend/`, in a second terminal), then open http://localhost:5173:

```
python -m http.server 5173
```
