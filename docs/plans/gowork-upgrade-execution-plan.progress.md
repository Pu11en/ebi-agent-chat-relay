# Go Work Upgrade Progress

## T01 — Stable master and child plan identities

- Added a versioned plan-tree manifest with stable IDs, parent links and canonical project paths.
- Preserved legacy checkbox plans through a deterministic single-plan compatibility path.
- Added round-trip fixtures plus clear failures for duplicate IDs, unknown parents and parent cycles.
- Implementation commit: `181dc76`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (289 passed), focused lint,
  formatting, pyright, import and security checks, and the full project suite (3462 passed after
  removing three live Discord routing variables from the test environment).
- Open: nothing for T01; T02 can build complete task assignments on this identity model.

## T02 — Complete worker task assignments

- Added a complete task contract with stable task and plan/version identities, dependencies,
  file and resource ownership, required inputs, expected output, acceptance check and source
  requirement.
- Added manifest parsing/rendering, task lookup helpers and clear rejection of incomplete or
  duplicate task definitions and assignments tied to the wrong plan version.
- Preserved old checkbox plans by deriving deterministic task records and serializing them on
  one shared legacy resource; the old format is never treated as safely parallel.
- Implementation commit: `bbf51be`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (304 passed), focused lint,
  formatting, pyright, import and security checks, and the full project suite (3477 passed after
  removing the three live Discord routing variables from the test environment).
- Open: the repository-wide Ruff security selection still reports 62 pre-existing findings;
  the touched parser passes its focused security check. T03 can now validate dependency and
  ownership safety without changing this assignment format.

## T03 — Validate dependencies and ownership

- Finished the validation started in `b9773d3` (David's computer, 2026-09-21): unknown, self
  and duplicate dependencies, dependency cycles (the cycle is named), unsafe owned paths
  (absolute, drive, `~`, `..`, `.git`, control characters, backslashes), cross-project
  dependencies that name no required input, duplicate/unknown/uncovered agreed outcomes.
- Overlapping ownership is a pairwise `OwnershipConflict` (`PlanTree.ownership_conflicts()`,
  `can_run_together()`), so a conflict blocks only that pair's simultaneous dispatch, never
  the whole plan; owned paths are compared per project and patterns compare by literal prefix.
- Legacy checkbox plans stay valid without declared outcomes.
- Tests: `tests/gowork_upgrade/test_plan_validation.py` (24 cases; the parametrised invalid
  fixtures each assert their specific message), fixture `validated-plan.md`.
- Implementation commits: `b9773d3` (code + tests, on the base) — no further code change was
  needed; this entry records the verification the interrupted step never wrote.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (329 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/gowork_plan.py` (0 errors).
- Open: nothing for T03. T04 can key LoopStore by stable build identity.
