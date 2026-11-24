# Trailwise AI Traveler: File Structure

Last updated: 2026-11-24 (checked against the files on disk after merging the trips and reply-actions branch).

Every file below exists in the project folder today, with one plain line each. Files and folders that do not exist yet are listed separately under "Not created yet". Generated folders (`node_modules/`, `.venv/`, `dist/`, `__pycache__/`, `.pytest_cache/`, `.git/`, `backend/logs/`) are left out.

**Status tags (exact words):** **[Built]** the file exists and does its job (read, not run). **[Partial]** it exists but something is missing (the line says what). **[Planned]** it does not exist (only used in "Not created yet", with what it depends on). **Unclear** could not be decided; listed at the end.

## Project root

- `.claude/settings.json` [Built]: Claude Code permissions, blocked commands and the hook that blocks writes to `.env` files.
- `.claude/skills/add-agent-tool/SKILL.md` [Built]: the playbook for adding a new agent tool.
- `.claude/skills/push-to-github/SKILL.md` [Built]: the safe stage, commit and push workflow.
- `.dockerignore` [Built]: keeps secrets, virtual environments and logs out of the Docker build.
- `.env` [Built]: the real keys and settings, never committed or shown.
- `.env.example` [Built]: the committed template with fake values. It lists the OpenAI, Pexels, SMTP, Keycloak, MongoDB, audit and debug settings the code reads (updated 2026-11-23).
- `.gitignore` [Built]: keeps `.env` and other `.env.*` files (the two `.env.example` templates stay tracked), `.claude/settings.local.json`, virtual environments, caches, build output, logs and editor files out of git.
- `ARCHITECTURE.md` [Built]: how the system is built, with a status tag on each part.
- `CLAUDE.md` [Built]: the rules Claude Code reads every session.
- `claudeprompts.md` [Built]: the log of prompts given to Claude Code and their outcomes.
- `docker-compose.yml` [Built]: runs Redis, Mailpit, Keycloak, the backend and the frontend (MongoDB is Atlas, not a container).
- `FILE_STRUCTURE.md` [Built]: this file.
- `plan.md` [Built]: the project plan, phases and launch checklist.
- `README.md` [Built]: the front page: status, structure, setup and how to run.
- `docs/agent.md` [Built]: how one chat request flows through the agent, every tool and the enforced rules.
- `docs/redis.md` [Built]: every Redis key, what is never stored and what happens when Redis is down.
- `docs/prompts.md` [Built]: the rules for versioning the five prompt files.

## backend/ (FastAPI server)

