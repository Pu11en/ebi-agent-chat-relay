# Scenario matrix — September 29, 2026 (task 6.1)

Run: `scripts/scenario-matrix.sh` on candidate `fix/harness-round-2-20260928`
after commit "Never give an internal worker a user tag…". Offline only:
temporary SQLite/Git, fake Discord/backends, zero model or worker calls.
Full gate on the same tree: `make verify` **6,228 passed, five known warnings,
zero errors in 556.81s**.

| Design sequence / injected fault | Tests (see script for IDs) | Result | Injected fault |
| --- | --- | --- | --- |
| Backend A → B → next reply → process reload | handoff reads A's transcript once, B's SYSTEM/RESULT bind `claude-b`, new repository + new cog resume natively | 1 passed | process reconstruction |
| Bad resume error / stale result after rebind | error echoes (Codex/unknown backend), echo after init, late success from an evicted run, legitimate fork control | 5 passed | rejected resume; late result |
| Close → Discord failure → restart → retry | archive recovery (6), whole build → archive failure → new repos/services/cogs, restart reconciliation | 12 passed | archive `False`/exception; reconstruction; reopen races |
| Worker spawn concurrent with allocation | create event before registration; exclusion write failure | 2 passed | settings write failure |
| Eligible completion → DB/Discord failure → retry | storage failure, tag-exclusion failure, thread-count summary, ledger eligibility (incl. unaccepted/unknown), legacy keep/throw-away | 12 passed | `mark_closed` failure; settings failure; Discord 503 |
| Waiting / closed / ambiguous loop → repeated restart | closed build not resumed; saved waits; legacy finished/unfinished; three consecutive restarts | 6 passed | repeated reconstruction |
| All tags used by legitimate users | remainder untagged; no stealing; diagnostic does not flag exhaustion | 3 passed | pool exhaustion |
| Snapshot excludes stale holder | `limit=1` page, off-page closed holder, stale closed view after reopen | 4 passed | pagination |
| Assertions pass but a task crashes | child-pytest probes for dropped/retained task crashes, callbacks, unawaited coroutines, teardown | 9 passed | deliberate crashes |

Boundary sweep coverage outside the rows: explicit reopen and a provider switch
while closed (`tests/test_lifecycle_wiring.py::TestRestartAndReopen`, archive
reopen races), cancellation (gate probes allow awaited cancellation), restart
during existing manifest work (`test_a_restart_keeps_finished_work_and_never_reruns_or_reassigns`).

Remaining gaps, stated rather than implied:

- Duplicate Discord delivery of the same user message is not exercised as a
  sequence here.
- Cross-process ordering (two bot processes) and direct repository writers
  outside the lifecycle service are not covered; the per-thread lock is
  in-process only.
- The first writer of the original REL-01 pair is still unknown.
- Newly recorded, not changed: `create_work_copy` names copies by wall-clock
  second, so two builds of one plan started within the same second collide on
  the branch name. `test_two_builds_for_one_project_integrate_in_turn` failed
  once under heavy parallel load on WSL (branch already exists) and passed 3/3
  in isolation; the fix needs the `LoopRecord` branch validation regex changed
  too, so it was left for a separate reviewed slice.
- Everything here is offline. Live Discord behavior, the microphone path and
  latency need the separately approved activation and trial.
