# Session and recovery boundary audit — September 29, 2026 (task 1.3)

Candidate `fix/harness-round-2-20260928`. "Confirmed" means a failing offline test
reproduced it before the repair; "reading" means established from code only;
"hazard" is unproven and deliberately not changed. No live state was modified.

| Boundary | Finding | Evidence / confidence | Impact | Status and test |
| --- | --- | --- | --- | --- |
| Event / error | Rejected-resume error echo persisted with the current runner's backend | Confirmed | Produces REL-01's `(claude, old ID)` pair | Fixed `bd518f5`; `test_rejected_resume_cannot_claim_another_backends_id` |
| Event / result | Late success from an evicted run replaced a newer binding | Confirmed | Undoes a provider switch | Fixed `b5c49f5`; `test_late_result_from_an_evicted_run_cannot_replace_a_newer_binding` |
| Event / attribution | `_backend_name_from_runner` falls back to `"claude"` for any unrecognized runner class | Reading, high | A new wrapper around a non-Claude runner would mislabel its IDs | Hazard; no test (no such wrapper exists today) |
| Repository | `save()` keeps the stored backend when a writer omits it (`COALESCE`) | Reading, high | Any backend-less writer pairs a new ID with an old backend | Mitigated by the two fixes above; remaining backend-less writers only write `""` or new import rows |
| API | `/api/sessions` listing can allocate tags; a paged listing reclaimed off-page holders | Confirmed (paging) | Stolen live tags | Fixed `8406035`; diagnostics now avoid the endpoint (`3171054`) |
| API / spawn | Empty auto-start spawn created a thread before rejecting | Confirmed | Orphan thread | Fixed `6fe2442`; `test_automatic_spawn_without_prompt_creates_no_thread` |
| Scheduler | Follow-up ran a model turn in a closed session (and its notice would unarchive it) | Confirmed | Breaks the close contract; spend | Fixed (this batch); `test_follow_up_never_starts_a_turn_in_a_closed_session` |
| Scheduler | Follow-up resumed a stored ID the current backend cannot resume | Confirmed | Same failure class as REL-01 | Fixed (this batch) by starting fresh; `test_follow_up_does_not_resume_another_backends_id`. No transcript handoff on this path (limit). |
| Scheduler | Follow-up runs outside the chat cog's per-thread lock | Reading | A follow-up and a user turn could run concurrently in one thread | Hazard; not changed. No scheduler activity appears in the live log. |
| Import (`session_sync`) | Imported CLI sessions are saved without a backend | Reading | `None` backend is treated as compatible; a non-Claude current backend would try to resume and fail | Hazard; low (new threads only) |
| Queue | Human replies reload the stored record under the thread lock before launching | Reading, high | Queued replies do not carry a stale ID across a switch | Safe; covered by the A→B→reload sequence test |
| Close / archive | Failed archive forgotten after reconstruction; stale wrap-up could close a reopened row | Confirmed | Stuck/incorrect lifecycle | Fixed `8021f53`; `tests/test_archive_recovery.py` |
| Close / archive | Ordering lock is per process | Reading | Two bot processes could interleave archive/unarchive | Hazard; single-process deployment assumed |
| Worker closure | Worker archive failure was a crash and never retried; legacy workers closed before acceptance | Confirmed | Lost archives; premature closure | Fixed `cbd42d9`, `48f6466` |
| Worker tags | Failed exclusion write let an orphan worker take a user tag | Confirmed | Tag pool loss | Fixed `1cb3d74` |
| Restart / loops | Saved closed build resumed; legacy waits re-ran paid checks | Confirmed | Unauthorized spend | Fixed `8021f53`, `48f6466`. Live main still has the hazard (see activation checklist). |
| Restart / chat | Restart-resume marker reads the stored record at shutdown | Reading | No stale in-memory ID | Safe |
| Delivery | No message-ID deduplication in `on_message` | Reading | A duplicated gateway event would run two turns | Hazard; no evidence of duplicates |
| Work copies | Two copies in one second collided on the branch name | Confirmed | Build start failure | Fixed `01e55e5` |
| Diagnostics | Health could not prove the running revision; live check wrote through `/api/sessions` | Confirmed | Unverifiable deploys | Fixed `3f146aa`, `949873f`, `3171054` |

