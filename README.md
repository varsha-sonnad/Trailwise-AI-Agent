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
| Database | MongoDB Atlas (hosted) | Backend uses PyMongo's `AsyncMongoClient`; `MONGODB_URI` is required; `/ready` pings it |
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
├── docker-compose.yml    Local services: Redis, Mailpit, Keycloak, the backend and the frontend (the database is MongoDB Atlas, not a container)
├── .env.example          Template of settings (copy to .env in the project root)
├── .gitignore            Keeps secrets and build files out of git
├── keycloak/
│   ├── trailwise-realm.json  Keycloak realm imported on first start: "trailwise" realm, public "trailwise-web" client (PKCE), sign-up and email verification, the Trailwise login theme and the registration field order
│   └── themes/trailwise/login/   The Trailwise login theme (sunset page, rounded card, orange buttons)
│       ├── theme.properties          Builds on Keycloak's keycloak.v2 login theme and adds one stylesheet
│       └── resources/css/trailwise.css  The styles, using the same colors and font as the frontend
├── .dockerignore         Keeps secrets, virtual environments and logs out of the Docker build
├── .claude/              Claude Code project setup
│   ├── settings.json     Permissions, blocked commands and the .env write-protection hook
│   └── skills/
│       ├── add-agent-tool/
│       │   └── SKILL.md  Step-by-step workflow for adding an agent tool
│       └── push-to-github/
│           └── SKILL.md  Safe stage, commit and push workflow (approval at each step)
├── frontend/             React + Vite web app (holds no API keys: it only talks to the backend)
│   ├── Dockerfile        Image for the frontend: builds the app, then serves it with non-root nginx (port 8080)
│   ├── .dockerignore     Keeps node_modules, dist and .env files out of the frontend Docker build
│   ├── nginx.conf        Web server settings for the container: the image's default site plus a fallback to index.html so /app works on reload
│   ├── index.html        Vite entry page: an empty #root that src/main.jsx fills
│   ├── package.json      npm packages and scripts (dev, build, preview)
│   ├── package-lock.json Exact package versions (committed)
│   ├── vite.config.js    Vite settings: React plugin, dev server on port 5173 (the port the backend allows)
│   ├── .env.example      Non-secret settings (VITE_API_URL, the backend address)
│   ├── reference/        UI reference screenshots (local only, git-ignored); only its README.md is tracked
│   │   └── README.md     Says the images are design references only: do not copy or ship them
│   ├── public/
│   │   ├── favicon.svg   Tab icon
│   │   └── places/       Six local placeholder illustrations (goa, jaipur, kerala, varanasi, ladakh, hampi .svg) for the /app Home cards
│   └── src/
│       ├── main.jsx      Starting point: mounts the app into #root
│       ├── App.jsx       App shell: nav bar, current page, login pop-up, disclaimer; holds the chat state
│       ├── styles.css    All styles (warm sunset theme, light and dark mode)
│       ├── api/
│       │   ├── client.js     The only code that calls the backend (POST helper, ApiError, friendly network errors)
│       │   └── chat.js       sendMessage(): POST /chat and the 422, 502 and 503 messages
│       ├── data/
│       │   ├── places.js             The hardcoded list for the /app Home dashboard: six places (name, tagline, image path) and the quick-start chips; no timings or prices
│       │   └── mockReturningUser.js  MOCK returning-user data, marked TODO until the backend endpoints exist; switched off by default
│       ├── auth/
│       │   └── session.js    Keycloak sign-in (keycloak-js, token in memory only), guest session id, auth headers for API calls
│       ├── hooks/
│       │   ├── useAuth.js    Who is signed in, plus login and logout
│       │   ├── useChat.js    Conversation state: messages, pending reply, send
│       │   └── useVoice.js   Voice input with the browser's speech recognition (English, Hindi, Kannada)
│       ├── components/
│       │   ├── NavBar.jsx        Public top bar: logo, Destinations (scrolls to the section), Plan a Trip and Log in (both open the login pop-up); a menu button on phones
│       │   ├── HomeDashboard.jsx Home section of /app: greeting, quick-start chips, place cards or "Continue your trip" and "Recently explored"
│       │   ├── AuthModal.jsx     Log in pop-up (email, Continue, Continue as guest, and a "Sign up" link that goes straight to Keycloak's register page; closes with X, Escape or outside click)
│       │   ├── ChatWindow.jsx    The chat area: message list plus input
│       │   ├── MessageList.jsx   Scrolling conversation with a "Thinking..." bubble
│       │   ├── MessageBubble.jsx One message; bot replies rendered as safe Markdown
│       │   ├── ChatInput.jsx     Message box and Send button (Enter sends, Shift+Enter new line)
│       │   ├── VoiceButton.jsx   Language picker and microphone button, with a typing fallback
│       │   └── Disclaimer.jsx    "Verify before travel" notice, always visible
│       └── pages/
│           ├── Home.jsx      Landing page: sunset hero (serif headline, chat input, amber button) and a "Popular destinations" section of six illustrated cards
│           ├── AppHome.jsx   The app page (route /app), for guests and logged-in users: sidebar (Home, Chats, Saved, Explore), greeting, chat bar with mic and send
│           ├── Chat.jsx      The chat screen (conversation and message box)
│           ├── Explore.jsx   Placeholder: Explore needs backend pieces that are not built yet
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
│   │   ├── routes_tool.py get_route(points, mode): 2 to 10 [lat, lng] points, driving only, returns map points (no distances, times or prices), Redis cache for 6 hours
│   │   ├── geocode_tool.py geocode_places(names, context): up to 12 place names, Redis cache first, one request per second to a Nominatim-style service
│   │   └── email_tool.py send_trip_email(subject, body, confirmed): validates, then sends a plain-text trip email by SMTP to one fixed address
│   └── tests/
│       ├── test_weather.py  Weather tool tests (API and Redis faked) and a registration check
│       ├── test_web_search.py  Web search tool tests (Gemini and Redis faked) and a registration check
│       ├── test_train_tool.py  Train tool tests (RailRadar faked) and a registration check
│       ├── test_bus.py  Bus tool tests (Parse.bot faked) and a registration check
│       ├── test_email_tool.py  Email tool tests (SMTP faked) and a registration check
│       ├── test_routes.py  Route tool tests (HTTP and Redis faked, no waiting) and a registration check
│       ├── test_geocode.py  Geocode tool tests (HTTP and Redis faked, no waiting) and a registration check
│       └── test_no_key_in_logs.py  Regression test: the API key never appears in logs
└── backend/              FastAPI server
    ├── .venv/            Python virtual environment with installed packages (not committed)
    ├── Dockerfile        Image that runs the backend and its stdio MCP server (build context: project root)
    ├── requirements.txt  Python packages
    ├── pytest.ini        Test settings
    ├── app/
    │   ├── __init__.py   Marks app as a Python package
    │   ├── api/
    │   │   ├── chat.py      The POST /chat route (thin: validate, call the agent, return)
    │   │   └── me.py        GET /me: the signed-in user from the Keycloak token
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
    │   │   ├── audit.py     Audit log: JSON lines of agent activity, secrets and message text never written
    │   │   └── auth.py      Verifies Keycloak access tokens (PyJWT, keys cached in Redis) and reads guest session ids
    │   ├── models/
    │   │   └── chat.py      Request and response models for /chat
    │   └── main.py       Creates the app (lifespan: shared MongoDB client), defines /health and /ready
    └── tests/
        ├── conftest.py   Shared test setup (fake API key)
        ├── test_health.py  Smoke test for /health
        ├── test_agent_tools.py  Tests for the agent's tool loop (Gemini and MCP faked)
        ├── test_auth.py   Tests for the Keycloak token check (fake signing key, no Keycloak needed)
        ├── test_mongo.py  Tests for the shared MongoDB client, the startup ping and /ready (fake client, no Atlas)
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
| `docker-compose.yml` | Starts the local services: Redis (6379), Mailpit (SMTP 1025, web UI 8025), the backend (http://localhost:8000) and the frontend (http://localhost:5173, mapped to nginx's port 8080 inside the container). The frontend service builds from `./frontend` with the build argument `VITE_API_URL` (the backend address your browser calls, baked into the JavaScript) and starts after the backend. |
| `frontend/Dockerfile` | Builds the frontend image in two stages: `node:22-alpine` runs `npm ci` (exact versions from `package-lock.json`) and `npm run build`; then `nginxinc/nginx-unprivileged:1.27-alpine` (a non-root user, port 8080) serves only the built files (about 74 MB). It holds no secrets and no `node_modules`. For local Docker testing, not production. |
| `frontend/reference/`, `frontend/reference/README.md` | A folder for UI reference screenshots. `.gitignore` ignores everything in it except the README, which says the images are design references only and must not be copied or shipped. The app never imports from it. |
| `frontend/nginx.conf` | Site settings copied over the nginx image's default (`/etc/nginx/conf.d/default.conf`). Same server (port 8080, same root, index files and 50x page; the image sets no custom headers) with one change: `try_files $uri $uri/ /index.html`, so a path that is not a file, such as `/app`, loads the React app instead of a 404. The Vite dev server already does this. |
| `frontend/.dockerignore` | Keeps `node_modules`, `dist`, `.env` files (except `.env.example`), `.git` and logs out of the frontend build. |
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
| `frontend/src/pages/Home.jsx`, `Chat.jsx`, `Explore.jsx`, `SavedTrips.jsx`, `Login.jsx` | Home is the public landing page: the warm sunset hero (serif headline, chat input, amber button) and a "Popular destinations" section of six cards (Goa, Jaipur, Kerala backwaters, Varanasi, Ladakh, Hampi) whose pictures are small SVG illustrations drawn in the file (no photos, licences or outside requests; no prices or times); "Plan it" opens `/app` as a guest with the typed text pre-filled in the chat input; clicking a card opens the guest Chat with a first message. Chat is the conversation screen. Explore and SavedTrips are "coming soon" placeholders that the public site no longer links to (the file `Explore.jsx` and `SavedTrips.jsx` stay for later). `Login.jsx` is no longer used: the log-in pop-up replaced it. |
| `frontend/src/pages/AppHome.jsx`, routing in `App.jsx` | The app page at `/app`, open to **guests and logged-in users**. There is no router library: `App.jsx` reads `window.location.pathname` and moves with the History API (Back and Forward work). `/` is the public site. "Plan a Trip" in the header and "Plan it" in the hero go straight to `/app` as a guest, with no login pop-up; any text typed in the hero search box is pre-filled in the chat input there (held in React state only, never sent automatically, nothing in browser storage). A guest sends the random `X-Session-Id` with chat calls, as before. Inside `/app` the sidebar's **Home** is a dashboard and **Chats** is the chat (see `HomeDashboard.jsx` below). A guest sees "Browsing as a guest" and a **Log in** button (it opens the same login pop-up); a logged-in user sees "Hi, name" and **Log out**. A guest who opens Saved sees "Log in to save your trips". The header's "Log in" still opens the login pop-up. Keycloak login and sign-up return to `/app` (`redirectUri` in `auth/session.js`; the client's allowed redirect `http://localhost:5173/*` covers it, so Keycloak is unchanged). A logged-in user who opens `/` still sees the home page with a "Go to your app" button. `AppHome` has a sidebar with only Home, Chats, Saved and Explore (a bottom tab bar on phones), a time-of-day greeting (with the first name when logged in), and a chat bar (text box, language picker, mic via `useVoice`, round send button) pinned to the bottom. Chats shows the shared conversation (the same chat as the rest of the app) and the chat bar. Saved and Explore are "coming soon" panels. Layout idea from `frontend/reference/app-home.png` only; the look is the Trailwise sunset style. |
| `frontend/src/components/HomeDashboard.jsx`, `frontend/src/data/places.js`, `frontend/src/data/mockReturningUser.js`, `frontend/public/places/*.svg` | The Home section of `/app`, built so it is different from Chat (it has no chat bar). It shows a greeting, a row of quick-start chips (Weekend getaway, Beach trip, Mountain trek) and then one of two views. **Guest or new user:** "Start your first trip" with six place cards (picture, title, short tagline) from `places.js`. **Returning user:** a "Continue your trip to <last trip name>" card with a Continue button, and a "Recently explored" row of places below it. Clicking a card, chip or Continue opens Chats with a message pre-filled in the chat input and not sent, for example "Plan a 3-day trip to Goa". No timings or prices appear on any card. The pictures are local placeholder SVG illustrations in `public/places/` (swap in real photos by changing the paths in `places.js`). The returning-user data is a **mock** in `mockReturningUser.js`, marked TODO until backend endpoints exist, and it is **off by default** (`USE_MOCK_RETURNING_USER = false`), so every visitor currently sees "Start your first trip". Set it to `true` to try the returning view as a logged-in user. |
| `frontend/src/components/NavBar.jsx`, `AuthModal.jsx` | The public nav bar (Trailwise logo on the left; Destinations, which scrolls to the destinations section; Plan a Trip and Log in, where Plan a Trip goes straight to `/app` as a guest (no pop-up) and Log in opens the Log in pop-up; a menu button on narrow screens; Chat, My Trips, Explore and Sign up are no longer in it) and the Log in pop-up (it asks for an email and checks it looks valid, then Continue sends the user to Keycloak's login page with the email as a hint; "Continue as guest" closes it; closes with the X, Escape or a click outside; there is no sign-up form here). "Sign up" (only the link inside the Log in pop-up, "New to Trailwise? Sign up"; the nav bar has just "Log in") calls keycloak-js `keycloak.register({ redirectUri: window.location.origin })`, which goes straight to Keycloak's register page (first name, last name, email, password, confirm; the email is verified by mail) and returns to the app's home page afterwards. UI only so far: nothing is sent or stored; Keycloak sign-in is wired in next. Layout and style ideas came from reference screenshots in `frontend/reference/`; no third-party logo, photo or wording is used. |
| `backend/` | The FastAPI server. The agent and its tools will be added here. |
| `backend/.venv/` | The Python virtual environment holding the installed packages. Ignored by git. |
| `backend/requirements.txt` | The Python packages the backend needs. |
| `backend/pytest.ini` | Settings for pytest (where tests are, async mode). |
| `backend/app/config.py` | Settings loaded from the environment. The app won't start without the Gemini API key or `MONGODB_URI` (the Atlas connection string, with no default; `MONGO_URL` is accepted as a fallback name). A missing or empty `MONGODB_URI`, or one that does not start with `mongodb+srv://` or `mongodb://`, stops startup with a one-line error that never shows the value. |
| `backend/app/agent/agent.py` | `run_agent(message)`: loads the system prompt, calls Gemini asynchronously and returns the reply. Raises `AgentError` if Gemini fails or returns nothing. A Gemini call that fails with a temporary server error (500, 503, 504) is retried up to 2 times (pauses of 2 s and 5 s); 429, 401, 403, 400 and timeouts are not retried. Each call times out after 20 s. If `GEMINI_FALLBACK_MODEL` is set (and differs from `GEMINI_MODEL`) and the main model still fails with 500, 503 or 504 after its retries, the request switches to the fallback model (with the same retries) and stays on it for its remaining tool rounds; 4xx errors and timeouts never trigger the switch, and each request starts on the main model again. If Gemini is still overloaded (503) after all that it raises `AgentBusyError`. The HTTP status code of a failure is written to the audit log (`http_status`, plus a `model_retry` event for each retry). No modes, tools or history yet. |
| `backend/app/agent/prompts/system.md` | The system prompt: Trailwise's role, scope (planning only, no booking), honesty rules (transport is indicative only) and response guidelines. |
| `backend/app/agent/prompts/explore.md`, `route.md`, `itinerary.md`, `email.md` | The four mode prompts from `plan.md`, each with a prompt ID and version (for example `trailwise.route`, version 1). Each adds to `system.md` and never weakens it. Explore: 3 to 6 suggestions, indicative transport only. Route: `search_buses` with a `YYYY-MM-DD` date, `get_train_details` only with a train number, no train seat claims, qualitative advice for other transport, always "Verify before travel". Itinerary: 1 to 14 days, Morning, Afternoon and Evening blocks, no clock times or prices, optional JSON output. Email: a draft only, sent only on explicit request to the verified address after confirmation. They use only tools that exist. Not loaded by any code yet: nothing selects a mode until `modes.py` is built. |
| `backend/app/agent/mcp_client.py` | `McpClient` (async context manager): launches `mcp_server/server.py` with the server's own `.venv`, then `list_tools()` and `call_tool(name, args)`. Calls return a dict; failures become an error object. Used by `agent.py` for tool discovery and calls. |
| `backend/tests/test_mcp_client.py` | Starts the real server: checks connection, discovery of `get_weather`, and error objects for a bad argument and an unknown tool (no weather API call). |
| `backend/tests/test_agent_tools.py` | Tool loop tests with fake Gemini and MCP: tool discovered, called and result used; unknown tool refused; tool error passed on; 5-round cap; fallback when MCP is down or discovery fails; empty reply. |
| `backend/app/api/chat.py` | `POST /chat`: takes a message, calls `run_agent`, returns `{reply}`. An agent failure becomes a generic 502; an overloaded Gemini (HTTP 503 after retries) becomes a 503 "busy, try again in a minute" with a `Retry-After` header. |
| `backend/app/core/auth.py`, `backend/app/api/me.py` | Authentication. Keycloak signs users in; this code only verifies the access token it issues. `verify_token` accepts RS256 only and checks signature, issuer (`KEYCLOAK_URL` + realm), audience (`trailwise-api`, added by a mapper in the realm file), expiry and subject, using Keycloak's public keys (fetched with async `httpx`, cached in Redis 10 minutes; if Redis is down it fetches directly). `get_optional_principal` gives a user (valid `Authorization: Bearer`), a guest (valid `X-Session-Id`, 22 to 64 URL-safe characters) or nothing; a bad token is a 401 and is never downgraded to a guest. `POST /chat` uses it (anonymous still allowed for now; chat history is not built). `GET /me` needs a user. Settings: `KEYCLOAK_URL`, `KEYCLOAK_INTERNAL_URL` (Docker: `http://keycloak:8080`), `KEYCLOAK_REALM`, `KEYCLOAK_AUDIENCE`. No users collection yet: it comes with saved trips. |
| `backend/tests/test_auth.py` | Token tests with a throwaway RSA key: valid token, no token, guest on `/me`, expired, wrong issuer, wrong audience, missing claims, unknown key, HS256 and `none` algorithms, malformed header, Keycloak down, and `/chat` with bad token, guest, anonymous and bad session id. |
| `keycloak/themes/trailwise/login/theme.properties`, `resources/css/trailwise.css` | The Trailwise login theme, mounted into the Keycloak container at `/opt/keycloak/themes` (read-only). It extends Keycloak's own `keycloak.v2` theme (all pages and scripts keep working) and only adds CSS: the sunset gradient with a low sun, the system font stack, a rounded white card, rounded inputs and orange pill buttons, copied from `frontend/src/styles.css`. The page is always light. Dev mode re-reads theme files, so edits show on reload. To use it on an existing realm: admin console > Realm settings > Themes > Login theme > `trailwise` > Save (new installs get it from the realm file). |
| `keycloak/trailwise-realm.json` | The Keycloak realm imported on first start (`start-dev --import-realm`): self-registration with email as username, email verification (mail goes to Mailpit), password reset, one public client `trailwise-web` (Authorization Code with PKCE, redirect only to `http://localhost:5173/*`) and an audience mapper that adds `trailwise-api` to access tokens. No secrets in it. Admin console: http://localhost:8080 (password from `KEYCLOAK_ADMIN_PASSWORD` in `.env`). |
| `frontend/src/auth/session.js`, `frontend/src/hooks/useAuth.js` | Browser side of login. `keycloak-js` does a quiet `check-sso`, then the Log in pop-up sends the user to Keycloak (the typed email is only a login hint; the password is never typed in this app). The token is kept in memory only, never in localStorage. API calls send `Authorization: Bearer` for a user or `X-Session-Id` (random, kept in `sessionStorage`) for a guest. If Keycloak is unreachable the site still works as a guest. |
| `backend/app/models/chat.py` | `ChatRequest` (message trimmed, 1 to `max_message_chars` characters) and `ChatResponse` (reply). |
| `backend/app/main.py` | Creates the FastAPI app with a lifespan: at startup it creates the one shared MongoDB client and pings it once (a failed ping only logs a warning, the app still starts), at shutdown it closes the client. `/health` says the server is up; `/ready` pings MongoDB and returns 503 ("Not ready") if it does not answer within 5 seconds. Redis is not checked yet. |
| `backend/app/core/mongo.py` | The shared MongoDB client. `create_client(settings)` builds PyMongo's `AsyncMongoClient` from `MONGODB_URI` (Atlas `mongodb+srv://` links work: `pymongo[srv]` adds the DNS support) with a 5 second server timeout and app name `trailwise`; it connects lazily. `ping(client)` returns True or False and never raises. `get_db(request)` returns `client[settings.mongo_db]`, for use as a FastAPI dependency. The connection string holds a password, so nothing here logs it. No indexes yet (they come with chat history). |
| `backend/tests/conftest.py`, `backend/tests/test_mongo.py` | `conftest.py` gives every test a fake MongoDB client and sets `MONGODB_URI` to a dead local port, so tests never reach Atlas even though the settings read your real `.env`. `test_mongo.py` checks `/ready` (ok, ping fails, ping hangs), the startup warning (and that it never prints the URL), that the client is created once and closed at shutdown (even if the startup ping failed), that `create_client` uses the settings URL, and that `get_db` uses `settings.mongo_db`. |
| `mcp_server/` | Separate MCP tool server. Run from `mcp_server/` with `python server.py` (stdio) and test with `pytest`. Not yet connected to the Gemini agent. |
| `mcp_server/tools/weather.py` | `get_weather(location)`: arguments validated, Redis cache first (20 min TTL, skipped if Redis is down), then OpenWeatherMap with a 10 s timeout. Returns structured fields or an error object, never raises. |
| `mcp_server/tools/web_search.py` | `web_search(query)`: query validated (3 to 200 characters), Redis cache first (1 hour TTL, skipped if Redis is down), then one Gemini call with only Google Search grounding and a 20 s timeout. Returns a capped `answer`, up to 5 `sources` (title and URL) and the `queries` run, or an error object, never raises. Uses `GEMINI_SEARCH_MODEL` (default `gemini-3.5-flash`). Grounding may need a paid quota: on the free tier it can return 429, which becomes `unavailable`. Not a source for transport timings or prices. |
| `mcp_server/tools/train_tool.py` | `get_train_details(train_number)`: number validated (1 to 10 letters, digits or hyphens), then one RailRadar call (`GET https://api.railradar.in/v1/trains/{number}`, key in the `Authorization: Bearer` header, 10 s timeout). Returns name, type, route ends, run days, classes, a bookable flag and up to 60 scheduled stops, or an error object (`invalid_arguments`, `not_configured`, `train_not_found`, `unavailable`), never raises. Scheduled data only: RailRadar has no seat availability API, so no seat counts or fares. Every result carries a "verify before travel" notice. No cache yet. Needs `RAILRADAR_API_KEY` in the root `.env` (free plan: 100 requests a month). |
| `mcp_server/tests/test_train_tool.py` | Fake RailRadar: shaped result, header-only key, no seat or fare fields, bad arguments, 404, 500, 429 and timeout, empty body, stop cap, missing key, and registration in the server. |
| `mcp_server/tools/bus.py` | `search_buses(source_city, destination_city, journey_date)`: city names (1 to 60 characters) and a `YYYY-MM-DD` date (today up to 120 days ahead, India time) are validated, then 3 Parse.bot calls on the redBus scraper `35a9b6fe-...` pinned to snapshot version 11 (header `API-Snapshot-Version`): `get_city_suggestions` for each city (in parallel), then `search_buses`. The key goes in the `X-API-Key` header, never the URL. Returns up to 20 buses with operator, bus type, departure and arrival times, duration, boarding and dropping points, fares in INR, available and total seats, AC and sleeper flags and rating (fields the source did not return stay empty), or an error object (`invalid_arguments`, `not_configured`, `city_not_found`, `no_buses_found`, `rate_limited`, `unavailable`), never raises. Third-party scraped data, with a "verify before travel" notice; it cannot book. No cache yet. Needs `PARSEBOT_API_KEY` in the root `.env`; the plan allows about 100 calls a day, so about 33 searches. |
| `mcp_server/tests/test_bus.py` | Fake Parse.bot: shaped result, city (not airport) chosen, key and snapshot header only in headers, bad cities and dates (past, too far ahead, same city), unknown city, no buses, 429 on either call, 500, timeout, error envelope, cap of 20, missing key, and registration in the server. |
| `mcp_server/tools/email_tool.py` | `send_trip_email(subject, body, confirmed)`: sends a plain-text trip email over async SMTP (`aiosmtplib`, 15 s timeout). There is no recipient argument: it can only send to one address from the root `.env`: `VERIFIED_RECIPIENT`, or the `SMTP_EMAIL` account itself when that is not set (a stand-in for the user's verified address until login exists), so a prompt injection cannot redirect it. Subject 1 to 150 characters on one line (blocks header injection), body 1 to 5000 characters, `confirmed` must be a real `true` (a prompt-level safeguard backing the rule "show the draft and ask first"). Sender and recipient are checked with `EmailStr`. The tool adds a "Verify before travel" footer itself. Returns the masked address on success, or an error object (`invalid_arguments`, `not_confirmed`, `not_configured`, `unavailable`), never raises. The SMTP password, body and full address are never logged. Settings from `.env`: `SMTP_EMAIL` (the account: it is the From address and the login name) and `SMTP_PASSWORD` (the names in `.env.example`), optional `VERIFIED_RECIPIENT`, and the server settings `SMTP_HOST` (default `localhost`), `SMTP_PORT` (1025) and `SMTP_STARTTLS` (false). The server defaults match Mailpit in `docker-compose.yml` (no password, no login), so local tests send nothing outside your machine; for a real provider set the host, port 587 and `SMTP_STARTTLS=true`. The tool logs in only when `SMTP_PASSWORD` is set. Not built yet: authentication and a rate limit (a Redis counter is needed because the MCP server starts a new process per request). Inside the Docker container the subprocess reads `SMTP_HOST` from `.env`, so use `mailpit` there. |
| `mcp_server/tools/geocode_tool.py` | `geocode_places(names, context)`: finds map coordinates for up to **12** place names in India (more is rejected with `invalid_arguments`, not cut) with a context such as "Uttarakhand, India". Names are 1 to 100 characters (letters, marks and digits in any script, and `, . ' - ( ) / &`), the context up to 100. For each distinct name it checks **Redis first** (key = hash of the lower-cased name and context); on a miss it waits for the shared slot and calls `GET {GEOCODE_BASE_URL}/search` with `q` = name plus context, `format=jsonv2`, `limit=1` and `countrycodes=in`, sending `User-Agent` from `GEOCODE_USER_AGENT`, using an `httpx.AsyncClient` with a 10 s timeout and up to 2 retries (timeout, network error, 429, 5xx; a 4xx is not retried). Names are looked up **one at a time**, at most **1 request per second across every process**: the slot is a Redis key set with NX and a 1 s expiry (the MCP server starts a new process per request, so an in-process timer alone could not enforce it; if Redis is down it falls back to an in-process timer). Only `lat` and `lon` are read from the first result (strings, converted to numbers, rejected if not finite or out of range); no third-party text is returned, and coordinates are never invented. Returns `{"ok": true, "places": [{name, lat, lng, found}], "source": "OpenStreetMap Nominatim"}`, one entry per name in order. No match: `found: false` with null coordinates. If the service failed after the retries the entry also has `"error": "unavailable"` (a failed lookup says nothing about whether the place exists; the route and itinerary prompts tell the agent to say retry later and never claim the place does not exist). **Only successful matches are cached**, for a fixed 30 days (a constant `CACHE_TTL_SECONDS` in the code, no environment variable); no-matches and failures are not. There is no distance limit between places. Settings from the root `.env` (read through `mcp_server/config.py`): `GEOCODE_BASE_URL` and `GEOCODE_USER_AGENT` (the User-Agent must identify the app and give a real contact, as Nominatim's usage policy asks; empty means "not configured"). Logs contain only status codes, never place names, the User-Agent or the URL. The tool never raises. Note: there is no `mcp_server` service in `docker-compose.yml` (the backend container runs the MCP server as a subprocess that gets no environment variables from compose), so these two settings reach the tool only through the mounted `.env`, like the other tools' keys. |
| `mcp_server/tools/routes_tool.py` | `get_route(points, mode)`: the **driving** route through 2 to 10 `[lat, lng]` points, for drawing on a map. `mode` must be exactly `"driving"`. Each point must be two real, finite numbers in range (strings, bools and `None` are refused); points are rounded to 5 decimals. **Redis first** (key = hash of the rounded points in order, so a different order is a different route); on a miss it calls `GET {ROUTING_BASE_URL}/route/v1/driving/{lon,lat;lon,lat}` with `overview=simplified` and `geometries=geojson` through an `httpx.AsyncClient` with a 10 s timeout and **one retry after 1 s** (timeout, network error, 429 or 5xx; a 4xx is not retried). Only the route's coordinates are read: they are swapped from `[lon, lat]` to `[lat, lng]`, validated, and thinned to at most **300 points** (the first and last kept, the rest evenly spaced real points, none invented). The reply's distance, duration, legs, steps and names are **never read or returned**, so no durations, distances or prices reach the model. Returns `{"ok": true, "found": true, "route": [[lat, lng], ...], "source": "OpenStreetMap OSRM", "notice": "Indicative route for the map only. Verify before travel."}`. Any failure returns `{"ok": true, "found": false}`: plain when the service says there is no route (`NoRoute`, `NoSegment`), and with `"error": "unavailable"` when the service itself failed (timeout, 5xx, other 4xx, unusable reply), because that says nothing about whether a road route exists (the prompts tell the agent to say retry later and never claim there is no road route). Bad arguments return `{"ok": false, "error": "invalid_arguments"}`; a missing address returns `not_configured`. **Only successful routes are cached**, for a fixed 6 hours (a constant `CACHE_TTL_SECONDS` in the code, no environment variable); failures and no-route answers are not. The small cache helper is inside this file (a TODO notes that `geocode_tool.py` and this file should share one helper later). Setting from the root `.env`: `ROUTING_BASE_URL` (the public OSRM demo server `https://router.project-osrm.org` is for light use only; use your own or a hosted OSRM for real traffic). Logs contain only status codes, never coordinates or the address. The tool never raises. As with the geocode tool, there is no `mcp_server` service in `docker-compose.yml` and compose variables do not reach the stdio subprocess, so `ROUTING_BASE_URL` reaches the tool only through the mounted `.env`. |
| `mcp_server/tests/test_routes.py` | 84 tests with the HTTP call, Redis and the waiting faked: the exact request (URL with lon,lat pairs, `overview`, `geometries`), lon/lat swapped to lat/lng, no distance, duration or other API fields in the result, 5-decimal rounding sharing one cache entry, thinning (300 kept whole, 301 and 1000 thinned, first and last kept, evenly spaced, only real points), the 6-hour cache TTL and cache hits (order matters, damaged entries are misses, failures and no-route never cached), failures (one retry after 1 s, 429, 5xx, 4xx not retried, plain no-route versus unavailable, many bad reply shapes, never raises), bad arguments (1 and 11 points, ranges, strings, bools, NaN, wrong mode), not configured, Redis trouble, logs free of coordinates and the address, and registration in `server.py`. |
| `mcp_server/tests/test_geocode.py` | 53 tests with the HTTP call, Redis and the waiting faked: request details (URL, `q`, `jsonv2`, `limit`, `countrycodes`, header), string coordinates converted to numbers, no third-party text returned, duplicates looked up once, success, no match (not cached), 30-day cache TTL and cache hits (case and spacing ignored, different context separate, damaged entry is a miss), the 12-name cap (13 rejected, 12 accepted one at a time), bad arguments, not configured, failures (retries, 429, 4xx not retried, one failure does not spoil the rest, bad replies never produce coordinates), the 1-second slot and Redis trouble, logs free of place names, the User-Agent and the URL, and registration in `server.py`. |
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

Mailpit's web UI is at http://localhost:8025. Docker is not needed for `/chat`. The backend needs MongoDB Atlas: `MONGODB_URI` is **required**, and the app stops at startup with a clear error if it is missing, empty or does not start with `mongodb+srv://` or `mongodb://` (the value is never printed, because it holds a password). Put your Atlas connection string in `MONGODB_URI` in the project-root `.env` (see `.env.example`) and allow your IP in Atlas under Network Access. There is no local MongoDB container any more. Chat history and saved trips will use it later.

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

The API is then at http://localhost:8000. Stop it with `docker compose stop backend`.

Run the chat page in Docker too (the React app is built once and served by nginx at http://localhost:5173; `docker compose up -d backend frontend` starts both):

```
docker compose build frontend
docker compose up -d frontend
```

Do not run `npm run dev` at the same time: both use port 5173. After changing the frontend, rebuild with `docker compose build frontend` and `docker compose up -d frontend`. Stop it with `docker compose stop frontend`.
