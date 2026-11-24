# Prompt versioning

The agent's prompts are five Markdown files in `backend/app/agent/prompts/`. Git is the version history: there is **one file per prompt and no versioned file names** (never `route_v2.md`). The version lives in the file's first line.

## The five prompts

| File | Prompt ID | Used |
|---|---|---|
| `system.md` | `trailwise.system` | in every request, first |
| `explore.md` | `trailwise.explore` | Explore mode, after system.md |
| `route.md` | `trailwise.route` | Route mode, after system.md |
| `itinerary.md` | `trailwise.itinerary` | Itinerary mode, after system.md |
| `email.md` | `trailwise.email` | Email mode, after system.md |

## Heading format

The first line of each file must be exactly:

```
# Trailwise <name> (prompt ID: trailwise.<id>, version N)
```

For example `# Trailwise Route mode (prompt ID: trailwise.route, version 2)`. The ID must match the file name, and N is a whole number from 1. The backend checks all five headings when it starts (`validate_prompts()` in `app/agent/modes.py`): a bad heading stops it from starting, and the tests check the same.

## Rules

1. **Bump N in the heading with every edit to that file**, however small.
2. **Commit with a message that says what changed and why**, for example `route prompt v2: never quote distances from get_route (the tool has none)`.
3. **Tag releases as `prompt-<id>-v<N>`**, for example `prompt-trailwise.route-v2`, on the commit that made version N.
4. **Rebuild the backend after any prompt edit**: `docker compose build backend`, then `docker compose up -d backend`. The prompts are copied into the image and read once, so a running container keeps the old text.
5. **Check after changes.** There is no automated eval suite yet (planned: `evals/`, see `claudeprompts.md`). For now run `pytest` in `backend/`, then try a few real messages in each mode you changed and read the replies, including a transport question and a request that needs a tool.
6. **Never use `git reset --hard` or a force push** to undo a prompt change. Use the rollback steps below.

## The saved-preferences line (not a prompt file)

When the browser sends saved preferences with a chat message, the agent adds ONE short line after the fixed prompt in the same `instructions`: `[App preferences, validated values] Reply in Hindi, ... Budget style: comfort (a style, not a price). Travel mode: train or bus. ...`. It is built in code (`preferences_line` in `agent/modes.py`) from a fixed template and only the allowed values (reply language en, hi, kn; budget style; travel mode), never from free text, so it needs no version and editing it is a code change, not a prompt edit. It comes AFTER `system.md` and the mode file, so the tools and the fixed text stay the identical prefix that prompt caching reuses. `prompt_hash` and `prompt_versions` are of the fixed text only; the audit line `request_received` adds `preference_fields` (the names of the fields set, never values). Without preferences the request is exactly what it was before. The saved reply language overrides `system.md`'s "Reply in the language the user writes in" unless the user asks for another language.

## What the audit log records

Each `request_received` and `model_decision` line in `backend/logs/audit.log` has:

- `mode`: the mode picked for the message.
- `prompt_versions`: the label of each part, for example `["trailwise.system@3", "trailwise.route@1"]`. Written by people, so it is only as accurate as rule 1.
- `prompt_hash`: 12 characters of SHA-256 of the exact combined text sent to the model. If the text changed but the labels did not, someone forgot to bump N.

## Roll back one file

1. Find the commit to go back to: `git log --oneline -- backend/app/agent/prompts/<file>.md`
2. Restore only that file: `git restore --source=<commit> -- backend/app/agent/prompts/<file>.md`
3. Check the old prompt still:
   - matches the **current tools** (it must not mention a tool that no longer exists, and must not miss rules for a tool that was added since), and
   - keeps the **transport rules and the verify-before-travel reminder** (project rule 4).
4. The restore is itself an edit (rule 1): set N to one higher than the highest version ever used for that file, so a label never repeats for different text.
5. Rebuild the backend, run `pytest`, try the mode by hand (rules 4 and 5), then commit with a message saying which version you restored and why.
