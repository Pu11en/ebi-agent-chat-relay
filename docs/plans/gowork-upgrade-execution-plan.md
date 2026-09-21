# Go Work Upgrade: Fresh-Session Execution Plan

Check: uv run python scripts/check_gowork_upgrade.py
Try: uv run python scripts/check_gowork_upgrade.py
Goal: Preserve Drew's detailed, question-led planning while Go Work executes several projects with resource-aware workers, explicit dependencies, recoverable results and automatic local completion.
Done when: All tasks below pass offline regression checks and the final simulated practice run proves multi-project dependencies, adaptive capacity above ten, bounded repair, direct-reply blockers, restart recovery, changed requirements and automatic completion without a looks-good gate.

## Authority and Bootstrap Rules

Drew selected Go Work with fresh sessions to implement this upgrade. His decisions in
`.planning/gowork-flow/task_plan.md` section 1 and `prompt-refinement.md` are requirements,
not questions to reopen. This execution plan replaces the conceptual checkbox list for
dispatch only; retain that document's full scope. No GitHub publishing or deployment.

This build uses the OLD loop runner to upgrade itself. Run the following chain in order:
every title names its prerequisite, and every task owns the same execution spine.
Do not group these upgrade tasks in parallel. This conservative bootstrap order does not
limit the parallelism of the finished product. Before making changes, each fresh worker
must verify the previous checkbox is checked and its implementation exists; if not,
report the unmet prerequisite instead of guessing or building a competing foundation.
The old grouping helper sees only task titles, which is why prerequisites appear there.

One checkbox means one outcome, about 15-30 minutes. Split an oversized task into smaller
ordered checkboxes in THIS plan before building it; retain its scope and downstream
prerequisites. Do not start a second coordinator, extra private worker swarm or nested
Go Work launch. Do not mark a checkbox complete for documentation or stubs alone.

For every task: inspect current implementation and local instructions, add a failing
focused test, implement, run its proof plus Check, and commit locally. Keep new offline
tests under `tests/gowork_upgrade/` or `tests/test_gowork_*.py` so Check discovers them.
Run ruff/format and relevant type checks for touched code. Use existing repository APIs;
do not build a parallel orchestration framework. Compatibility includes old plan files,
stored runs, queue/model choices, cancellation and API consumers.

All verification fixtures use fake workers/providers/Discord, temporary databases and
temporary git repositories. Never stress this host, spend on live evaluation calls,
restart the shared bot, edit its live database, or install global instructions during
this isolated build. Commit a tested opt-in local installation/activation procedure;
activation must not interrupt other sessions. The current running bot will not acquire
new code merely because this build edits it. Report that boundary honestly.

## Context and Research to Reuse

- Current code: `claude_code_core/task_loop.py`, `loop_store.py`, `work_copy.py`,
  `gowork_records.py`, `claude_discord/cogs/task_loop.py`, `cogs/_run_helper.py`,
  `setup.py`, and `ext/api_server.py`.
- Current limitations: fixed cap ten; grouping receives labels; LoopStore keys by
  repository; batch gather delays worker cleanup; review errors may become no objection;
  channel-wide waiters consume replies; finalization waits for looks good and deletes threads.
- Existing useful parts: separate work copies, checks of combined work, model selection,
  balanced review modes, restart handling, queue and records. Extend them.
- Saved research: `.planning/gowork-flow/findings.md`, `planner-audit.md`,
  `prompt-refinement.md`, and `local-plans/gowork-oss-patterns.md`.
- Preserve One Question and the existing planner. The wholesale replacement skill under
  `planner-skill-draft/` is NOT accepted or installed. Reuse selected prompt ideas only.
- Superpowers: intent and small concrete assignments; GSD: saved decisions, task
  needs/creates, ownership and coverage; BMad: readiness without invented decisions.
  Inspected revisions and MIT notices are saved in the audit/draft references. Inspect
  licenses before copying actual source; do not run upstream installers or hooks.
- Inspect the current handoff contract before T12; another branch may have newer work.
  Use read-only git/API inspection and a narrow adapter if needed, not a wholesale merge
  of another session's branch or a second incompatible messaging format.

## Build Chain

- [x] T01: Execution spine, first: load stable master and child plan identities.
  Scope: core plan parsing/models; old single-plan files remain valid.
  Add IDs, parent links, canonical project paths and versions; allow multiple projects
  under one master and nested child plans without assuming an atomic cross-repo merge.
  Proof: legacy fixture and multi-project tree round-trip; duplicate IDs and parent cycles fail.