## Candidate review against the contracts (task 1.4)

- Legacy worker deletion: removed on all successful paths; explicit "throw it
  away" keeps its requested deletion of the build thread and copy (accept).
- Legacy loop recovery: closed builds are not resumed; ambiguous legacy records
  wait (accept, `48f6466`).
- Scope: besides the repairs above, the candidate carries the async failure gate
  and pytest warning policy (`582ab45`) and `17c65de` from the earlier session:
  stall warnings once per turn, and restart/upgrade resume prompts that tell the
  agent to continue the already-authorized task instead of asking the user to
  reconfirm. That last one is a deliberate policy change, in the handoff's stated
  scope ("authority-preserving recovery prompts") but not a bug fix; Drew should
  approve it knowingly at activation. No other unrelated work was found in
  `git diff main...HEAD`. EBI commits `726ecaf` and `48f67d5` reviewed (accept;
  one defect fixed in `6fe2442`).

## Gate record for this batch

Scheduler fix: RED first (2 failed). Scheduler suites **63 passed**. First full
gate had one load-sensitive failure (`TestGoalNotMet::test_after_three_rounds_it_stops_and_asks`:
the fake worker's `git commit` found nothing to commit, then its teardown reported
the crashed task). It passed 5/5 alone and the immediate rerun of the full gate —
with no other work running — passed: **6,232 passed, five known warnings, zero
errors in 543.41s**. Recorded as a second fixture flake under load, not fixed.

## Adversarial review pass over the integration (task 6.2)

Each attack below was checked against the code; none produced a new defect beyond
those already fixed or recorded above.

- **Stale SYSTEM event** (not covered by the result compare-and-set): the chat
  path's `_evict_active_run` awaits the previous run's task in both queue and
  interrupt modes, so an older run cannot emit SYSTEM after a newer one binds.
  Paths outside that lock (scheduler follow-ups, direct API turns) remain the
  recorded concurrency hazard.
- **Workflow no-lock bypass**: only `workflow_close_on_done` rows archive
  unlocked, and a later user close rewrites `close_authority` through
  `request_close`, so a person's close still locks. A pending workflow close
  completed by chat finalization stays unlocked (tested).
- **Ledger truthfulness**: `_close_worker_thread` reports done only when the
  stored close exists, the tag exclusion was written and no archive is pending;
  every failure path returns False, so the ledger keeps the worker owed.
- **Legacy restoration**: the restored record is saved with `checkpoints=True`
  before launch, so a crash during launch cannot re-trigger spending; manifest
  builds keep the ledger path; invalid waits park (tested).
- **Diagnostic writes**: the inspector never opens a writable connection; with
  no WAL it opens immutable (a concurrent writer can at worst make the read fail,
  which is reported as unavailable); with a WAL it opens `mode=ro`.
- **Health exposure**: commit, pid and start time only; no path, environment or
  secret. Git runs with fixed argv, `--no-optional-locks`, 2-second timeout.
- **Security-audit checklist** (`.agents/skills/security-audit/SKILL.md`): no new
  `shell=True`, no user text in argv, no new environment variables passed to
  model subprocesses, secrets only sent as the configured API bearer header by the
  operator's live-check script and never printed; Ruff S counts unchanged per
  batch except the documented, suppressed S603/S608 constants.

Gate after the pass: `make verify` **6,233 passed, five known warnings, zero
errors in 533.93s**; no unexplained asynchronous failures. Two load-only fixture
flakes observed during the day are recorded above and in the scenario matrix.
