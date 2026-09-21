# HANDOFF: finish every open build and ship ccdb v4.1.0

Written 2026-09-21 on Drew's Lenovo (the DrewAI bot machine) for a **different computer
with a different Claude subscription** (David's computer). That computer does all the
remaining build work. The Lenovo only pulls the finished result at the end.

Read this whole file before doing anything. Then work through
`docs/plans/v4.1.0-finish-all-builds.plan.md` top to bottom.

## 1. What Drew wants (his words, condensed)

- Everything is in this repo now. Clone it, build **everything** that is planned but not
  finished, test it all, and ship it as **one new version: v4.1.0** (current: 4.0.26).
- He wants to be **hands-off**. Do not stop to ask him things you can decide from the
  plans. Only stop for something truly blocking (see section 6).
- "Build it out all the way" means: every open task in the plans listed in section 4 is
  either implemented with passing tests, or explicitly recorded as dropped with a reason.
- When done: push the release branch to GitHub and open a PR. The Lenovo then switches
  its live bot to that version (section 8).
- Don't use this computer's Claude for the Lenovo, and don't use Codex/DeepSeek paid
  calls at all. Build with Claude only.

## 2. Where to start (exact git state)

```bash
git clone https://github.com/Pu11en/ebi-agent-chat-relay.git ccdb-v410
cd ccdb-v410
git checkout handoff/v4.1.0-base
git checkout -b release/v4.1.0
uv sync --dev
```

`handoff/v4.1.0-base` = the code Drew's live bot runs right now
(`session/1550757693784989707`) merged with the unfinished Go Work upgrade line
(`gowork/gowork-upgrade-execution-plan-20260921-022548`). That merge had no conflicts.

**Do not start from `main` on GitHub.** GitHub `main` (9d9c336) is ~170 commits behind.
Drew's local `main` was pushed separately as `save/local-main-2026-09-21` — it has a few
features the base lacks; task B1 merges them in.

## 3. How to test (the baseline is green — keep it green)

The test suite reads Discord/ccdb settings from the environment. If the shell running
the tests belongs to a ccdb bot session (it will, on David's computer), those variables
break a few tests. **Always run tests with every `DISCORD_*` and `CCDB_*` variable removed:**

```bash
scripts/test-clean-env.sh            # added by this handoff; extra args go to pytest
# = env $(env | grep -oE '^(DISCORD|CCDB)[A-Z_]*' | sed 's/^/-u /') uv run pytest tests/ -q
```

Baseline measured on `handoff/v4.1.0-base` on 2026-09-21:

- `pytest tests/` → **4556 passed, 0 failed** (about 95 s)
- `ruff check claude_discord/ claude_code_core/` → clean
- `pyright claude_discord/ claude_code_core/` → 0 errors (6 old warnings)
- `ruff format --check claude_discord/ claude_teams/` (what CI checks) → clean

If a test fails in a normal shell but passes with `scripts/test-clean-env.sh`, it is the
environment, not your change.

Full gate before calling anything done (same as CI plus core):

```bash
uv run ruff check claude_discord/ claude_code_core/ claude_teams/
uv run ruff format --check claude_discord/ claude_teams/ claude_code_core/
uv run pyright claude_discord/ claude_teams/ claude_code_core/
scripts/test-clean-env.sh
```

## 4. What is unfinished (the whole scope)

All of these live in the repo. Checkbox counts are as of the base; some checkboxes are
**stale** (the code already landed in the wave merges but the box was never ticked), so
every build task starts by reconciling boxes against the code.

| Plan | Open | What it is |
|---|---|---|
| `docs/plans/gowork-upgrade-execution-plan.md` | T03–T32 (30) | Go Work upgrade: multi-project, resource-aware parallel workers, recovery, auto-completion. T01–T02 done; T03 was started (WIP commit `b9773d3`, uncommitted tests saved). Strict order, one task at a time. |
| `openspec/changes/discord-command-surface/` | 14 | Location-aware slash commands + close/reopen lifecycle |
| `openspec/changes/trusted-agent-handoffs/` | 14 | Bot-to-bot handoffs (lookups already work — see `claude_discord/handoff_*.py`) |
| `openspec/changes/shared-project-catalog/` | 11 | One project catalog shared by the bots |
| `openspec/changes/discord-my-ai-setup/` | 13 | "My AI Setup" inventory view |
| `openspec/changes/professional-harness-audit/` | 12 | Harness configuration audit |
| `openspec/changes/parallel-gowork/` | 16 | Parallel Go Work task graph — **overlaps the Go Work upgrade plan**; reconcile, don't build twice |
| `openspec/changes/model-capacity-resilience/` | 13 | Never dead-end on "model at capacity"; fall back / queue |
| `openspec/changes/folder-session-launcher/` | 3 | Favorites/browse launcher — nearly done |
| `openspec/changes/trial-greeting/`, `trial-word-count/` | 6, 4 | Tiny trial features from the Lockin workflow test; probably already implemented — verify and archive |
| `.planning/2026-09-20-discord-agent-flow-program/` | 9 | The umbrella program for the seven openspec changes above (phases 3–5) |
| `.planning/2026-09-10-land-everything/` | 16 | Older "land everything" plan — mostly superseded; reconcile, tick what is done, record the rest |

Old branches that may hold work not yet in the base are listed with every commit in
`docs/plans/v4.1.0-branch-inventory.md`. Most of it already landed under different
commit IDs (dsh backend, plan cards, voice recorder, feature workflow are all present).
Known real gaps: `voice_transcripts` (branch `deploy/drew-ai-voice-transcripts`), the
standalone new-project/GitHub clone menu (`save/local-main-2026-09-21`), and
`e5c3293 fix: accept structured worker test evidence` (`lockin/feature-workflow-runtime`).

## 5. Rules that stop you from "finishing" things that aren't finished

1. **A checkbox is ticked only with proof.** Proof = the test(s) you added, the command
   you ran, and its pass line, written into the progress file
   (`docs/plans/v4.1.0-finish-all-builds.progress.md`) under that task. No proof, no tick.
2. **TDD** (repo rule): failing test first, then code, then green. Tests go in `tests/`.
3. **Read before you write.** Before building any task, grep for existing code that
   already does it. The wave merges landed a lot of foundation code
   (`claude_code_core/handoffs/`, `catalog/`, `capacity/`, `workflow/`, `lifecycle/`,
   `ai_setup/`, `harness_audit/` …). Extend those; never build a second copy.
4. **Never invent requirements.** The specs in each openspec change are the requirements.
   If a spec is genuinely ambiguous, pick the smallest reasonable reading, write it down in
   `docs/plans/v4.1.0-decisions.md` (one line: question → what you chose → why), and move on.
5. **One outcome per commit**, message style `<type>: <description>`, commit after every
   task. Never leave work uncommitted at the end of a session.
6. **Offline only.** Tests use fakes for Discord, model CLIs, providers and git remotes.
   No live model calls in tests. No paid API calls outside your own Claude session.
7. **Don't touch other people's bots.** Never use Drew's DrewAI bot token, never restart
   the Lenovo bot, never edit the Lenovo database. If you run a bot for a live check, run
   *David's own* bot from this clone, only in the DAVID'S COMPUTER category.
8. **Security audit** (`.claude/skills/security-audit/SKILL.md`) is mandatory after any
   change to `runner.py`, `_run_helper.py`, or any Cog.
9. **Don't merge to `main` and don't tag.** You push `release/v4.1.0` and open a PR.
   Drew merges after he tries it (his standing rule: GitHub `main` is last).
10. If a task is bigger than ~30 minutes, split it into smaller checkboxes in the plan
    first, then build them.

## 6. When to stop and ask Drew (only these)

- A spec requires a secret, account, or paid service you don't have.
- Two specs directly contradict and choosing wrong would throw away real work.
- The full test gate cannot be made green after a genuine attempt (say what fails).

Ask in one short plain-English message with 4–5 lettered options, recommendation first.

## 7. Finishing (last tasks in the plan)

- Full gate green (section 3) + security audit.
- `pyproject.toml` version → `4.1.0`; move `CHANGELOG.md` `[Unreleased]` items under
  `## [4.1.0] - <date>` and add one line per finished feature.
- Write `docs/plans/v4.1.0-release-notes.md` in plain English: what's new, how to try each
  thing in Discord in 30 seconds, what was dropped and why, any known issues.
- `git push -u origin release/v4.1.0` and `gh pr create --base main` with the release
  notes as the body.

## 8. How the Lenovo picks it up (done on the Lenovo, not here)

The live bot on the Lenovo runs from a dev worktree named in `~/.ccdb-dev-worktree`
(see `CLAUDE.md` "開発フロー"). To switch it to v4.1.0 a Lenovo session will:

```bash
cd /home/drewp/main-projects/ebi-agent-chat-relay
git fetch origin release/v4.1.0
git worktree add .worktrees/release-v4.1.0 origin/release/v4.1.0
(cd .worktrees/release-v4.1.0 && uv sync --dev && scripts/test-clean-env.sh)
echo /home/drewp/main-projects/ebi-agent-chat-relay/.worktrees/release-v4.1.0 > ~/.ccdb-dev-worktree
# restart the bot only after Drew says go
```

After Drew tries it and says it's good: merge the PR on GitHub, `make dev-off`.

## 9. Things already true that you should not redo

- Project lookups ("ask DrewAI to search…") work end to end: the worker's answer comes
  back to the asking thread, and lookup workers run on cheap Claude (haiku, override with
  `CCDB_PROJECT_LOOKUP_MODEL`). Commits `af1be95`, `53626ff`.
- Handoff envelopes, inbox, executor, return and status view exist
  (`claude_discord/handoff_*.py`) — they are part of the trusted-agent-handoffs change.
- The three-bot layout (DrewAI / iMac / David categories) is described in
  `.planning/davids-computer/three-bot-flow.md`.