- [x] T02: Execution spine, only after T01: define complete worker task assignments.
  Scope: T01 models and parser. Add stable task ID, plan version, dependencies, owned
  files/resources, required inputs, output, acceptance check and source goal/requirement.
  Proof: complete fixtures round-trip; missing fields/duplicate task IDs fail clearly;
  legacy checkboxes retain a conservative supported path, never invented parallel safety.

- [ ] T03: Execution spine, only after T02: validate dependencies and ownership.
  Scope: plan validation. Reject missing references/cycles, unsafe paths and invalid
  cross-project requirements. Overlapping ownership prevents simultaneous dispatch,
  not necessarily the whole plan. Require coverage of agreed outcomes.
  Proof: valid independent work passes; each invalid fixture explains the specific issue.

- [ ] T04: Execution spine, only after T03: store multiple builds without overwriting runs.
  Scope: LoopStore and existing persistence adapters. Use stable build identity instead
  of repo-only identity; preserve legacy records, lookup compatibility and atomic writes.
  Proof: two plans for one repo survive reopen; migration is repeatable and loses no run.

- [ ] T05: Execution spine, only after T04: persist task attempts and acceptance evidence.
  Scope: durable task state. Record ownership, assignment/version, attempt identity,
  result commit, checks/review, repair usage and accepted versus merely finished results.
  Proof: reopen preserves state; duplicate result submission is idempotent; stale attempt
  cannot overwrite an accepted current result; interrupted writes leave recoverable state.

- [ ] T06: Execution spine, only after T05: select ready tasks across child plans.
  Scope: deterministic core scheduler. Only accepted current-version prerequisites
  release dependent work; shared ownership excludes overlapping tasks.
  Proof: website waits for product input while independent marketing proceeds; a failed,
  unfinished or stale dependency never releases children, including across repositories.

- [ ] T07: Execution spine, only after T06: sample host and worker resource pressure.
  Scope: read-only resource adapter, using established dependencies or a justified small
  adapter. Measure available memory, CPU, swap and disk; respect container/host limits;
  account for worker descendants such as tests/browsers and observed resource peaks.
  Proof: injectable fake snapshots cover healthy, constrained and unavailable data;
  missing/partial observations are explicit and conservative, not unlimited capacity.

- [ ] T08: Execution spine, only after T07: arbitrate shared capacity with fair admission.
  Scope: one admission controller used across builds. Atomically reserve/release slots
  for tasks and reviews, account for chat, reserve OS/chat/coordination headroom; prefer
  tasks unblocking work but age waiting builds so none starves. Persist fairness state.
  Proof: racing requests do not double-book, cancellation releases reservations, reviews
  cannot deadlock behind workers, and fairness survives reopen.

- [ ] T09: Execution spine, only after T08: adapt admissions to measured resources.
  Scope: controller policy. Conservative start, bounded growth from observed peaks,
  pressure backoff, cooldown recovery, protective ceiling and missing-data fallback.
  Keep explicit provider/operator constraints separate. Critical pressure has a defined
  bounded cancellation/recovery path, not a machine-crash experiment.
  Proof: simulated healthy capacity admits more than ten useful workers; pressure pauses
  starts, recovery avoids oscillation, and configured external limits still apply.

- [ ] T10: Execution spine, only after T09: connect adaptive capacity to actual process starts.
  Scope: `_run_helper.py`, setup defaults and task-loop cap integration. Replace both
  fixed bottlenecks on the adaptive path; all relevant task/review/chat starts participate
  without acquiring the same reservation twice. Preserve explicit existing overrides.
  Proof: fake process integration exceeds ten only when admitted, backs off under pressure,
  cleans cancellation paths, and normal chat remains responsive without a worker slot.

- [ ] T11: Execution spine, only after T10: dispatch ready work through the existing coordinator.
  Scope: core loop plus Discord/API adapters. Connect multi-build storage, readiness,
  fairness and admissions; replace repo-only running identity and incompatible switch
  behavior for the new multi-plan path while retaining legacy single-build compatibility.
  Proof: two plans in one planning thread and two projects progress independently;
  repeated start requests cannot dispatch duplicate attempts or close an unrelated build.

- [ ] T12: Execution spine, only after T11: issue compact persisted worker handoffs.
  Scope: existing handoff contract adapter and worker prompt. Supply only assignment,
  goal, saved decisions, required input evidence and expected result, not all chat history.
  Proof: duplicate delivery uses the same attempt; worker gets complete input/ownership;
  current handoff contract inspection is documented and no competing format is invented.