- `backend/Dockerfile` [Built]: builds one image with the backend and `mcp_server`, non-root, port 8000.
- `backend/pytest.ini` [Built]: pytest settings (async tests need no marker).
- `backend/requirements.txt` [Built]: the Python packages the backend needs.
- `backend/.gitignore` [Built]: keeps `.venv`, caches, `logs/` and `.env` out of git for this folder.
- `backend/app/__init__.py` [Built]: marks `app` as a package.
- `backend/app/main.py` [Built]: creates the app, the shared MongoDB client, the Preferences, trips and explore-history indexes, and `/health` and `/ready`.
- `backend/app/config.py` [Built]: reads all settings from the environment and the root `.env`.
- `backend/app/api/chat.py` [Partial]: the `POST /chat` route; for a signed-in user it also saves one explore-history entry after an explore reply. Missing: streaming and saved chat history.
- `backend/app/api/me.py` [Built]: `GET /me`, `GET`/`PUT /me/preferences` and `DELETE /me/data`.
- `backend/app/api/trips.py` [Built]: saved trips and explore history for signed-in users (`/trips`, `/explore-history`) and `POST /trips/{id}/email`.
- `backend/app/agent/__init__.py` [Built]: marks `agent` as a package.
- `backend/app/agent/agent.py` [Built]: sends the message to OpenAI and runs the capped tool loop through the MCP client.
- `backend/app/agent/modes.py` [Built]: picks the mode, builds the system prompt, lists the forced and required tools, and builds the saved-preferences line.
- `backend/app/agent/legs.py` [Built]: route mode's gateway-flow helpers (geocoded-pair checks, the destination-not-found signal, the map legs).
- `backend/app/agent/mcp_client.py` [Built]: starts the MCP server over stdio, lists its tools and calls them.
- `backend/app/agent/openai_adapter.py` [Built]: pure helpers for the OpenAI Responses API (tool definitions, tool choice, arguments, errors).
- `backend/app/agent/prompts/system.md` [Built]: the main system prompt.
- `backend/app/agent/prompts/explore.md` [Built]: the Explore mode prompt.
- `backend/app/agent/prompts/route.md` [Built]: the Route mode prompt.
- `backend/app/agent/prompts/itinerary.md` [Built]: the Itinerary mode prompt.
- `backend/app/agent/prompts/email.md` [Built]: the Email mode prompt.
- `backend/app/core/audit.py` [Built]: the JSON audit log of agent activity, never secrets or message text.
- `backend/app/core/auth.py` [Built]: verifies Keycloak access tokens and reads guest session ids.
- `backend/app/core/debug_log.py` [Built]: the optional plain-text debug log for local testing.
- `backend/app/core/mongo.py` [Built]: the shared MongoDB client, the ping and `get_db`.
- `backend/app/core/rate_limit.py` [Partial]: a per-key counter in Redis (fixed window). Missing: only trip emails use it (5 per user per hour); chat and the other routes are not limited.
- `backend/app/core/preferences.py` [Built]: saves and loads preferences in the `Preferences` collection.
- `backend/app/services/trip_email.py` [Built]: builds the plain-text email of a saved trip on the server and sends it by SMTP.
- `backend/app/services/trips_store.py` [Built]: MongoDB reads and writes for trips and explore history (`owner_id` in every query, indexes).
- `backend/app/models/chat.py` [Built]: the request and response shapes for `/chat`, including the memory and preferences fields.
- `backend/app/models/trips.py` [Built]: strict models for trips and explore history (size caps, no `owner_id` in any input).
- `backend/app/models/preferences.py` [Built]: the saved-preferences shape and its allowed values.
- `backend/tests/conftest.py` [Built]: shared test setup with a fake MongoDB client and fake settings.
- `backend/tests/test_agent_tools.py` [Built]: tests for the agent's tool loop.
- `backend/tests/test_audit.py` [Built]: tests for the audit log.
- `backend/tests/test_auth.py` [Built]: tests for the Keycloak token check.
- `backend/tests/test_chat.py` [Built]: tests for `POST /chat`.
- `backend/tests/test_chat_images.py` [Built]: tests for photos travelling with a reply.
- `backend/tests/test_chat_memory.py` [Built]: tests for the per-session memory and its validation.
- `backend/tests/test_debug_log.py` [Built]: tests for the optional debug log.
- `backend/tests/test_explore_required.py` [Built]: tests for explore mode's required tools.
- `backend/tests/test_force_web_search.py` [Built]: tests for forcing `web_search` first.
- `backend/tests/test_gateway_flow.py` [Built]: tests for the gateway flow (remote destinations, dashed legs).
- `backend/tests/test_health.py` [Built]: the `/health` smoke test.
- `backend/tests/test_mcp_client.py` [Built]: tests that start the real MCP server.
- `backend/tests/test_mcp_timeouts.py` [Built]: tests for tool call time limits.
- `backend/tests/test_model_fallback.py` [Built]: tests for the fallback model.
- `backend/tests/test_modes.py` [Built]: tests for mode selection and prompt building.
- `backend/tests/test_mongo.py` [Built]: tests for the MongoDB client and `/ready`.
- `backend/tests/test_openai_adapter.py` [Built]: tests for the adapter helpers.
- `backend/tests/test_openai_config.py` [Built]: tests for the OpenAI settings.
- `backend/tests/test_openai_retry.py` [Built]: tests for OpenAI retries.
- `backend/tests/test_preferences.py` [Built]: tests for `GET`/`PUT /me/preferences` with a fake database.
- `backend/tests/test_preferences_prompt.py` [Built]: tests for the saved-preferences line with a fake provider.
- `backend/tests/test_required_modes.py` [Built]: tests for required tools in itinerary and route mode and the overall time cap.
- `backend/tests/test_trip_email.py` [Built]: tests for `POST /trips/{id}/email` (fake database, Redis and SMTP).
- `backend/tests/test_trips.py` [Built]: tests for trips, explore history and `DELETE /me/data` with a fake MongoDB.
- `backend/tests/test_tool_choice.py` [Built]: tests for forcing a tool on round 1.
- `backend/tests/test_train_prompts.py` [Built]: tests for the train prompt rules.

The 26 backend test files hold 503 test functions (917 passed, 1 failed on 2026-11-24: see Unclear).

