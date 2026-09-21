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
