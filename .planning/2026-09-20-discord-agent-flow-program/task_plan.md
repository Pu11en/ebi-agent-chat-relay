# Task Plan: Discord Agent Flow Program

## Goal
Recover every decision from Discord thread 1550757693784989707, separate the work into
independently buildable features, preserve exact approved scope, and dispatch verified builds
without losing unrelated user changes.

## Next Step
Phase 5: Drew reviews PR "Release v4.1.0" (`release/v4.1.0` → `main`), runs the live checks
the release notes list (`docs/plans/v4.1.0-release-notes.md`), then switches the Lenovo bot
over. The four boxes left open in the OpenSpec changes (command-surface 4.3/4.4, catalog 5.2,
my-ai-setup 5.2) are those live checks.

## Current Phase
Phase 5

## Phases

### Phase 1: Requirements & Discovery
- [x] Recover the complete thread history
- [x] Reconcile existing planning commits and live runtime state
- [x] Document in findings.md
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Split the program into independent OpenSpec changes
- [x] Record every approved decision and dependency
- [x] Define task ownership, checks, and integration order
- [x] Strict-validate all seven OpenSpec changes
- **Status:** complete

### Phase 3: Implementation
- [x] Commit the approved planning foundation (`handoff/v4.1.0-base`)
- [x] Dispatch independent tasks through visible Discord workers — built on David's machine
  (2026-09-21) as seven isolated worktree builders plus the Go Work upgrade T03–T32, one
  commit per task; proof per task in `docs/plans/v4.1.0-finish-all-builds.progress.md`
- [x] Integrate verified worker branches in dependency order (merged with `--no-ff` into
  `release/v4.1.0`; conflicts confined to the plan docs and three wiring files)
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Run focused checks for each feature (each change's `Check:` line green; all seven
  validate `--strict`; four archived under `openspec/changes/archive/2026-09-21-*`)
- [x] Run combined lint, type, test, and security checks (E1: 5607 passed; E2: three
  security audits, 16 findings fixed with tests → 5781 passed; E3: Go Work Check 555 +
  practice demo exit 0)
- [ ] Verify the Discord flows locally before publication — needs a live bot; carried into
  the release PR's checklist (E5) and the three `tasks.md` boxes that stay open
- **Status:** verification complete offline; live Discord checks pending

### Phase 5: Delivery
- [ ] Give Drew worker links and live status
- [ ] Give Drew one concrete local try-it flow per feature
- [ ] Keep GitHub publication out of scope until Drew tests and approves
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Compact structured child-thread handoffs | Preserve goal, project, decisions, dependencies, restrictions, and parent link without token-heavy transcript copying. |
| Hybrid automatic build splitting | Automatically split clearly independent builds, ask only when uncertain, and honor explicit “make this separate” language. |
| One branch and worktree per simultaneous task | Prevent agents working in the same repository from overwriting one another. |
| No fixed `/gowork` worker cap | Dispatch every dependency-ready, non-conflicting task; infrastructure backpressure may queue actual execution. |
| Parent planning thread coordinates child builds | Each child thread plans one build and may later run its own `/gowork`; merely creating a plan does not spend on implementation. |
| Seven separate builds | Commands/lifecycle, handoffs, project catalog, My AI Setup, harness audit, parallel `/gowork`, and model-capacity resilience have distinct outcomes and can progress independently. |
| Provider saturation must not dead-end a task | Queue/retry or apply an approved fallback policy and keep visible status instead of ending with only “model at capacity.” |
| Build only after the proposal boundary | The OpenSpec proposal workflow requires planning artifacts to be presented before implementation starts in a later request. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Initial planning script was not executable | Invoked the installed script explicitly with `bash`. |
| Initial JavaScript tool wrapper expanded an undefined `DISCORD_THREAD_ID` | Passed the shell variable inside a literal command string. |
