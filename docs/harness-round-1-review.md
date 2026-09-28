# Harness reset — implementation and verification record

## Latest authorization and round scope

Integrated release candidate v4.2.0: `make verify` passed with 6,112 tests and
18 warnings (including known asynchronous mock cleanup noise), plus clean ruff
and pyright. The Jester commits required two type-check corrections (typed cog
lookup and binding the receipt callback with `partial`) and one test formatting
correction; owner-routing behavior was not redesigned. The existing setup-viewer
fix and DSH stop-continuity fix are preserved in this integration.

The candidate Codex settings were validated with native app-server `skills/list`
without a model turn: zero enabled plugin skills and zero discovery errors;
shared standalone skills are not yet archived at this checkpoint.

The owner removed the Claude review requirement and authorized hands-off cheap
offline verification, coordinated restart, main publication and a release tag.
Earlier review gates below describe the first slice only, not the active workflow.

The second slice adds GLM through Claude Code using per-process Z.ai credentials,
an instance backend allowlist, fair model suggestions, bounded file-delivery
instructions with an explicit project-output destination, and shared linked-Git-
worktree classification for both project pickers. Existing history/explicit paths
remain usable. It also makes AGENTS.md the real project rulebook and moves the
long inherited guide to docs; obsolete /home/ebi Claude hooks are disabled.

Checks so far: test-first reproductions; 6,082 offline tests passed, 18 warnings;
ruff and pyright passed. These are not paid model calls, a GLM generation test,
or proof of billing/cache savings. Global settings candidates and private backups
are separate from this public repository; activation and release are recorded later.

GLM configuration follows https://docs.z.ai/devpack/tool/claude; native AGENTS.md
support follows https://code.claude.com/docs/en/memory (local Claude 2.1.283).
Optional global skills/plugins and old shared memory are to be recoverably archived
or disabled, not deleted. No Hindsight, ECC or replacement app is installed.

Check: make test-one f="tests/test_cross_backend_handoff.py tests/test_lounge.py"
Try: make verify
Open: no new application; nothing is activated by these commands.

## Goal and current boundary

Drew confirmed the goal: a simple Discord workspace where the selected AIs follow
the same preferences, produce correct work in the right project, and need less
correcting, with less wasted time/context and an understandable chosen toolset.
Improve in rounds, use the activated setup normally for days, then review real
evidence in a fresh session. This slice is not the complete harness reset.

Review branch: `fix/harness-round-1`.
Worktree: `/home/drewp/.local/state/ccdb/session-wt/harness-round-1-20260928`.
Base: `9238e89136629426e829b073b99636008d9b3ff1`.
Inspect `git diff 9238e891..HEAD` after the local save commit.

The user's sequence is implementation, Claude Code review, then coordinated
restart and a tagged publication to main once the result looks good. This note
does not assert review approval, integration, deployment or publication.

## Changes to review

- Lounge guidance: the empty-message prompt drops from 1,030 to 330 words, keeping
  authenticated coordination examples, collision checks, claims, release and
  queued communication. A 420-word regression ceiling limits renewed bloat.
- Opening/closing announcements are for substantial work, not every small chat.
- Removes the old demand to push automatically when standing down; preserving
  local work and respecting the task's actual publishing authority are explicit.
- Codex history: accepts user/assistant response_item messages as well as legacy
  event_msg records; ignores system/developer, tool, reasoning and known generated
  AGENTS/environment envelopes. It does not discard ordinary user HTML.
- Collapses adjacent same-role/text mirrors in different record formats, while
  preserving repeated messages within a format and tested paired repeats.
- Skips malformed JSON/record/payload shapes; retains the existing transcript
  lookup, session-ID validation, size limits and unanswered-tail behavior.

This does not move changing lounge posts out of the system prompt or establish
cache/billing savings. The measured reduction is this instruction block's words,
not total session tokens. Mirroring without stable message IDs is best-effort;
review ambiguity in mixed-format identical messages rather than assuming universal
deduplication. Generated-envelope filtering covers observed formats, not every
future CLI format.

## Evidence and tests

- Test-first run: five failures / 49 passes before implementation, reproducing
  missed response messages, malformed payload crashes and excess prompt size.
- First corrected run: 54 passes; additional malformed-field regression then
  failed as intended and was corrected before full verification.
- Two previously empty real Codex histories now return bounded, nonempty handoffs
  (3,753 and 6,843 characters). Read-only check; no new model call and no transcript
  text copied into this repository.
- Security lint passes on both changed modules; subprocess arguments, credentials,
  live settings and API behavior are untouched.
- Final full-suite result is recorded in the completion entry below.

## Jester ownership and release integration

Jester Voice is a separate, actively built replacement; the old voice system is
archived according to Drew. Do not inspect, edit or reactivate either voice system
as part of this slice.

Read-only checks at 2026-09-28 11:31 local service time showed Jester thread
1553899450227757156 running and another project active. Its EBI worktree changed
cogs/claude_chat.py, cogs/task_loop.py, ext/api_server.py, voice_labels.py and
related tests. This slice changes none of those files. A coordination note was
posted to the lounge (2567); it is a broadcast, not a confirmed handoff or lock.

Do not activate this worktree as a replacement for the current runtime: the
runtime pointer still names fix-dsh-stop-continuity-20260927, which includes the
DSH stop fix not in this main-based branch. Reconcile the current main, runtime,
Jester changes and this patch in an integration copy, then rerun checks. Do not
overwrite any live pointer or change global agent configuration while Jester's
session is working. Recheck live sessions/claims before the eventual restart.
Never stage unrelated untracked status cards, logs or other sessions' work.

## Still open, not silently completed

- Shared instruction/configuration reset and selected skills/MCP/plugins.
- Project discovery, working-copy/alias organization and output placement.
- GLM route, DSH retirement, switch catalog completeness and Hindsight choice.
- Full custom-behavior inventory and broader history audit.
- Independent Claude review, combined integration check, release tag/version,
  restart, publication, observation-window start and later usage evaluation.

Detailed goal/evidence tracking remains in the main checkout's
`.planning/2026-09-28-agent-os-reset/`; use the latest notes without importing
them into every session's standing prompt.

## How to check this slice

1. Confirm the diff touches only the two modules, their tests and this review note.
2. Run the focused check and `make verify` in this isolated worktree.
3. Confirm the running bot and Jester were not switched to this branch.

## Claude review request

Review this branch read-only against the base above, concentrating on history
fidelity, generated-context exclusions, duplicate detection, malformed records,
and whether shorter lounge guidance retains necessary coordination. Report concrete
findings and a pass/fix-first verdict. Do not restart, publish, inspect voice work,
or change global settings during review. A passing review of this slice does not
mean the full reset or the combined Jester release is complete.

## Completion evidence for this slice

`make verify` exited 0: formatting and lint passed; pyright reported 0 errors;
6,072 tests passed in 105.22 seconds on Python 3.13.12. The run reported 20
warnings (mock coroutine/deprecation/duplicate-zip warnings) and a teardown
CollisionWatchCog mock task exception. Those paths were not changed here, but
this run alone does not establish their baseline status; do not describe it as
warning-free. The formerly noted deploy-recovery test did not fail in this run.

`ruff check --select S` passed on both changed modules, and `git diff --check`
passed. No new model invocation, Claude review, deployment, restart, push or tag
was performed. The independent review and combined release remain outstanding.
