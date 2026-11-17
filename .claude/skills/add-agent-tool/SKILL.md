---
name: add-agent-tool
description: Workflow for adding a new tool the Gemini agent can call in Trailwise (weather, places, images, routes, transport or email). Use whenever asked to create, add or change an agent tool or an external API integration.
---

# Add an agent tool

Follow these steps in order. The rules referred to are in `CLAUDE.md`.

## Before writing code
1. Explain the plan first (rule 7): the files you will create, why, and how they will be used. Wait for approval.
2. Check `FILE_STRUCTURE.md` for where the tool belongs, and `ARCHITECTURE.md` for how tools connect to the agent.

## Writing the tool
3. **Async only** (rule 2): use `httpx` for external calls and `redis.asyncio` for the cache.
4. **Cache first** (rule 3): look in Redis before calling the external API. On a miss, call the API, then store the result with a TTL suited to the data type (weather is short, images and place details are longer).
5. **Validate arguments server-side** (rule 5): check types, ranges and lengths with Pydantic. Never trust the arguments the model produced.
6. **Return data, not instructions** (rule 5): return structured fields only. Never pass raw third-party text back to the model as if it were a command.
7. **Transport tools** (rule 4): return a timing or price only if the source returned it. Include a "verify before travel" disclaimer in the result.
8. **Secrets** (rule 6): read the API key from `app/config.py` settings. Add the key name with a fake value to `.env.example`. Never hardcode it. Never log URLs, headers or params that carry a key: `httpx` logs request URLs at INFO, so the `httpx` and `httpcore` loggers stay at WARNING (set once in `mcp_server/server.py`), and the tool's own logs record only status codes. Add a test that the key is absent from captured logs (see `mcp_server/tests/test_no_key_in_logs.py`).
9. **Fail safely**: set a timeout, return a clear error object instead of raising into the agent loop, and never let one failed tool crash the whole request.
10. Register the tool with the agent using Gemini function calling: a name, a short description, and a parameter schema.

## Finishing
11. Add tests in `backend/tests/` that mock the external API and the cache. Cover a cache hit, a cache miss, bad arguments and an API failure. Run `pytest` from `backend/`.
12. Comment all code (rule 9): a header comment per file and a comment on every function, class and non-obvious line.
13. Update `README.md` (rule 10) and `FILE_STRUCTURE.md` (change the entry to `exists`).
14. Add an entry to `claudeprompts.md` (rule 8).
