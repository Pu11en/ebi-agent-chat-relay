## 1. Focused tests FIRST

These are future builder tasks; this planning session executes none of them. The lead assigns an isolated Ebi builder with this approved plan revision. `<builder-worktree>` below means that builder's absolute worktree path, never the main checkout or the planner's worktree. The builder's only writable project files are `examples/feature_workflow_trial/counter.py` and `examples/feature_workflow_trial/test_counter.py`; the plan remains owned by planning thread `1546687950375493752`.

- [ ] 1.1 In `examples/feature_workflow_trial/test_counter.py`, write standard-library `unittest` cases for all five scenarios in `specs/trial-word-count/spec.md`, including an integer-type assertion on the public result. Before creating `counter.py`, run `python3 -m unittest discover -s "<builder-worktree>/examples/feature_workflow_trial" -p test_counter.py -v`; verify and retain the expected failure caused by the missing counter module, not a test syntax error.

## 2. Build

- [ ] 2.1 After task 1.1's failure is recorded, create only `examples/feature_workflow_trial/counter.py`, exporting `count_words(text: str) -> int` with the retained decision: Split only on whitespace; punctuation remains part of each token. Empty input is zero. Use separator-free string splitting semantics without normalization, external dependencies, or I/O. Verify completion by rerunning the focused command and observing all five tests pass.

## 3. Verification and lead handoff

- [ ] 3.1 After task 2.1, review both owned files against the spec and repository Python conventions, including annotations and future annotations. Run the available repository Ruff tooling with `check` and `format --check` on those two absolute file paths; verify both checks pass and the tests assert each specified output independently of the implementation. Rerun focused tests if any verification fix changes either file.
- [ ] 3.2 Verify `git -C "<builder-worktree>" diff --check` passes and inspect the full worktree status and staged diff to confirm the feature commit contains only `examples/feature_workflow_trial/counter.py` and `examples/feature_workflow_trial/test_counter.py`. Commit and push the builder's session branch to `voice-backup`, then provide the exact commit SHA, branch, absolute worktree, retained decision, and failing/passing test evidence in the final Ebi result for root manager thread `1546685954167672912`. Verify the remote branch points at that commit and the owned files are clean.

Dependency order: 1.1 → 2.1 → 3.1 → 3.2 → lead integration. The feature has no dependency on another feature's implementation. The lead alone combines commits, wires the CLI, and verifies integration; those files and actions are outside the builder's assignment. No native Claude team tools, additional workers, or extra manager messages are required from this planner.
