# Task Plan: Discord Agent Flow Program

## Goal
Recover every decision from Discord thread 1550757693784989707, separate the work into
independently buildable features, preserve exact approved scope, and dispatch verified builds
without losing unrelated user changes.

## Next Step
After Drew's next explicit build instruction, dispatch the seven validated OpenSpec changes through
separate visible workers, respecting their dependency and file-ownership boundaries.

## Current Phase
Phase 3

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
- [ ] Commit the approved planning foundation
- [ ] Dispatch independent tasks through visible Discord workers
- [ ] Integrate verified worker branches in dependency order
- **Status:** pending

### Phase 4: Testing & Verification
- [ ] Run focused checks for each feature
- [ ] Run combined lint, type, test, and security checks
- [ ] Verify the Discord flows locally before publication
- **Status:** pending

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
