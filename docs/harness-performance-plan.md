# Codex and Claude harness improvement plan

## Goal and status

Research completed 2026-10-19. Improve verified task completion, continuity and
response time in Discord, with fewer repeated instructions from the operator.
Implementation is in progress; no improvement is claimed as deployed. No model
settings, live service, provider route or other computer changed during research.
The earlier diagnostic fixes at `c8be866` remain undeployed.

The operator has already supplied the useful requirements: short replies, work
in the right project, preserve decisions, finish authorized work, and demonstrate
results. They should choose user-visible tradeoffs, not debug the implementation.

## Findings that determine the order

| Finding | Evidence | Implication |
| --- | --- | --- |
| Codex context tracking is unreliable | Eight recent completed sessions: seven saved zero/missing usage, one stale; none matched final native usage. | Fix measurement before changing context thresholds. This does not prove Codex lost memory. |
| Claude usage tracking matched the sample | Eight saved usage counts matched final input plus cache usage. | Preserve that path; this does not validate its configured window size or prove task quality. |
| Codex output surfaces differ | Installed CLI reports 0.160.1. Its upstream JSON processor consumes token notifications internally, emits total usage on completion, and does not forward the raw token-count event expected here. | Do not treat cumulative usage as current context or assume rollout JSON equals CLI stdout JSON. |
| A second counting flaw exists | Read-only parser reproduction: completion input 9,000 plus last-request input 6,000 persists a reconstructed 9,000. | Even with a token notification, `_fold_token_count` prefers completion totals over last-request context. |
| Continuation explicitly asks again | `claude_code_core/context_nudge.py::starter_prompt` tells the new session to ask before starting. | For already authorized work, the harness itself can cause an avoidable extra question. |
| Handoff route needs verification | `ContextNudger.hand_off` supplies the folder but no explicit backend/model to `spawn_session`. | Test continuation after a provider switch; a default route may differ from the source thread. This is a code-path risk, not yet an observed wrong-provider handoff. |
| Folder recovery caused repeated corrections | A reviewed Discord thread repeatedly clarified the project and received refresh advice without an accessible control. Catalog logs separately rejected an alias outside an approved root. | Fix the actual catalog binding and usable recovery action, not just wording. |
| Prompt inventory is incomplete | A captured Codex startup advertised 123 skill entries with nine repeated names; the existing audit scans fewer items. | Measure loaded instructions separately from skill metadata before removing anything. |

The earlier runtime snapshot showed a responsive relay, low bot CPU and healthy
databases. Spotlight was busy and Discord message edits sometimes rate-limited.
Neither observation establishes the cause of long agent turns. Mixed historical
tasks do not provide a fair Codex-versus-Claude speed or quality ranking.

