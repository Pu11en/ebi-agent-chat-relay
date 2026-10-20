# Codex and Claude harness improvement plan

## Goal and status

Improve verified task completion, continuity and response time in Discord, with
fewer repeated instructions from the operator. Research completed 2026-10-19;
the scoped source changes were reviewed and pushed. After an initial import-timeout
rollback, the operator authorized the corrected deployment on 2026-10-20.
Revision `0267b68` is now installed and healthy; all 258 installed package files
match the reviewed candidate. Native context comparisons passed for three
consecutive turns on each provider. Installed continuation, folder recovery,
provider switching and final-delivery checks passed. The scoped iMac goal is
complete. Later sections distinguish current evidence from earlier research,
baseline measurements and implementation checkpoints.

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

The follow-up review completed on `claude-opus-4-8`: PASS for the implementation
through `edbbb16`, including all five earlier findings. Catalog changes below
are a separate review scope.

### Project discovery and opening agree

A direct-child symlink could previously be advertised as available even though
opening it correctly rejected a canonical target outside its approved root.
Discovery and resolution now apply that same canonical boundary. The identity
remains discoverable as unavailable; links inside the root remain available,
and retargeting a link after discovery is rejected at revalidation. The catalog
does not silently broaden approved roots to repair a local shortcut.

Existing explicit-path recovery remains supported: `/cd <absolute folder>` or
the folder browser's Favorites/Add by path and Start here controls can open an
authorized folder outside catalog roots. They do not require moving the project
or expanding catalog access to a home directory. Three reproduced failures now
pass; catalog/service/launcher suites pass 201 tests. Across twelve local uncached
scans of 29 projects, the median rose from 3.614 ms to 6.250 ms for the extra
canonical checks. This is a small correctness cost, not a speed improvement or
an agent-latency benchmark. Live folder recovery remains a deployment check.

Full verification for the catalog correction exited 0: 6,192 passed, 12 skipped,
19 warnings; formatting, lint and type checks passed. Separate catalog review
completed on `claude-opus-4-8`: PASS. Security lint's existing S105 finding is
the literal colon used as an identity separator, not a credential; comparison
with the prior source found zero new findings. No deployment or live acceptance
is claimed.

### Instruction overhead evidence

The first completed native runner probe stored 15,073 UTF-8 bytes in three
developer messages and 4,588 bytes in two user messages. Its catalog-bearing
message listed 28 entries; the broader active conversation catalog observed
during research differed. This is stored-message evidence, not an exact token
count or a full inventory of tool schemas. The probe did not load the Caveman
skill body as message text. Installed skill-body sizes therefore cannot be
counted as mandatory startup context.

Nine workflow names occur in both global and project skill directories. Eight
bodies are byte-identical; the Lockin workflow differs. The project copies keep
this reusable repository self-contained, while global copies serve other
projects. Removing either scope without a supported scoped loading mechanism
would remove functionality. These files remain. No unmeasured speed or token
saving is claimed from deleting them. Scenario and post-deployment measurements
remain required before reporting an overall performance change.

### Deployment rollback and maintenance evidence

The attempted deployment of `f1372cf` installed the candidate, then exceeded the
30-second deadline for its public-import smoke check. The worker restored the
previous package and reverted its runtime pin change. The candidate is **not**
running and has not passed live release acceptance.

An independent follow-up compared all 257 restored package files with the
known-good `1514f26` wheel: no mismatches. The runtime pin is restored, the private
environment is unchanged, and the restarted service reports healthy. The
installed distribution now identifies the local rollback wheel; its revision is
established by exact file comparison, not by nonexistent VCS metadata.

New diagnostic imports passed for the restored package in 10.461 seconds and
the candidate source using runtime dependencies in 20.340 seconds. These probes
did not install a package or restart the service. They do not reproduce or
explain the original timeout, prove a safe cold-start deadline, or establish a
candidate production pass. A future deployment should diagnose imports before
service interruption and preserve stack/timing evidence on timeout.

Post-restart logs also show 226 member-enrollment calls across 33 active and
193 archived Discord threads, with 45 rate-limit retries in the inspected log
tail. The backfill finished about four minutes after login. This is a separate
measured startup overhead finding; it is not evidence that Discord caused the
earlier import timeout. Changing membership behavior requires its own scoped
compatibility checks.

The maintenance follow-up explicitly prohibits redeployment and repeating
completed tests. Live scenario observations therefore describe the restored
revision only. They cannot satisfy the candidate's post-deployment context,
continuation, folder recovery or delivery acceptance criteria. Existing source
verification and independent review remain recorded above; neither is
represented as a successful deployment.

