Check: .venv/bin/python -m pytest tests/test_project_launcher.py -q --no-cov

- [x] Add and check personal favorite storage, folder-bound thread creation, and same-thread resume.
- [x] Add and check persistent buttons, authorization, automatic setup, and reconnect behavior.
- [x] Run quality checks and stage local activation; deliver Windows installation instructions.

## How to try it
1. Open the computer's launcher and add a favorite folder.
2. Choose New session and confirm its welcome names that folder.
3. Choose Resume and confirm it opens the original thread.

## Validation and activation
- 16 launcher tests pass (84% module coverage), including per-user persistence, missing folders, operator revocation, private/deleted threads, duplicate clicks, restart registration and panel recreation.
- Clean source snapshot: 3,091 tests pass; the known aggregate-hanging ingest test passes separately (1 test), covering all 3,092 collected tests across two runs.
- Lint, formatting, type checking, import checks and security scan of the new Cog pass. Repository-wide security scan still reports pre-existing findings in untouched code.
- The ordinary development environment additionally exposes its known import-hook test contamination and an unrelated untracked folder-picker archive-policy violation; the clean snapshot includes neither artifact.
- Actual Discord button clicks and David's Windows runtime require post-activation checks. The Linux activation helper waits for all turns to be idle and reports the verified panel link automatically.