OpenAI's [non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
documents the exec JSON event surface. The version-specific
[0.160.1 JSON processor](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
establishes the total-usage behavior above. Source inspection explains the mismatch;
a captured live stdout fixture is still needed before implementing the adapter.

## Proposed fixes, in order

### 1. Make context measurements honest

Keep the current runner initially. Separate current input context, model window,
cumulative usage and cache counters. Store measurement source and freshness; show
unknown when measurement is missing instead of presenting zero or an inherited
window as current. Never estimate current context from cumulative billing usage.

First capture a sanitized real event sequence from the installed CLI. Prefer an
existing structured provider surface. If exec lacks current context, evaluate a
bounded adapter reading the matching native session's latest token metadata.
Require exact session identity, a current-turn timestamp, bounded reading, no
prompt-body retention, and graceful unknown behavior on missing/malformed files.
Do not scan every transcript on each message. If those guarantees cannot be met,
keep unknown and investigate the documented
[app-server token-usage notifications](https://learn.chatgpt.com/docs/app-server)
as a separate integration change. A full transport migration is not the first fix.

Acceptance: regressions cover missing events, distinct total/last usage, cache
counting, resumed turns, compaction, stale records, and model/provider changes.
Compare three consecutive real turns per backend against their matching native
records after deployment. No claimed improvement until that comparison succeeds.

### 2. Stop avoidable repetition and folder dead ends

Use existing handoff machinery. Preserve goal, decisions, current branch/commit,
unfinished changes, verification results, constraints and next authorized action.
Carry the selected provider/model and project explicitly. Continuing approved
work should not generate a second permission question; unresolved scope decisions
must remain unresolved. Validate a newly written handoff, not merely an old file
at the expected path. Keep the original thread usable if continuation fails.

The current nudge displays a crossed threshold rather than necessarily the actual
percentage, and promises no information loss. Replace those with measured usage
and accurate wording. Context percentage is a capacity signal, not a diagnosis of
forgetting. Keep native compaction; do not force resets at an arbitrary percentage.

Reproduce project recovery through the same Discord picker/catalog paths the
operator uses. Register a legitimate canonical project location or approved root
where appropriate; retain root validation. Never recommend an unavailable refresh
button or substitute a same-named project on another computer.

Acceptance: a fresh continuation retains known decisions, route and folder,
resumes approved work, and stops for a genuinely missing decision. A known project
opens through Discord without repeating its name or moving to a desktop interface.

### 3. Reduce measured overhead, then compare real outcomes

Inventory actual injected instructions, skill metadata, loaded skill bodies,
conversation history and tool output separately. Remove proven duplicate entries
and stale conflicting rules; move task-specific detail to on-demand references.
Preserve required preferences and useful skills. OpenAI documents
[on-demand loading of skill bodies](https://learn.chatgpt.com/docs/build-skills),
so installed skill count alone is not the context cost. Claude recommends concise
[project instructions and scoped rules](https://code.claude.com/docs/en/memory).

Instrument comparable timestamps for both providers: message received, run start,
first useful visible response, tool time, final result and delivery. Identify
queueing, provider latency and Discord rate-limit waits separately. Debounce status
edits only if measured delivery waits justify it; preserve final-result delivery.

Start with six scenario types drawn from observed work: known-project lookup,
small verified code fix, research with a saved decision, same-provider resume,
fresh-context continuation, and explicit provider handoff. Expand to 20 cases,
with repeats, before drawing broad performance conclusions. Use isolated fixtures
and the same pinned task state, model configuration and success criteria.

Score completed outcome, verification evidence, repeated requests for known facts,
wrong-project actions, time to useful response and total completion time. Record
unnecessary retries only after reviewing why they happened. Do not optimize token
count or test totals as substitutes for successful work. This follows Anthropic's
[outcome-based evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
Keep a change only if it preserves correctness and improves the targeted behavior;
report a latency result as inconclusive when the sample is too small or variable.

## Accepted operator decision

On 2026-10-19 the operator accepted the recommendation: save a validated handoff
and offer one Continue button when a fresh session is appropriate. Do not
automatically create a new thread merely because a context threshold was crossed.
The user-visible choice is settled; implementation and live verification remain
outstanding.

The operator also rejected the long plan attachment. Keep supporting documents
in the project and default to short plain-text chat replies. Research or planning
requests do not by themselves request a card or attachment. The standing Caveman
preference remains active for both Codex and Claude Code.

No need to ask the operator for examples already present in session history or
to choose telemetry plumbing. Later speed-versus-depth choices should use a
measured comparison rather than an abstract questionnaire. Keep current model
choices until there is evidence for changing them.

## Implementation and release checks

Make separate small changes for telemetry, continuation and project recovery.
Each bug needs a failing regression first. Run focused tests, the repository's
parallel `make verify`, and the required boundary security review. Preserve public
defaults and optional providers; personal catalog configuration stays external.

Source commits do not update the installed iMac service. Before activation, check
the exact deployed package, active sessions and claims; coordinate a restart and
keep the known-good package available. Verify health and complete one Discord
workflow per provider, including route/folder continuity and final delivery.
Record installed revision separately from source revision. Observe the next normal
work sessions before claiming reduced repetition or better performance.

The MacBook crawler endpoint was reachable and fetched official OpenAI docs plus
the version-specific source using its first fetch tier. No crawler installation,
configuration change, recipe execution or model job was needed. Private metadata
and reproducible review notes remain in the local ignored planning directory;
this plan contains no raw conversation transcripts or credentials.

Validation for this documentation-only change: `make verify` exited 0 on
2026-10-19; formatting, lint and type checks passed; 6,133 tests passed, 12
Linux-specific tests skipped, and 18 warnings were reported. The warnings include
existing asynchronous mock cleanup noise. This is not a live acceptance test of
the proposed fixes. The parser reproduction above confirms a defect; it does not
repair it.

## Implementation progress

Context tracking now separates last-request input from cumulative CLI usage. The
optional native adapter queries the matching session ID in Codex's read-only
SQLite index, validates the rollout identity and current completed turn, and
reads at most a 256 KiB first record plus a 1 MiB tail. Cumulative counters must
match stdout. Missing, stale, malformed or mismatched data becomes unknown.
No prompt text is returned or persisted by the adapter. The runner waits for
natural process exit before reading, allowing native records to flush.

Context source and observation time are persisted. Unknown readings replace old
ones, and changing native session, provider or an explicitly saved model clears
the previous measurement. Claude's existing per-request usage path is retained.
Codex `turn.failed` now surfaces a terminal error instead of being ignored.

Two real pre-deployment runner probes succeeded on the same native session:
first request 16,993 context tokens; resumed request 17,185 context tokens versus
34,178 cumulative input tokens. Both reported a 258,400-token window and
`codex_rollout` provenance. These establish local runner behavior, not deployed
Discord acceptance. Focused context tests: 200 passed after observed red tests.

Continuation now uses a unique handoff file, validates content in all required
sections, pins the selected route/model and configured effort, and waits for a
successful first turn before closing the source session. Missing/empty/stale
handoffs and failed starts leave the source open. The prompt resumes already
authorized work and preserves unresolved decisions. The Continue button displays
the measured percentage without claiming that context size proves forgetting.
Focused continuation tests: 34 passed after eight reproduced failures.

Release checks also cover existing message-delivery, queued-work, stop/recovery
and restart behavior. Their tests are part of full verification; live installed
revision and Discord acceptance still need separate evidence. Project recovery,
overhead measurements, independent review, push and deployment remain outstanding.

Full verification for this implementation slice exited 0: 6,171 passed, 12
Linux-only skips, 18 warnings; format, lint and type checks passed. Additional
type checks covering the new core adapter also passed, as did public imports.
Security lint found nine S101 assertions already present in the baseline; a
statement-level comparison found no new findings. These assertions narrow
internal types/state, not new authorization boundaries. Existing asynchronous
mock cleanup warnings and a collision-watch mock teardown exception remain
visible in the log; verification is not described as warning-free.

An independent read-only Claude/Opus review was started against an immutable
patch. Its result is pending, so it is not yet a passed review or release gate.

### Interrupted-turn error reporting

Native records for two interrupted October 19 turns report
`usage_limit_exceeded`. The installed relay instead classified them as permanent
generic CLI failures, once quoting an unrelated expired tool-process handle.
Parsing `turn.failed` fixes the missing signal, but a regression reproduced a
second problem: a later nonzero process exit overwrote that structured failure.
The runner now retains the provider error and uses stderr as a fallback only
when stdout supplied no failure. Two failing regression cases (empty stderr and
an unrelated tool error) now pass; the runner and capacity suites pass all 158
tests. This improves diagnosis; it cannot increase an account's usage allowance.
Full `make verify` exited 0 for this fix: 6,173 passed, 12 skipped, 18 warnings;
format, lint and type checks passed. The same existing asynchronous mock warnings
remain. The touched runner has one baseline security-lint assertion and no new
security findings.

The original independent review process ended without a saved result. It has
been relaunched with a detached supervisor, direct output files and durable
completion/timeout status, so a fresh agent process does not need a stale tool
handle to recover it. Review remains pending until its actual result is read.

The independent review ran on `claude-opus-4-8` and returned FIX FIRST: the
original incident had captured a native failure, not stdout, so the stdout-only
regression was insufficient evidence. A bounded fallback now reads the latest
current-run native `task_complete.error` after a failed process without a stdout
failure. It shares the context adapter's identity, path, size and freshness
checks. An isolated copy of the actual failed native completion reproduces exact
message recovery and `quota_exhausted` classification through the runner, with
subprocess I/O stubbed and no stdout error. This does not claim a new live quota
failure; the operator reports that their limit is cleared.

The review also prompted malformed-usage guards and a check preventing a later
completion from overriding a reported failure. Fifteen new cases failed before
the fix; 190 focused tests and full verification now pass (6,188 passed, 12 skips,
19 warnings). Nonzero exits deliberately leave context unknown even after a
completion event. The nullable health count is documented; no internal numeric
consumer was found beyond its endpoint and tests. Follow-up review is running.