### Restored-version live scenario measurements

Five Codex scenario types completed on the restored revision, with six successful
turns because continuation includes handoff preparation and a fresh-session turn.
Each final result was verified in actual Discord messages. The selected and native
model was `gpt-6-astra` with native `high` effort. Same-provider resume kept its native
session; the fresh continuation used a different native session while preserving
the fixture folder, selected model/effort, accepted decision and verification state.

| Scenario | Submission to final Discord result |
| --- | ---: |
| Known-project catalog lookup | 59.443 s |
| Small code fix with failing-then-passing tests | 60.838 s |
| Research using the supplied local corpus | 42.940 s |
| Resume with the accepted decision | 39.745 s |
| Fresh-context continuation | 45.511 s preparation + 34.427 s fresh turn |

These are individual diagnostic observations, not an overall speed improvement,
native time-to-first-token measurement or provider ranking. Driver waits and relay
cooldowns are outside those per-turn durations. One attempted next-turn submission
was correctly rejected by the relay's 60-second pair cooldown; the measurement
driver was adjusted to respect it. It did not require a model-task correction.

The first three consecutive Codex turns ended with native last-input counts of
30,295, 39,087 and 41,206 tokens, each with a 258,400-token native window. The
restored relay saved neither count nor window for all three; the remaining three
successful turns also saved no context measurement. This reproduces the live
baseline defect, rather than validating the undeployed fix.

The fixture's original tests, instructions and sources remained byte-identical.
Independent extra checks confirmed exact-boolean counting, including rejection of
integer `1` and string `"true"`. Saved research, resume and continuation artifacts
retained the accepted constraint without requiring the operator to repeat it.

The handoff file passed the reviewed source validator. The fresh session was
created through the existing REST control plane with an explicit route/model.
This is not a live test of the candidate's Continue-button callback or automatic
source-closing behavior.

The first explicit switch to Claude created a different Claude-native session,
but inference hit a session limit. That failed attempt remains separate from
the successful retry after the reported 01:00 reset. Its synthetic zero-token
record is not a context measurement. The queued follow-up was disabled when
the operator resumed this session, avoiding duplicate execution.

### Baseline completion and Claude window correction

All six scenario types now completed on both routes, including provider switches
in both directions. Each route has seven successful measured turns because
continuation includes preparation and a fresh turn. Actual Discord final delivery
and expected artifacts were verified. Original fixture instructions, tests and
source corpus stayed byte-identical; independent exact-boolean checks passed.
This completes baseline measurement on `1514f26`, not candidate acceptance.

| Remaining baseline scenario | Submission to final Discord result |
| --- | ---: |
| Codex route switching to Claude, successful retry | 93.207 s |
| Claude catalog lookup | 28.291 s |
| Claude small verified code fix | 113.974 s |
| Claude supplied-corpus research | 13.816 s |
| Claude same-provider resume | 51.118 s |
| Claude fresh continuation | 17.539 s preparation + 19.717 s fresh turn |
| Claude route switching to Codex | 41.345 s |

The switch retry excludes the earlier quota failure and reset wait. A later
submission hit the relay's five-messages-per-ten-minutes sender limit before
starting inference. The driver waited for that window and resumed only the
pending turn. No completed scenario was repeated, and no rate guard was bypassed.

Claude's first three request counts matched saved counts: 33,440 / 37,637 / 40,338.
All six Claude turns matched counts, but fresh sessions saved a 200,000-token
window while resumed sessions saved 1,000,000. Captured CLI result metadata
explains a parser defect: a Haiku helper's 200,000-token entry precedes the primary
Opus entry reporting 1,000,000. The parser previously selected the first entry.
The reverse switch also retained Claude's stale 200,000 window on the old relay
while Codex's native record reported 258,400; candidate route invalidation already
has regression coverage but still needs deployed acceptance.

The Claude correction tracks the observed primary model within each stream,
ignores child-agent and synthetic model identities, and selects that model's
reported limit. Without an identity it accepts only valid, unanimous limits.
Missing, ambiguous or invalid metadata explicitly clears stale context state.
Model identity resets between streams; no raw prompt data is retained.
Twelve new failing regressions reproduced the issue before the fix; all fifteen
new cases and 227 existing focused tests then passed.