## mcp_server/ (tool server, started by the backend over stdio)

- `mcp_server/server.py` [Built]: registers the eight tools and serves them over stdio.
- `mcp_server/config.py` [Built]: the tools' settings from the root `.env`.
- `mcp_server/pytest.ini` [Built]: pytest settings for the tool tests.
- `mcp_server/requirements.txt` [Built]: the Python packages for the tool server.
- `mcp_server/.gitignore` [Built]: keeps `.venv`, caches and `.env` out of git for this folder.
- `mcp_server/tools/weather.py` [Built]: `get_weather`, current weather from OpenWeatherMap.
- `mcp_server/tools/web_search.py` [Built]: `web_search`, OpenAI's built-in web search with sources.
- `mcp_server/tools/train_tool.py` [Built]: `search_trains`, scheduled trains from RailRadar.
- `mcp_server/tools/bus.py` [Built]: `search_buses`, redBus results via Parse.bot.
- `mcp_server/tools/email_tool.py` [Built]: `send_trip_email`, plain-text email to one fixed address.
- `mcp_server/tools/geocode_tool.py` [Built]: `geocode_places`, map coordinates for up to 12 places.
- `mcp_server/tools/routes_tool.py` [Built]: `get_route`, a driving route line for the map.
- `mcp_server/tools/images_tool.py` [Built]: `get_place_images`, up to 3 filtered Pexels photos with credit.
- `mcp_server/tests/test_weather.py` [Built]: weather tool tests.
- `mcp_server/tests/test_web_search.py` [Built]: web search tool tests.
- `mcp_server/tests/test_train_tool.py` [Built]: train tool tests.
- `mcp_server/tests/test_bus.py` [Built]: bus tool tests.
- `mcp_server/tests/test_email_tool.py` [Built]: email tool tests.
- `mcp_server/tests/test_geocode.py` [Built]: geocode tool tests.
- `mcp_server/tests/test_routes.py` [Built]: route tool tests.
- `mcp_server/tests/test_images.py` [Built]: image tool tests.
- `mcp_server/tests/test_no_key_in_logs.py` [Built]: checks that an API key never appears in logs.

The 9 MCP test files hold 329 test functions (609 cases passed on 2026-11-24; parametrized cases explain the higher number).

## keycloak/ (login server setup)

- `keycloak/trailwise-realm.json` [Built]: the realm imported on first start (sign-up, email verification, public PKCE client, no secrets).
- `keycloak/themes/trailwise/login/theme.properties` [Built]: builds the login theme on Keycloak's own theme.
- `keycloak/themes/trailwise/login/resources/css/trailwise.css` [Built]: the sunset page, rounded card and orange buttons.

## frontend/ (React + Vite app)

