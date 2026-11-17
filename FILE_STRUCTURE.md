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
├── backend/                   # FastAPI server
├── frontend/                  # React + Vite app
├── mcp_server/                # exists (weather and web_search tools): Tools the AI agent calls
├── nginx/                     # (to add) Load balancer and HTTPS
├── evals/                     # (to add) Test prompts for AI answer quality
├── .env                       # Real keys (never commit)
├── .env.example               # (to add) Fake placeholder keys, safe to commit
├── .gitignore                 # Keeps .env, .venv, node_modules out of Git
├── ARCHITECTURE.md            # How the system is built
├── CLAUDE.md                  # Always-on rules for Claude Code
├── claudeprompts.md           # Saved prompts for Claude Code
├── docker-compose.yml         # Runs MongoDB, Redis, and later all services
├── FILE_STRUCTURE.md          # This file
├── plan.md                    # Project plan
└── README.md                  # How to set up and run
```

| Item | What it is for |
|---|---|
| `.claude/settings.json` | Controls what Claude Code may run or read |
| `.claude/skills/add-agent-tool/` | Step-by-step playbook Claude follows when adding a tool |
| `CLAUDE.md` | Project rules Claude reads every session |
| `.env` | Real API keys and passwords. Must stay out of Git |
| `.env.example` | Template other people copy to make their own `.env` |
| `docker-compose.yml` | Starts the databases (and later the whole app) |
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
│   ├── api/                   # Routes (endpoints): auth, chat, trips
│   ├── agent/                 # AI agent, modes, prompts
│   ├── services/              # Weather, images, places, routes, transport, email
│   ├── db/                    # mongo.py and redis.py connections
│   ├── models/                # Pydantic schemas for requests and responses
│   └── core/                  # Security, rate limit, logging
│       └── audit.py           # exists: audit log of agent activity (JSON lines, no secrets or message text)
├── tests/                     # pytest tests (test_audit.py: exists, audit log tests)
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
├── agent.py          # exists: sends the message to Gemini and runs the MCP tool loop (capped at 5 rounds)
├── modes.py          # Explore, Route, Itinerary, Email and the tools each may use
├── mcp_client.py     # exists: McpClient, launches the MCP server over stdio, lists and calls tools
└── prompts/
    ├── system.md     # exists: main system prompt
    ├── explore.md
    ├── route.md
    ├── itinerary.md
    └── email.md
### MCP server (partly built)

```
mcp_server/
├── server.py                  # exists: registers all tools, serves over stdio
├── config.py                  # exists: settings from the root .env (weather and Gemini keys, Redis URL)
├── tools/                     # One file per tool
│   ├── weather.py             # exists: get_weather(location), cached, validated
│   └── web_search.py          # exists: web_search(query), Gemini Google Search grounding, cached, validated
├── tests/
│   ├── test_weather.py        # exists: weather tool and registration tests
│   ├── test_web_search.py     # exists: web search tool and registration tests (Gemini and Redis faked)
│   └── test_no_key_in_logs.py # exists: API key must never appear in logs
├── pytest.ini                 # exists
├── requirements.txt           # exists
└── Dockerfile                 # planned
```

---

## FRONTEND

```
frontend/
├── src/
│   ├── components/            # Chat, Map, Itinerary pieces
│   ├── pages/                 # Full screens (Home, Login, SavedTrips)
│   ├── api/                   # Functions that call the backend
│   ├── hooks/                 # Reusable React logic (streaming, voice)
│   ├── App.jsx
│   └── main.jsx
├── public/
├── index.html
├── package.json
└── .env.example               # Non-secret settings only (like backend URL)
```

Right now `frontend/` has one file, `index.html`: a plain HTML/CSS/JS chat page that calls `POST /chat` (exists). It will be replaced by the React + Vite layout above when that is set up (`npm create vite@latest frontend -- --template react`).

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
| Login, password, or token logic | `backend/app/core/` |
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
