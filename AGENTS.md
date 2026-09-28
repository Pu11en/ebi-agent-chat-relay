# ccdb — project instructions for every coding agent

This repository is the reusable Discord agent relay, not a personal bot config.
Keep personal credentials, channel IDs and workflows out of the package; personal
add-ons belong in external custom cogs. Preserve the public API and sensible,
backward-compatible defaults. Consumers must not copy or subclass cogs to update.

## Small, checked changes

- Python 3.12+, discord.py 2, asyncio, SQLite repositories; typed functions and
  `from __future__ import annotations`. Use ruff, 100-character lines.
- Reproduce each bug with a failing test first, make the smallest fix, then rerun it.
- Fast focused check: `make test-one f=tests/test_example.py`.
- Before committing: `make verify` (format, lint, pyright, parallel full suite).
  Do not run the whole suite serially; it can exceed tool timeouts.
- Tests must not depend on the live bot's environment; use the autouse environment
  fixture in `tests/conftest.py` and set only test-specific values explicitly.
- Preserve unrelated edits. Work in an isolated worktree for concurrent changes;
  put temporary copies outside the user's project root.
- Before changing shared files or restarting, check the lounge and `/api/sessions`.
  Separate folders do not guarantee separate runtime dependencies.

## Architecture and safety

- A Discord thread is a session. Native session IDs only resume in the same route;
  changing providers requires an explicit bounded transcript handoff.
- Use existing `BackendFactory`, `RunConfig` and `cogs/_run_helper.py`; do not copy
  streaming, session or file-delivery logic into new features.
- CLI subprocesses perform agent work; the REST API is the explicit control plane.
  Do not add hidden stdout markers or model calls to coordinate the bot.
- Always use argv-based subprocesses, never `shell=True`. Prompts go through stdin
  or after `--`; validate session IDs and skill names. Strip relay credentials from
  child environments and never log secrets. Provider credentials stay per process.
- No bypass-permissions default in the reusable library; the instance may opt in.
- Run `.agents/skills/security-audit/SKILL.md` before commits changing a runner,
  run helper, cog or environment boundary. Report failures; don't hide exceptions.
- Keep optional privacy/local/Teams behavior compatible; do not activate it for
  every user or delete it merely because one instance doesn't use it.
- Model catalogs are discovered where supported; failures fall back without
  breaking commands. Do not hardcode every future model or add paid discovery calls.
- Keep project outputs, plans and durable decisions in this project, not global
  preferences. Record fixes and verification so a fresh session can continue.

## Where to look, only when needed

- `.agents/skills/`: TDD, verification, Python, tests, security, cogs and release help.
- `docs/development-reference.md`: inherited detailed architecture and rationale;
  examples there may refer to the original operator, not this machine.
- `scripts/pre-start.sh` and `.github/workflows/`: inspect before any deployment.
  Do not assume `make dev-on` targets the correct service on this host.
- `docs/harness-round-1-review.md`: current harness-reset scope and checks.
- `.planning/2026-09-28-agent-os-reset/` in the main checkout: full local audit notes.

## Release

Use small local commits. Normally develop on a branch and use a PR; the current
owner's explicit release instructions take precedence. Never publish unrelated
files or secrets. A release requires checked integration, a coordinated restart,
live health verification and an honest distinction between configured and tested.
