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

## Category flow update
- [x] Show separate Favorites and Recent folders, with no computer-selection step.
- [x] Persist recent launcher folders per operator and guild; deduplicate favorites.
- [x] Offer Browse folders and return buttons without typing a path.
- [x] Update the self-contained handoff for David and iMac category mappings and idle activation.
- Validation: 3,095 tests pass in the clean snapshot; the unchanged known aggregate-hanging ingest test was excluded and previously passed alone. Four additional regressions cover ordering, persistence, recent selection and no-typing browse. Lint, format, type checks and new-Cog security checks pass.
- Previous local launcher activation was verified at commit 4ed013b; this update requires a later idle activation. David reports auth commit e72a851 is tested but not active. Windows/iMac launcher activation is not yet verified.


## Folder browser, fixed home and category scope
- [x] Add tests first for nested/outside-root browsing, pagination and explicit Start here.
- [x] Implement filesystem navigation and personal favorites without path typing.
- [x] Separate launcher home from the workers destination, retaining old defaults.
- [x] Add command/autocomplete/chat category checks without changing operator access.
- [x] Create the three category start-here channels and record their IDs.
- [x] Validate local code: 3110 full-suite passes plus the isolated ingest test; lint, format, pyright, security review clean for changed code.
- [x] Activate local code after the current turn ends and verify the new panel.
  - v4.1.0: activation on the Lenovo happens when Drew switches the bot to `release/v4.1.0` (HANDOFF-V4.1.0.md §8); the code is in this release and covered by `tests/test_project_launcher.py`.
- [x] Apply the self-contained upgrade on David and iMac and verify their buttons.
  - v4.1.0: David's bot is checked live in box E5 of `docs/plans/v4.1.0-finish-all-builds.plan.md`; the iMac is not part of this build and picks the release up the same way as the Lenovo.

Try: Open your category's start-here channel, New session, Browse folders, Start here.
The full filesystem browser and fixed home supersede the earlier shallow suggestion picker.
Native slash-menu visibility for administrators is a Discord limitation, not fixed here.


## Correction: control-center remains the entry point
- [x] Restore each computer’s home mapping to its existing control-center.
- [x] Restore DrewAI’s original live panel; remove unused extra channels created by this session.
- [x] Add tests first for a silent bottom shortcut, coalescing, own-message deletion and unload cleanup.
- [x] Keep workers as the destination for new session threads.
- [x] Activate bottom-shortcut code at the next idle window and verify it.
  - v4.1.0: same switch-over as above; the shortcut is covered by `tests/test_project_launcher.py::test_bottom_shortcut_replaces_only_its_previous_message`.