- [ ] T13: Execution spine, only after T12: save and release workers individually.
  Scope: replace batch-only result processing with per-worker completion handling.
  Persist results before cleanup, release execution capacity promptly on success/failure,
  handle exceptions and cancellation, retain uncombined work and ownership correctly.
  Proof: a fast worker stops while its sibling continues; crash/exception cannot discard
  its saved commit or leave an execution reservation permanently occupied.

- [ ] T14: Execution spine, only after T13: archive completed worker threads without deleting history.
  Scope: Discord worker cleanup after durable result save. Archive immediately, regardless
  of sibling completion; never archive the master planning thread. Retry cleanup safely.
  Proof: result-save precedes archive; archive failure retains work and releases process
  capacity; restart retries archive without rerunning completed work or deleting messages.

- [ ] T15: Execution spine, only after T14: serialize task-result combination in review copies.
  Scope: work_copy integration and durable integration state. Combine commits per project
  in controlled order; run combined checks before accepting results for downstream tasks.
  Proof: concurrent results serialize, combined failure blocks dependents, conflict keeps
  both versions for repair, and crash after merge can be reconciled without merging twice.

- [ ] T16: Execution spine, only after T15: require real check and review evidence.
  Scope: existing verification/reviewer adapters. Every task has tests/checks; balanced
  adds independent AI review for difficult changes; retain explicit cheap/careful modes.
  Missing/unavailable/failed required review is pending or blocked, never approval.
  Proof: ordinary balanced task needs no mandatory extra review; difficult task cannot
  accept without evidence; failed checks/reviewer exceptions cannot produce success.

- [ ] T17: Execution spine, only after T16: reconcile interrupted work without duplication.
  Scope: startup recovery. Compare durable attempts with worker/process/commit state;
  recover abandoned reservations, saved results and integration/cleanup operations.
  Proof: restart before dispatch, during execution, after result save and after merge
  preserves completed work; uncertain still-running ownership is not blindly reassigned.

- [ ] T18: Execution spine, only after T17: enforce exactly one automatic repair attempt.
  Scope: retries, smart_unstick, model escalation, splitting and goal-auto-round paths.
  Original failure gets one repair; durable lineage keeps the budget across restarts,
  splitting or model changes. Independent work continues; exhausted tasks become blockers.
  Proof: every failure entry point shares the budget and cannot create hidden repair loops.

- [ ] T19: Execution spine, only after T18: version mid-build requirement changes.
  Scope: plan synchronization and scheduler. Save new version immediately, finish current
  small task, then create fresh user-directed rework before integrating changed outputs.
  Preserve old work; hold affected dependents and keep unrelated work moving.
  Proof: edits/restart cannot accept stale results; user-requested rework is distinct from
  failure repair and cannot reset the existing attempt's repair allowance.

- [ ] T20: Execution spine, only after T19: persist blocker messages and question identity.
  Scope: durable blocker ledger and posting adapter. Map planning message ID to build,
  task, attempt/version, owner/channel, question and unresolved/resolved status.
  Proof: several blockers in one thread survive reopen; repeated posting/recovery does
  not lose actionable questions or attach them to the wrong build.

- [ ] T21: Execution spine, only after T20: route actual Discord Replies to their blocker.
  Scope: message listener/waiter integration, using message.reference.message_id plus
  authorized-user/channel checks. Ordinary conversation stays with normal chat.
  Proof: two replies resolve only their respective current blockers; duplicates, stale,
  resolved, unknown and unauthorized references cannot dispatch work; resolving an answer
  resumes only eligible affected tasks under normal checks and capacity.

- [ ] T22: Execution spine, only after T21: report concise context-rich outcomes.
  Scope: progress/blocker/completion rendering from saved evidence. Name project/goal,
  what changed or failed and why it matters; short what-and-why completion bullets plus
  checks, workarounds and limitations. One actionable decision per blocker message.
  Proof: fixtures understandable without chat history; all-blocked lists issues, not
  success; no invented results, unexplained technical labels or routine full-plan cards.

- [ ] T23: Execution spine, only after T22: automatically integrate each checked local build.
  Scope: work_copy final integration and per-project integration lock. Combine completed
  builds locally while others continue; preserve existing tracked/untracked edits, check
  the exact combined result, and block conflicts instead of forcing overwrite.
  Proof: two same-repo builds serialize; dirty source remains intact; failed check/merge
  preserves private work and enters bounded repair; no push, PR or deploy side effect.

- [ ] T24: Execution spine, only after T23: remove success approval waiters and reminders.
  Scope: wrap-up lifecycle. Successful verified local integration ends automatically,
  posts recap and releases coordinator/worker resources; no looks-good gate. Terminal
  blocked runs retain recoverable records and actionable questions without idle workers.
  Proof: completion without user reply; unrelated builds continue; duplicate finish/restart
  cannot repost completion, reintegrate or reopen workers; user stop still means stop.

