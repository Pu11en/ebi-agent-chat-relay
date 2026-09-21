## Context

See `proposal.md` for motivation and `specs/trial-greeting/spec.md` for observable behavior. The agreed foundation is `1b19dba258393c9e49e4e16631ed0bff70ea8b32`. Its trial README defines the interface; no greeting implementation or focused tests exist yet. The repository targets Python 3.12+ and requires tests before feature code.

This is a planning-only handoff owned by thread `1546687948588589086` in `/home/drewp/main-projects/wt-1546687948588589086`. Root manager and integration lead: thread `1546685954167672912`. The explicitly requested design records the fixed decision and file boundaries before any builder starts.

## Goals / Non-Goals

**Goals:** Use a pure, independently testable function with deterministic output, standard-library operations, and no dependency on integration code. Freeze the string interface so the lead can consume the builder's commit.

**Non-Goals:** Runtime type coercion for non-string inputs, localization, case or interior-whitespace normalization, I/O inside the greeting function, and creating shared utilities. This plan does not launch workers or perform implementation.

## Decisions

### Retain the approved greeting decision

Preserve case. Strip exterior whitespace only. Empty name becomes friend. `greet(name: str) -> str` returns `Hello, <name>!`.

Use Python's standard `str.strip()` semantics for exterior whitespace, then substitute `friend` only if the result is empty and format the exact greeting. Case conversion and splitting/rejoining the name would change approved behavior, so neither is appropriate. Handling only literal spaces would miss tabs and newlines covered by the spec. Non-string inputs remain outside the declared `str` contract; add no coercion or validation policy.

### Keep one owner per file

| Owner | Writable scope |
| --- | --- |
| Planning thread `1546687948588589086` | `openspec/changes/trial-greeting/` only |
| Future greeting builder, assigned by Ebi/lead | `examples/feature_workflow_trial/greeting.py` and `examples/feature_workflow_trial/test_greeting.py` only |
| Integration lead `1546685954167672912` | Shared trial README, `cli.py`, `test_integration.py`, and combining commits |

Builders read the fixed plan revision and report task completion through their Ebi result; they do not edit planning artifacts. Separate worktrees and branches isolate edits. Giving the builder shared-file ownership would create unnecessary overlap with the lead. No new shared interface file or package initializer is needed.

### Test the public behavior before building

Use standard-library `unittest` in `test_greeting.py` against the real `greet` export. Assert complete returned strings for all six spec scenarios, including the preserved interior tab and two spaces. This avoids extra test dependencies and makes exact punctuation and newline behavior observable.

Run the tests before creating `greeting.py`; the expected initial failure is the missing greeting import, not an unrelated environment error. After implementation, all cases must pass. Using only happy-path tests would leave the preserved decision unprotected; mocking `greet` would not verify its contract.

### Use a short, explicit handoff chain

Dependencies are: fixed plan revision → focused tests with recorded failure → greeting implementation → focused verification and pushed commit → lead integration. The greeting slice has no feature-code dependency outside its owned files. Ebi and the lead handle assignment and result collection in place of native Claude team tools. This planning owner spawns no workers and sends no extra manager messages.

## Risks / Trade-offs

- Case conversion or interior-whitespace normalization could drift from the fixed decision → assert mixed case and repeated interior whitespace with exact output comparisons.
- Repository-default test discovery targets `tests/`, so it could miss the trial test → run discovery explicitly against the absolute trial directory and `test_greeting.py` pattern.
- A builder could edit integration or planning files while marking progress → constrain its diff to the two owned files and report completed task IDs in the handoff.

## Migration Plan

No deployment, data migration, or runtime restart is required. The builder supplies a verified commit containing only its module and tests. The lead combines that commit and performs integration verification. If the disposable feature is rejected before integration, the lead can omit the builder commit; an already integrated feature is reverted by the lead in coordination with its consumers. Existing conversations and saved plans stay intact.
