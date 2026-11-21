---
name: push-to-github
description: Safe workflow for staging, committing and pushing Trailwise changes to GitHub, with checks and explicit user approval before anything is staged, committed or pushed. Use whenever asked to commit, push, ship or publish changes to GitHub. Pull requests only when requested.
---

# Push to GitHub

Follow these steps in order. The "Git Safety Rules" in `CLAUDE.md` always apply; if a step here conflicts with them, the rules win. Nothing is staged, committed or pushed until the user has approved it in this conversation.

## 1. Inspect (read-only)
1. Run `git status` and `git diff` (plus `git diff --staged` if anything is staged) and read the changes.
2. Run `git branch --show-current` and `git remote -v`.
3. Verify the remote: `origin` is the default. Show the user the `origin` URL and the current branch, and check the URL looks like the expected Trailwise repository. If `origin` is missing or looks wrong, stop and ask. Use another remote only if the user names it.
4. If the current branch is `main`, do not commit there. Propose a new branch name and ask before creating it. Pushing to `main` needs separate, explicit approval.

## 2. Check for problems
5. Look for secrets in the changes: `.env` files, API keys (OpenAI, OpenWeather, Pexels), tokens, passwords, audit logs (`logs/`, `*.log`) and virtual environments. Never stage them. Only `.env.example` (fake values) is allowed. Do not print secret values in the conversation; name the file and line only.
6. Run `pytest` in `backend/` and in `mcp_server/` (activate each venv first). If anything fails, stop and report the failures with the output. Do not push failing code unless the user explicitly says to.
7. Check that `README.md` and `FILE_STRUCTURE.md` are updated for any new file or folder (rule 10).

## 3. Get approval to stage and commit
8. Show the user: the branch, the remote URL, the exact list of files to stage, a one-line summary of each change, the test result and the proposed commit message.
9. Wait for explicit approval. A request like "push" is not approval of the specific file list; confirm it.

## 4. Stage and commit (after approval)
10. Stage only the approved files by path: `git add <file> <file>`. Never `git add -A`, `git add .` or `git commit -a`.
11. Run `git diff --staged --stat` and confirm it matches the approved list.
12. Commit with a short, imperative subject (under about 70 characters), a body explaining why if needed, and the attribution line from the session's system reminder at the end. Create a new commit; do not amend.
13. If a hook fails, fix the cause and make a new commit. Never use `--no-verify`.

## 5. Push (after approval)
14. Ask for approval to push, naming the remote and branch (for example "push `feature/x` to `origin`?"). Approval to commit is not approval to push.
15. Run a plain `git push -u origin <branch>`. Never `--force` or `--force-with-lease`. If the push is rejected, stop and report; do not force it. Pull or rebase only if the user asks.
16. Report the result: branch, remote, commit hash and the push outcome.

## 6. Pull request (optional)
17. Create a PR only when the user asks. Use `gh pr create` with a clear title and a body that summarizes the changes and the tests run, ending with the PR attribution line from the session's system reminder. Show the title and body and get approval first. Return the PR URL.

## 7. Log it
18. Add an entry to `claudeprompts.md` (rule 8) describing what was committed and pushed.
