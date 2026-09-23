## Why

The authorized Lockin AI setup trial needs an independently testable greeting module to exercise a scoped planning-to-builder handoff. Recording the agreed greeting behavior now gives the builder and integration lead one stable contract.

## What Changes

- Plan a public `greet(name: str) -> str` function in `examples/feature_workflow_trial/greeting.py` that returns exactly `Hello, <name>!`.
- Retain the decision: Preserve case. Strip exterior whitespace only. Empty name becomes friend.
- Specify focused tests in `examples/feature_workflow_trial/test_greeting.py`, written and run before implementation.
- Record the planning owner, fixed foundation, builder file ownership, and lead integration boundary in this change.

## Capabilities

### New Capabilities

- `trial-greeting`: Format a greeting while preserving case and interior whitespace, trimming exterior whitespace, and using `friend` when the trimmed name is empty.

### Modified Capabilities

None.

## Impact

This planning handoff writes only `openspec/changes/trial-greeting/`. At foundation `1b19dba258393c9e49e4e16631ed0bff70ea8b32`, the trial directory contains its shared-contract README; the greeting module, its tests, and existing capability specs are absent.

The eventual builder owns only `examples/feature_workflow_trial/greeting.py` and `examples/feature_workflow_trial/test_greeting.py`. The public interface is the function above; it requires only Python's standard library. The integration lead owns the shared README, CLI, integration tests, and combining commits. Runtime relay code, dependencies, other feature plans, live data, and existing conversations are outside this change.
