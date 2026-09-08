## Why

The authorized Lockin AI setup trial needs an independently buildable word counter to exercise the feature planning and lead integration workflow. This plan fixes the counting contract and file ownership before the builder starts.

## What Changes

- Add `count_words(text: str) -> int` to the disposable trial's `counter.py`.
- Retain the exact decision: Split only on whitespace; punctuation remains part of each token. Empty input is zero. `count_words(text: str) -> int`.
- Cover the contract with focused tests in `test_counter.py`, written and run before implementation.
- Deliver the verified feature commit to the lead for integration.

## Capabilities

### New Capabilities

- `trial-word-count`: Count whitespace-separated tokens, including punctuation within tokens, and return zero when there are no tokens.

### Modified Capabilities

None.

## Impact

- This planning revision changes only `openspec/changes/trial-word-count/`.
- The builder owns only `examples/feature_workflow_trial/counter.py` and `examples/feature_workflow_trial/test_counter.py`.
- The shared contract comes from `examples/feature_workflow_trial/README.md` at foundation `1b19dba258393c9e49e4e16631ed0bff70ea8b32`; this capability has no existing main spec at that foundation.
- The implementation and focused tests use the Python standard library. No dependencies or project configuration need changes.
- Root manager thread `1546685954167672912` owns integration, including the shared README, CLI, and integration tests. The feature requires no changes to live relay behavior or data.
