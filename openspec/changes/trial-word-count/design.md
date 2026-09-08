## Context

See `proposal.md` for motivation. Foundation `1b19dba258393c9e49e4e16631ed0bff70ea8b32` contains the trial README and its shared contract, but no counter module or focused counter tests. The repository uses Python 3.12+; the trial contract requires only the standard library.

Planning owner thread `1546687950375493752` owns only `openspec/changes/trial-word-count/`, in `/home/drewp/main-projects/wt-1546687950375493752`. The root manager and integration owner is thread `1546685954167672912`.

## Goals / Non-Goals

**Goals:**

- Provide a deterministic, side-effect-free function with the public contract `count_words(text: str) -> int`.
- Make the feature independently testable within the builder's two assigned files.
- Preserve one owner per file and an explicit handoff to the integration owner.

**Non-Goals:**

- Linguistic word segmentation, punctuation removal, input normalization, or validation/coercion of non-string values.
- CLI wiring, integration tests, shared configuration edits, or changes to any other feature.
- Implementation or worker dispatch in this planning session.

## Decisions

1. **Retained behavior:** Split only on whitespace; punctuation remains part of each token. Empty input is zero. `count_words(text: str) -> int`.
   Use Python's separator-free string splitting semantics, including Unicode whitespace, and count the resulting tokens. This directly satisfies the contract without a normalization step. Splitting on a literal space would mishandle tabs, repeated whitespace, and blank input; a punctuation-aware tokenizer would change the approved behavior.

2. **Module boundary:** The builder owns only `examples/feature_workflow_trial/counter.py` and `examples/feature_workflow_trial/test_counter.py`. The module exports the named function without printing, reading files, or importing the relay. The lead invokes that real function during integration. Putting the function in the CLI would couple file ownership and prevent independent verification.

3. **Focused tests first:** Use standard-library `unittest` cases in `test_counter.py`, importing `count_words` from the neighboring `counter` module. Explicit discovery from the trial directory makes that import work without adding a package initializer or changing project configuration. The tests remain runnable by pytest, while unittest discovery keeps this disposable trial independent of the relay's installed dependencies. Cover the five spec scenarios and assert the public result is an `int`. Observe the initial failure before creating `counter.py`.

4. **Coordination:** The lead assigns a visible Ebi builder in its own isolated worktree using this exact plan revision and the agreed foundation. The builder completes focused tests, build, and verification sequentially; no other feature is a prerequisite. Native Claude team/task tools are replaced by the Ebi assignment and final lead handoff. The planner starts no workers and sends no extra manager messages.

## Risks / Trade-offs

- [A punctuation-aware interpretation could drift from the contract] → The punctuation scenario includes joined words, an apostrophe, a hyphen, and a punctuation-only token.
- [Default test discovery may omit the trial directory] → Invoke focused discovery with the builder's absolute trial path and `test_counter.py` pattern.
- [An accidental edit could cross ownership boundaries] → Verify the builder's diff contains only its two assigned files; the lead owns README, CLI, and integration test changes.
- [Separator-free splitting allocates tokens] → Accept this small standard-library approach for the disposable trial; streaming large-input optimization is outside scope.

## Migration Plan

No deployment or data migration is required. After focused verification, the builder commits and pushes its two owned files and reports the SHA and test result through its Ebi result. The lead combines the feature commit and performs integration verification. If integration fails, the lead can withhold the feature commit while preserving this plan and all existing conversations.
