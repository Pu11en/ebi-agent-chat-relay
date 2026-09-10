# dsh review + fix round — handoff for the next agent

Written 2026-09-10. PR #10 (`chore/consolidate-main`) was reviewed with the
tag1consulting/comprehensive-review methodology (parallel specialist agents:
summary, fresh-eyes, adversarial, edge-case-tracing, plus an orchestrator
architecture pass) and the blocking findings were fixed on branch
`fix/dsh-review-findings`, which was fast-forwarded onto the PR head.

## Current state

| Thing | Value |
|---|---|
| PR | [#10](https://github.com/Pu11en/ebi-agent-chat-relay/pull/10) — mergeable, **CI green** (test 3.12 + 3.13, CodeQL, Analyze, package) |
| Branch | `fix/dsh-review-findings` @ `22b8be7` (also the PR head, and `main` local is `bb3c124`) |
| Fix commits | `468792f` (dsh backend), `22b8be7` (plan-card + system-context + codex images) |
| Local suite | 2930 passed, 1 pre-existing failure (`test_deploy_recovery.py::test_runtime_hook_loads_fallback_then_returns_to_main` — fails identically on pre-merge `24567c4`). `tests/test_api_server.py` hangs in this environment (pre-existing); CI runs it fine. |
| Lint/type | `ruff check` + `ruff format --check` clean, `pyright` 0 errors (6 pre-existing warnings) |

## What this session fixed

### dsh backend (`claude_code_core/dsh_backend.py`)

1. **Model switch mid-thread broke the thread permanently.** `_LIVE_SESSIONS` was
   process-global while the SDK's session-lineage check is per-runtime, so
   `/model glm-5.2` after a deepseek turn kept handing the old runtime's session
   id to the new runtime → every subsequent turn errored until `/clear` or a
   restart. Sessions are now bound to the runtime key that minted them
   (`_session_for_turn(session_id, runtime_key)`); a switch starts a fresh
   session, which is also the documented restart trade.
2. **First-turn failure for unconfigured deploys.** The SDK refuses an implicit
   `~/.dsh` (`HarnessConfig requires an explicit dsh_home or non-empty
   DSH_HOME`). ccdb now defaults to `$XDG_STATE_HOME/ccdb/dsh` (created on
   first use); explicit `dsh_home` arg > `DSH_HOME` env > default. Documented in
   `.env.example`.
3. **`/effort` was a confirmed no-op once a runtime existed.** `reasoning_effort`
   is fixed at runtime handshake, so it now participates in the runtime cache
   key. Cost: one more ~382 MB runtime per distinct effort value.
4. **`DshRunner.clone()` silently swallowed `append_system_prompt`** — the
   per-turn system context (AI Lounge, worktree-collision notice, file-delivery
   marker) never reached dsh models. `clone()` now accepts it like
   ClaudeRunner/CodexRunner.
5. **System context only reached turn 1.** `_with_standing_instruction` now
   prefixes every turn, matching the Claude CLI's per-run
   `--append-system-prompt` (the injected context is ephemeral, recomputed each
   message).
6. **Stop leaked the orphaned worker's notifications** into an abandoned
   unbounded queue. A turn-scoped closed-flag on `push` stops that.
7. **Environ scrub race.** The scrub popped keys from the real `os.environ`,
   which could raise `RuntimeError: dictionary changed size during iteration`
   in a concurrent Claude/Codex spawn. It now swaps the mapping object under
   the lock and restores it (`# noqa: B003, B010` — deliberate swap).
8. **Max-tokens notice was swallowed.** It carried a session id, which the
   processor drops; it is now session-less so a truncated answer renders as a
   visible warning instead of a clean "Done".
9. Docs: `docs/backends.md` no longer promises that a follow-up turn waits for
   stopped work to quiesce (nothing enforces that — see open findings).

### Plan-card / UI / shared plumbing

- **Plan-card previews were dead code in production.** `file_sender` temp-copied
  `x.plan.json` with only the last suffix (`.json`), so `render_file_to_png`
  failed its own routing check. The compound suffix is now kept.
- `plan_card` tolerates non-dict step entries, clips oversized fields (4000
  chars), logs a non-object top level, and oversized PNGs are dropped instead
  of failing the attachment batch (they share Discord's 8 MiB-per-message cap
  with the original file).
- **`APPEND_SYSTEM_PROMPT` was dead on every normal chat turn.**
  `clone(append_system_prompt=...)` replaces rather than merges and the
  per-turn context is never None, so the operator value was lost. New
  `_merge_system_context(base, built)` prepends it. NOTE: when `built` is None
  it returns None (nothing is cloned; the runner already carries its own
  instruction) — tests/conftest.py patches `_build_system_context` to return
  None, and a naive `return base` there (with a MagicMock runner) breaks the
  semaphore tests in a confusing hang. Keep the `isinstance(base, str)` guard.
- **Codex/local silently dropped image attachments.** They now yield a
  session-less SYSTEM warning event (same shape dsh uses).
- `/backend`'s describe string names `dsh`.

Tests added/updated: `tests/test_plan_card.py` (new), dsh runtime-home/effort/
lineage/every-turn/max-tokens tests, codex image-warning test,
`_merge_system_context` unit tests, plan-card routing tests in
test_render_preview.py / test_file_sender.py.

## Open findings (not fixed — prioritized for the next agent)

**High**

- **Unbounded dsh runtime cache.** `_RUNTIMES` keyed (route, model, cwd,
  effort) with no eviction; 382 MB each, all user-mintable. Ten worktree
  threads ≈ 3.8 GB, an OOM vector. Suggest LRU eviction that `close()`es
  evicted harnesses (cost: fresh session, the documented restart trade).
- **Stop leaves the agent working invisibly with no coordination.** No
  per-session serialization; a follow-up message runs `session.run()`
  concurrently with the orphaned turn on the same runtime. Either a
  per-runtime run lock + backend-specific "still finishing in background"
  status, or accept + surface loudly.

**Medium**

- **Secrets widened in both directions.** `DEEPSEEK_API_KEY`/`ZAI_API_KEY`
  survive into Claude/Codex spawn envs (their denylist doesn't include them);
  the dsh runtime inherits everything else (`ANTHROPIC_API_KEY` etc.). A
  per-backend env allowlist would close both.
- **dsh runs fully autonomously.** `permission_mode`,
  `dangerously_skip_permissions`, `allowed_tools` are stored and unused; no
  approval channel exists and `/backend dsh` doesn't warn about it. Minimum:
  a visible autonomy warning on backend switch.
- **Supply chain:** `deepseek-harness-sdk>=0.1.2rc1` — pre-release, no upper
  bound, ships a 70+ MB binary wheel, installed unconditionally by
  `pre-start.sh`.
- **A stuck SDK call holds a default-executor thread forever** (`asyncio.to_thread`
  can't cancel a running thread). Dedicated bounded executor for dsh blocking
  calls.
- **`/model` lists only the two hardcoded dsh routes** (`DSH_ROUTES`), not
  patch-declared ones; no tests for dsh model discovery.
- **Cross-backend handoff lies about history.** Switching away from dsh/agui
  posts "its file-backed conversation history will be carried into a fresh
  session" but `ConversationHistoryReader.read` returns "" for those backends
  (also logs "unknown backend 'dsh'"). Branch the notice on what was actually
  read.
- **dsh restart-resume is misleading.** The bot posts "The bot restarted,
  please report what you were working on" but dsh sessions never survive
  restart by design. Skip that marking for dsh, or say "context was lost".
- **Engine-status footer drops dsh.** `_post_engine_status_footer` spawns for
  any backend but renders only claude/codex lines; dsh's `describe_api()` label
  is computed and discarded.

**Low**

- `/model set` on agui is an accepted no-op (remote agent owns model
  selection).
- `_LIVE_SESSIONS` grows unboundedly; ids for failed turns are kept.
- `shutdown_runtimes()` has no production caller; runtime subprocesses are
  never asked to shut down on bot restart.
- `event_processor`'s `accumulated_text` is actually last-block text (rename or
  accumulate); `_make_error_embed` in `_run_helper` is dead and diverges from
  `_error_notice` (`.match` vs `.search`); `backend_settings` docstring omits
  dsh; `default_model_for` silently returns "sonnet" for unknown backends while
  `command_for` raises.
- Plan-card feature has no CHANGELOG entry; dsh subagent tool activity is
  invisible (notifications for non-root sessions are dropped); SDK runtime
  stderr tails surface verbatim in Discord error text.
- Design decision 11 (one sanctioned vendor call) was widened by dsh's two
  `/models` calls without updating the decision record.

## Deploying this work

1. Merge PR #10 (CI green).
2. Bot: `pre-start.sh` pulls on restart, so a restart picks up the fixes. On
   this host the bot runs from the main checkout with the dev hook inert.
3. Verify through Discord: `/backend dsh`, a thread, Stop, a follow-up in the
   same thread, then `/model glm-5.2` (needs `ZAI_API_KEY` in `.env`) and
   another turn — the model-switch lineage fix should start a fresh session
   cleanly instead of erroring.
4. The iMac (second machine) still needs: pushed main or the branch,
   `uv sync --extra voice --extra deepseek`, both provider keys in its `.env`,
   restart. DSH runtime wheel is `macosx_14_0_arm64` (macOS 14+, Apple Silicon).

## The goal behind this (from Drew)

This Discord bot is the primary work environment; the dsh backend exists so
DeepSeek and GLM (Z.ai) models are usable as options alongside Claude and
Codex — "add two models to the discord server as models so I can use those".
Backend/model switching mid-thread must just work; that is what the lineage,
effort, and system-context fixes were for.

## Verification commands

```bash
uv run pytest tests/ -q --ignore=tests/test_api_server.py   # expect 1 pre-existing failure
uv run ruff check claude_discord/ claude_code_core/ tests/
uv run ruff format --check claude_discord/ claude_code_core/ tests/
uv run pyright claude_discord/ claude_code_core/
```