- `frontend/Dockerfile` [Built]: builds the app and serves it with non-root nginx on port 8080.
- `frontend/.dockerignore` [Built]: keeps `node_modules`, `dist` and `.env` files out of the build.
- `frontend/.gitignore` [Built]: keeps `node_modules`, `dist`, coverage and local env files out of git for this folder.
- `frontend/.env.example` [Built]: the non-secret settings (backend address, Keycloak, map tiles).
- `frontend/nginx.conf` [Partial]: serves the built app with an `index.html` fallback. Missing: TLS, proxying, security headers and SSE settings.
- `frontend/index.html` [Built]: the Vite entry page.
- `frontend/package.json` [Built]: the npm packages and the `dev`, `build` and `preview` scripts (there is no `test` script; tests run with `node --test`).
- `frontend/package-lock.json` [Built]: the exact package versions.
- `frontend/vite.config.js` [Built]: Vite settings (React plugin, port 5173).
- `frontend/public/favicon.svg` [Built]: the tab icon.
- `frontend/public/places/goa.svg`, `hampi.svg`, `jaipur.svg`, `kerala.svg`, `ladakh.svg`, `varanasi.svg` [Partial]: six placeholder illustrations for the place cards. Missing: real photos.
- `frontend/reference/README.md` [Built]: says the local reference screenshots are for design only.
- `frontend/src/main.jsx` [Built]: mounts the app.
- `frontend/src/App.jsx` [Built]: the app shell, routing between `/` and `/app`, and the shared chat and preferences state.
- `frontend/src/styles.css` [Built]: all the styles.
- `frontend/src/api/client.js` [Built]: the only code that calls the backend (get, post, put and friendly errors).
- `frontend/src/api/chat.js` [Built]: `sendMessage`, which calls `POST /chat`.
- `frontend/src/api/trips.js` [Built]: save a trip and email a saved trip (`POST /trips`, `POST /trips/{id}/email`).
- `frontend/src/api/preferences.js` [Built]: preference values, the browser copy and `GET`/`PUT /me/preferences`.
- `frontend/src/auth/session.js` [Built]: Keycloak sign-in, the guest session id and the auth headers.
- `frontend/src/data/places.js` [Partial]: six hardcoded places, ranking by interest and a mock place search. Missing: a backend places source and a real search.
- `frontend/src/data/mockReturningUser.js` [Partial]: mock returning-user data, off by default. Missing: backend endpoints for saved trips.
- `frontend/src/hooks/useAuth.js` [Built]: who is signed in, plus login and logout.
- `frontend/src/hooks/useChat.js` [Built]: the conversation state, the per-session memory, the saved preferences sent with each message and the map legs.
- `frontend/src/hooks/chatMemory.js` [Built]: pure helpers for the chat memory rules.
- `frontend/src/hooks/usePreferences.js` [Built]: preferences state: load, edit, reset, save, and the copy to a new account.
- `frontend/src/hooks/useVoice.js` [Built]: voice input in English, Hindi and Kannada.
- `frontend/src/components/NavBar.jsx` [Built]: the public top bar.
- `frontend/src/components/HomeDashboard.jsx` [Partial]: the Home section of `/app`. Missing: real returning-user data (mock, off by default).
- `frontend/src/components/PlacePanel.jsx` [Built]: the side panel on Explore.
- `frontend/src/components/ImageRow.jsx` [Built]: up to 3 photo cards under a reply, with credit.
- `frontend/src/components/ChatSplit.jsx` [Built]: the two-column chat and map layout.
- `frontend/src/components/MapPanel.jsx` [Built]: the map panel (column on desktop, overlay on phones).
- `frontend/src/components/PlaceMap.jsx` [Built]: the Leaflet map with pins, card and route line.
- `frontend/src/components/mapData.js` [Built]: pure helpers for the map.
- `frontend/src/components/replyActions.js` [Built]: the rule for which buttons appear under a reply.
- `frontend/src/components/ReplyActions.jsx` [Built]: the Save trip, Email plan and Plan a trip to buttons under a reply.
- `frontend/src/components/LoginPrompt.jsx` [Built]: the popup that asks a guest to log in.
- `frontend/src/components/ChatWindow.jsx` [Built]: the chat area: messages plus input.
- `frontend/src/components/MessageList.jsx` [Built]: the scrolling conversation.
- `frontend/src/components/MessageBubble.jsx` [Built]: one message, with bot replies as safe Markdown.
- `frontend/src/components/ChatInput.jsx` [Built]: the message box and Send button.
- `frontend/src/components/VoiceButton.jsx` [Built]: the language picker and microphone button.
- `frontend/src/components/Disclaimer.jsx` [Built]: the "verify before travel" notice.
- `frontend/src/pages/Home.jsx` [Built]: the public landing page.
- `frontend/src/pages/AppHome.jsx` [Partial]: the `/app` page with Home, Chats, Saved, Explore and Preferences. Missing: the Saved panel is "coming soon" (needs saved trips).
- `frontend/src/pages/Chat.jsx` [Built]: the chat screen.
- `frontend/src/pages/Explore.jsx` [Partial]: the Explore section. Missing: the search is a mock over six hardcoded places (needs a backend search).
- `frontend/src/pages/Preferences.jsx` [Built]: the Preferences page with four cards and a save bar.
- `frontend/src/pages/SavedTrips.jsx` [Partial]: a placeholder page that nothing imports. Missing: saved trips (needs the trips backend).
- `frontend/src/pages/Login.jsx` [Partial]: an unused placeholder; login goes straight to Keycloak. Missing: nothing is needed, it can be removed (owner's decision).
- `frontend/tests/chat-images.test.mjs` [Built]: tests for photos under a reply.
- `frontend/tests/chat-memory.test.mjs` [Built]: tests for the chat memory rules.
- `frontend/tests/map-panel.test.mjs` [Built]: tests for the map panel.
- `frontend/tests/place-ranking.test.mjs` [Built]: tests for ranking by interest.
- `frontend/tests/reply-actions.test.mjs` [Built]: tests for the reply buttons and the login popup.
- `frontend/tests/preferences.test.mjs` [Built]: tests for the preferences helpers.
- `frontend/tests/preferences-chat.test.mjs` [Built]: tests for what the chat sends and the voice language.

The 7 frontend test files run with `cd frontend && node --test` (207 passed on 2026-11-24).

## Not created yet

Nothing below exists on disk. "Depends on" says what has to come first.

| Planned item | Tag | Depends on |
|---|---|---|
| `nginx/` (load balancer, HTTPS, SSE settings) | [Planned] | A production deployment plan; today only `frontend/nginx.conf` exists |
| `evals/` (test prompts for answer quality) | [Planned] | A golden prompt set (30 to 50 prompts) and CI |
| `.github/workflows/` (CI/CD) | [Planned] | A decision on lint and test commands; no lint command exists |
| `mcp_server/Dockerfile` | [Planned] | Running the MCP server as its own service instead of a subprocess |
| One client per outside provider in `backend/app/services/` | [Planned] | Moving provider code out of `mcp_server/tools/`; `services/` exists now but holds only the trips store and the trip email |
| `backend/app/db/` or a shared Redis helper (`cache`) | [Planned] | Deciding to share one Redis helper between backend and tools |
| Chat history routes, `users` record and data export (`GET /me/export`) | [Planned] | `users` and `ChatHistory` collections; trips, explore history and `DELETE /me/data` exist already |
| General rate limiting (chat, per-IP, daily spend cap) | [Planned] | The shared Redis helper and a limiter library; `core/rate_limit.py` exists but only limits trip emails |
| Itinerary schema and `frontend/src/components/ItineraryView.jsx` | [Planned] | Structured itinerary output from the backend |
| A streaming hook in `frontend/src/hooks/` | [Planned] | An SSE route in the backend |
| Real places endpoint and Explore search | [Planned] | A backend places source |
| `docker-compose.override.yml` | Unclear | Optional and git-ignored; not present in this folder (see below) |

## Where new things go

| I want to add... | Put it here |
|---|---|
| A new API endpoint | `backend/app/api/` |
| Request or response shape | `backend/app/models/` |
| A new outside API (weather, transport, etc.) | `mcp_server/tools/` (as a tool), until a `services` layer exists |
| A new tool for the agent | `mcp_server/tools/`, then register it in `mcp_server/server.py` (use the `add-agent-tool` skill) |
| Change agent behavior or prompt | `backend/app/agent/` |
| A database connection or collection | `backend/app/core/` (see `mongo.py` and `preferences.py`) |
| A new cache rule (key and TTL) | The tool that uses it, in `mcp_server/tools/` |
| Keycloak token check and guest session logic | `backend/app/core/auth.py`. Passwords and login screens live in Keycloak, not our code |
| A new setting or API key | `backend/app/config.py` (or `mcp_server/config.py` for a tool) and `.env.example` |
| A backend test | `backend/tests/` |
| A new UI piece | `frontend/src/components/` |
| A new screen | `frontend/src/pages/` |
| A call to the backend from React | `frontend/src/api/` |
| Reusable React logic | `frontend/src/hooks/` |
| A test prompt for AI quality | `evals/` (not created yet) |
| A new Claude Code playbook | `.claude/skills/<name>/SKILL.md` |
| A new rule for Claude Code | `CLAUDE.md` |
| A new document | project root or `docs/` |

**Quick check before you add a file:** ask "is this a route, a tool, a model, or a UI piece?" The answer tells you the folder.

## Must stay out of Git

`.env` and other `.env.*` files (not the `.env.example` templates), `.claude/settings.local.json`, `backend/.venv/`, `.pytest_cache/`, `__pycache__/`, `node_modules/`, `dist/`, `*.log`, `backend/logs/`, `debug-logs/`, `docker-compose.override.yml`

Check with `git status`. If `.env` shows up, run `git rm --cached .env`, commit, and rotate every key in it.

## Unclear (for the owner)

- `docker-compose.override.yml`: described in older docs as an optional local file that mounts `./debug-logs`; it is not in this folder, so whether you use it is unknown.
- One backend test fails: `test_only_the_override_file_mounts_the_debug_logs` reads the git-ignored `docker-compose.override.yml`, which is not in this folder. Whether you use that file is your decision.
- Whether the Docker images and the Keycloak realm import work today: not run during the audit.
