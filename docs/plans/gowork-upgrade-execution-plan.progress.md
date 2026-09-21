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
