# Async verification gate — September 28, 2026

Local candidate only. No service restart, live record change, paid backend, new
dependency, or global hook. This addresses REL-04 / stabilization task 5.4, not
the entire Jester/EBI release.

## Reproduction and repair

The old gate returned success even when a test spawned an unhandled task failure.
Child pytest probes reproduced five false-success cases: dropped task, retained
task, event-loop callback, unawaited coroutine, and asynchronous fixture teardown.
The reproducer failed 5 cases while 2 expected-error controls passed (7.66s).

`tests/conftest.py` now retains tasks created during a test until teardown, checks
asyncio's unretrieved-exception marker, and records loop exception-handler calls
without replacing the original handler's behavior. Awaited/retrieved failures and
cancelled tasks are not treated as unhandled failures. Garbage collection happens
while pytest's warning/unraisable hooks are active, exposing delayed mock cycles.
`pyproject.toml` makes unawaited-coroutine, unraisable-exception and unhandled-thread
warnings fatal. No broad warning suppression was added.

The gate itself has nine subprocess probes, including a shutdown-cancellation
failure and three controls for correctly handled errors/cancellation. These run a
temporary test module with the real conftest and warning policy, no agent backend.
Task marker `_log_traceback` is private asyncio state; the probes protect this
choice on supported Python upgrades. Current checked interpreter is Python 3.13.12;
this run does not claim to test other interpreters or alternate event-loop engines.

## Attributable fixture faults

- Setup tests awaited a synchronous `bot.wait_until_ready` MagicMock. It is now
  AsyncMock, matching the real bot method; production CollisionWatch is unchanged.
- Upgrade/webhook and slash tests mocked `wait_for` without consuming the passed
  `communicate()` coroutine. They now use real wait_for around the fake process.
- Runner timeout tests abandoned `wait()` the same way. Fake process waits now
  raise the expected timeout through a real wait_for; the kill test's second wait
  returns normally. A fake stdin now has synchronous write and asynchronous drain.
- The question-timeout test now expires a real queue wait with a zero deadline,
  allowing asyncio to cancel it instead of dropping its coroutine.
- One restart-approval assertion previously reset its mock after the operation.
  The mock is now installed before the operation, so the assertion means something.

## Verification evidence

- Initial strict focused run: 177 passed, 1 failed, 32 teardown errors.
- After the first fixture repairs: 185 passed in 22.71s, no warnings.
- First strict parallel full run: format/lint/types passed; 6,160 passed,
  6 teardown errors, 5 warnings in 449.39s. The six errors identified the slash
  and question-timeout fixture faults above; this was a FAILED gate, not success.
- After all identified fixture repairs, seven-file focused run: 229 passed in
  30.41s, no warnings. All nine detector probes passed.
- Final `make verify`: format/lint/pyright passed; **6,160 passed, 5 warnings,
  zero errors in 507.62s**, exit 0. No unexplained asynchronous failure appeared.
  This checks the candidate tree (including the separately reviewed identity
  slice), not the live deployment or every cross-boundary product requirement.

The remaining five warnings in both full runs were an intentionally duplicated
ZIP member in an ingest test and four discord.py `re.sub` argument deprecations.
They are identified non-async warnings. Full per-test collection adds cost:
about 7.5–8.5 minutes versus the prior two-minute gate.
Keep focused checks first; do not recover speed by silently ignoring failures.

## Scoped security review

Only test/configuration code changes in this slice. Ruff's optional security scan
flags the child `subprocess.run` (S603) and two test assertions (S101). Manual review:
the child uses an argv list, the current Python executable, fixed pytest options
and owned temporary paths, with no shell or external user input; assertions are
the test's intended checks. These are not vulnerabilities. No credentials are
printed and plugin autoload is disabled for the child. Public package imports and
`git diff --check` passed. This is not a repository-wide security audit, and it
does not approve the other uncommitted lifecycle/recovery changes.