- [ ] T25: Execution spine, only after T24: record reproducible workflow friction.
  Scope: existing gowork_records. Track queue/dependency waits, repairs, review outcomes,
  repeated owner questions and capacity decisions with task/build/version identity.
  Proof: fixture yields repeatable counts, no invented token/cost totals, and suggested
  improvements never silently rewrite the user's planning instructions.

- [ ] T26: Execution spine, only after T25: refine existing planning prompts without replacing the flow.
  Scope: focused repo-owned prompt/template resources, using prompt-refinement.md.
  Preserve known answers, technical fact gathering and detailed internal plans; short
  useful multiple-choice questions until required user answers are resolved, concrete
  examples when unclear, no repeated planning ritual or automatic card dump.
  Proof: static examples for existing report plan, multi-project business and resumed
  answer preserve scope/context; do not claim static examples measure live model behavior.

- [ ] T27: Execution spine, only after T26: export validated plans supported by the installed runner.
  Scope: planner templates/export adapter. Complete identities, requirements coverage,
  inputs/outputs, ownership/dependencies, checks and saved decisions. Include conservative
  compatibility handling for older runtime; reject unsupported behavior clearly.
  Proof: old checkbox plan works; new multi-project plan validates and schedules through
  the implemented parser; impossible ownership/dependency plan cannot start by accident.

- [ ] T28: Execution spine, only after T27: pass full task context to grouping and worker prompts.
  Scope: task block extraction and existing grouping integration, not labels alone.
  Include inputs/outputs/ownership/prerequisites and respect code-enforced readiness;
  group suggestions are advisory and cannot bypass dependency or capacity checks.
  Proof: same-title different-ownership fixtures show correct context; incomplete or
  malformed model grouping output safely falls back without losing tasks.

- [ ] T29: Execution spine, only after T28: package shared planner guidance for all harnesses.
  Scope: narrow supporting guidance plus idempotent local installation tool and tests.
  Reuse the canonical shared instruction/skill paths and existing One Question flow;
  do not fork per-harness copies or activate the rejected wholesale skill. Preserve user
  instructions, backups and unrelated edits; stage changes for inspection, not live install.
  Proof: temporary-home fixtures for Claude/Codex/DSH resolve the same guidance; install
  twice creates no duplication and rollback restores only owned content.

- [ ] T30: Execution spine, only after T29: evaluate planning and execution contracts offline.
  Scope: static regression fixtures from saved examples. New plan, resumed answers,
  tiny changes, multiple projects, ownership conflict, missing decisions and legacy
  runtime all retain correct scope and readiness; explicitly separate structural results
  from prompt quality/live-model claims. No extra paid evaluation agents.
  Proof: executable validation and rendered examples identify exact failures and coverage;
  copied upstream content retains required license notices.

- [ ] T31: Execution spine, only after T30: provide a repeatable offline Go Work practice command.
  Scope: simulated integration demo spanning actual coordinator/adapters with fake workers,
  machine readings and Discord. Two plans in multiple projects, dependency waits, >10
  adaptive admissions, fair turns, one repair, direct-reply blocker, restart, midbuild edit,
  archive and automatic checked completion. No real stress, provider calls or bot process.
  Proof: deterministic exit status/assertions fail if behavior regresses; short output names
  what/why and pending limitations. Update Try below to the actual standalone demo command.

- [ ] T32: Execution spine, only after T31: verify compatibility and document local activation.
  Scope: final cross-module regression and deployment/rollback notes, not deployment.
  Run Check, broader tests affected by integration, lint/format/type checks and the demo;
  resolve actual regressions within this task or explicit small follow-ups, never hide
  failures. Document backup/migration, shared-host coordination, instruction install,
  live activation and rollback steps. Do not restart another session's bot.
  Proof: saved commands/results distinguish implemented, checked and not-yet-live behavior;
  every approved requirement maps to tests and no pending behavior is called complete.

## How to Try It

- Now: Try runs existing offline Go Work tests; this is a baseline, not the new experience.
- Once T31 exists, run the updated Try command and look for independent work progressing
  while a task needing another result waits; simulated load changes the allowed worker count.
- Check that a finished worker closes before a slow sibling and its result/history remain.
- Check that one failed repair produces a specific question; only a direct reply resolves
  it, and successful work finishes without looks good. These three checks take about 30 seconds.

No website is part of this upgrade, so there is no localhost page. The demo must be offline
and finish promptly. Keep Check standalone and under two minutes; it must discover new
tests rather than repeatedly proving only the original implementation.