Fresh `make verify` passed after this source change: 6,207 tests passed, 12 skipped,
20 warnings; formatting, lint and type checks passed. Additional core type checks
and public imports passed. Security comparison found no new findings and retained
one pre-existing S101 assertion in the runner. Three captured mixed-model CLI
results also selected the primary model's reported window with the corrected parser.
Independent Claude Opus 4.8 review passed the code. Its patch-export path finding
was corrected in the final review artifact; an init-alias docstring was clarified.
Clearing prior readings on unresolved or failed turns is intentional: an old
measurement must not appear to describe the current turn.

The private deployment helper now checks the exact candidate wheel's public
imports before stopping the healthy service. It records import timings and
periodic stack traces with a bounded 120-second deadline. Two new regressions
failed before the change; the three affected checks then passed. The updated
candidate wheel matched all 258 source package files and passed isolated imports
in 3.699 seconds while the old service remained running. The installed package
still matched all 257 rollback files and reported healthy. This does not explain
the original timeout or establish deployed acceptance. No redeployment occurred;
both release PRs remain drafts.

### Installed release and live acceptance

The operator authorized the corrected deployment. Runtime pin `97fcc68` selects
source `0267b68`; only the relay revision changed in the manifest and lockfile.
All 45 kit checks against the new candidate source passed, along with lock,
formatting, lint and type checks. Remote source CI passed on Python 3.12 and 3.13,
with package, container and CodeQL checks; runtime CI passed on Linux and macOS.

The supervised restart succeeded. An independent check matched all 258 installed
package files to the prepared wheel and verified the exact installed VCS revision.
The new process reported healthy with zero overdue notifications. Both selected
instance add-ons loaded; the duplicate legacy `/cd` add-on is no longer active.
The known-good rollback remains available. No other computer was deployed.

Three consecutive new Discord turns per provider matched native last-request
input counts, retained the same native session within each route, and delivered
the expected artifacts and final replies:

| Provider | Native and saved input counts | Saved window | Provenance |
| --- | --- | ---: | --- |
| Codex, `gpt-6-astra` | 30,716 / 33,640 / 37,488 | 258,400 | `codex_rollout` |
| Claude, `claude-opus-4-8` | 30,465 / 35,834 / 40,778 | 1,000,000 | `provider_usage` |

Codex's window also matched its native records on every turn. Claude's transcript
contains input/cache usage but no `contextWindow`; its window is the installed
parser's primary-model result, supported separately by captured CLI result
metadata and attribution regressions. Do not describe that as an independent
per-turn window comparison against Claude's transcript. All six context percentages
actually displayed in Discord matched the saved measurements.

The first Codex validation prompt led it to assume a nonempty relay secret was
required for catalog access. A clarification of this instance's configured
loopback access completed the lookup on the next turn. No credentials were passed
to the agent and authentication was not changed. Preserve this correction in
the results; it is not a zero-correction run. The original context measurement
was valid and was not repeated.

Canonical folder recovery passed through the installed `/cd` callback with a
test interaction adapter and real Discord objects, messages and session storage.
The catalog consistently rejected an out-of-root alias; explicitly supplying its
legitimate canonical path created a correctly bound idle thread and visible folder
notice. No model started or project file changed. The temporary test session was
closed afterward. This was programmatic callback verification, not a claimed
human slash-command click.

The installed `ContextNudger.hand_off` flow passed with actual Discord thread
objects, session/settings repositories and real model turns through the relay's
existing API. The source stayed open while the fresh turn waited for the relay's
sender limit. The fresh turn preserved the accepted choice, constraint, nonce,
folder, `gpt-6-astra` model and `high` effort, used a new native session, and
delivered its result. Only then did the installed close method archive the source
test thread and remove its binding. Fresh context also matched native usage.
This was a programmatic invocation of the installed continuation callback, not a
human button click; button presentation and dispatch retain the existing tested
coverage. The original operator conversation was not closed.

The fresh test thread then explicitly switched to `claude-opus-4-8`. The runtime
control reports that changes apply to the next turn: it preserves the previous
turn's reading until the new native session binds, at which point all old context
fields were observed clearing. The new Claude session retained the task and
folder, delivered its final result, and saved exactly 34,156 native input/cache
tokens with a 1,000,000-token primary-model window and `provider_usage` provenance.

The six old-version baseline scenarios were not repeated. No overall speed or
cost improvement is claimed from these individual measurements. Other machine
settings and channels were preserved; the private environment changed only the
selected add-on directory. Source and runtime changes are pushed. Their PRs remain
drafts to avoid invoking repository-wide merge/upgrade automation beyond this
scoped iMac deployment. No additional deployment is needed for this documentation.
