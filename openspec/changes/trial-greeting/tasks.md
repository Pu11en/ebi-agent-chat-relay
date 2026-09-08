## 1. Focused Tests FIRST — Greeting Builder

These are future execution tasks; none has been performed by the planning owner. Prerequisite: the lead supplies the fixed plan revision and an isolated builder worktree based on the agreed foundation. The builder may write only `examples/feature_workflow_trial/greeting.py` and `examples/feature_workflow_trial/test_greeting.py`. It reads this plan and reports task IDs in its Ebi handoff without editing these artifacts.

Set `TRIAL_ROOT=/home/drewp/main-projects/wt-$DISCORD_THREAD_ID` in the builder session so commands address that builder's absolute worktree path. Use Python 3.12+.

- [ ] 1.1 Write standard-library `unittest` cases in `examples/feature_workflow_trial/test_greeting.py` for every scenario in `specs/trial-greeting/spec.md`: exact ordinary greeting, preserved mixed case, exterior whitespace trimming, preserved interior spaces/tab, empty string fallback, and whitespace-only fallback. Import the real `greet` from `greeting`; verify six scenario cases assert complete expected strings without mocks.
- [ ] 1.2 Before creating `greeting.py`, run `python3 -m unittest discover -s "$TRIAL_ROOT/examples/feature_workflow_trial" -p test_greeting.py -v`; retain the command, nonzero exit, and missing-greeting-import failure in the builder handoff as RED evidence. Resolve unrelated test setup failures before treating this step as complete.

## 2. Build — Greeting Builder

Depends on 1.1 and 1.2.

- [ ] 2.1 Create `examples/feature_workflow_trial/greeting.py` exporting `greet(name: str) -> str`, following `design.md` and the fixed decision: Preserve case. Strip exterior whitespace only. Empty name becomes friend. Return exactly `Hello, <name>!`. Verify the focused discovery command from 1.2 now exits zero with all six scenario cases passing; retain GREEN evidence.

## 3. Verification and Handoff — Greeting Builder

Depends on 2.1. These checks cover the module and its tests together, including the ownership boundary.

- [ ] 3.1 Run the repository's existing Ruff lint and format checks and Pyright against only the two owned files, using the builder's absolute worktree paths; verify zero exit statuses without adding dependencies or editing shared configuration. Run `git -C "$TRIAL_ROOT" diff --check` and inspect `git -C "$TRIAL_ROOT" status --short`; verify all builder changes are confined to the assigned module and test file. Apply any needed fixes within those files and rerun affected checks and the focused test command after fixes.
- [ ] 3.2 Commit only the two owned files and push the builder's session branch to `voice-backup`. Verify the remote branch resolves to the reported commit and deliver the commit SHA, plan revision, completed task IDs, exact decision, RED/GREEN evidence, verification results, builder thread ID, and absolute worktree in the Ebi result for lead collection.

## 4. Integration — Lead Only

Depends on the verified builder handoff in 3.2 and lead-controlled integration readiness. This step grants the greeting builder no additional file ownership.

- [ ] 4.1 Lead thread `1546685954167672912` integrates the greeting builder's commit and verifies, through the lead-owned CLI and integration tests, that the `greeting` result uses the real `greet` function and retains the specified behavior for mixed-case, exterior-whitespace, and blank names. Completion evidence is the integration commit and passing lead-owned checks; all shared-file edits and integration decisions belong to the lead.
