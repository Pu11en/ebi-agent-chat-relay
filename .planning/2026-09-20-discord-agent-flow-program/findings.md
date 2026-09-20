# Findings & Decisions

## Requirements
- Preserve every build and decision from Discord thread 1550757693784989707.
- Separate independently buildable ideas into linked planning threads/builds.
- Build approved work now through the Discord-visible worker workflow.
- Preserve unrelated dirty changes in the main checkout.
- Keep all work local until Drew has tested it and separately approves GitHub publication.
- Handle temporary model/provider saturation without dead-ending the Discord task.

## Recovered Feature Scope

### 1. Control Center and session commands
- Keep one fresh control row near the bottom; delete its previous copy so panels do not pile up.
- Buttons: New session, Sessions, Settings. Show computer status above the buttons.
- New session: Favorites, Recent, Browse, Create, Clone; use the computer default model and
  create a clean idle thread. `/switch` changes the current thread later.
- Sessions: newest first with search; Open, New in same folder, and Close actions.
- Control Center visible commands: `/new`, `/sessions`, `/settings`, `/help`.
- Session-thread visible commands: `/switch`, `/stop`, `/session`, `/close`, `/help`.
- `/session` contains Fork, Rewind, Compact, Clear, Context, and Goal.
- `/help` shows only actions that work in the current location.
- Old commands move into menus or internal agent actions and are removed only after replacements
  are locally tested.
- `/close` is distinct from `/stop`: finish the active turn, produce a wrap-up, mark closed,
  archive without deleting or permanently locking, and allow Sessions to reopen it.
- Agents use the same internal close operation only when Drew requested it or a preapproved
  workflow says to close when done.

### 2. Trusted bidirectional computer handoffs
- DrewAI, iMac, and David can all assign work to each other through Discord.
- One shared `agent-handoffs` channel; one visible task thread per job.
- Each packet contains task ID, explicit destination/owner, validated folder, goal, permissions,
  relevant findings, expected result, and reply location—never the full conversation by default.
- Reading/searching/status work is automatic. Edits are automatic only when the original request
  authorized them. Deletion, deployment, paid services, external messages, permission changes,
  and unclear scope still require authority.
- Trusted sender/recipient/guild checks, deduplication, typed message states, bounded replies, and
  loop prevention are mandatory.
- Offline destinations show queued status and start automatically on reconnection.
- Detail stays in the handoff thread; the origin receives acknowledgement, blockers, and final
  completion. The origin normally integrates the remote result, adapting when a decision or
  direct remote conversation is needed.
- Language is explicit across owners: on David, “Drew’s projects” targets DrewAI; “David’s
  projects” stays local. Do not guess cross-computer pronouns.

### 3. Shared project catalog
- Every direct child of each computer's approved main-projects root appears automatically in New
  session and is resolvable by local Claude, Codex, and DSH sessions.
- The catalog is queried only when needed; folder trees and contents are not injected into every
  prompt.
- Each computer keeps its own paths and availability. Remote access uses trusted handoffs.
- DrewAI and iMac share Drew's behavior/profile but may have deliberate machine capability
  exceptions. Unsupported models/features are hidden.

### 4. My AI Setup inventory
- Discord view lives at Settings → My AI Setup.
- Show custom preferences, instructions, memory, skills, tools, plugins/connectors, custom
  commands, hooks, and harness settings. Hide built-in defaults unless requested.
- Open on Browse by kind; retain Where it lives and Compare computers tabs, plus search, filters,
  and Recent changes.
- Each item shows its source and scope: everywhere, shared Drew profile, one computer, David, or
  one project.
- Ask Setup Agent opens a management session with the selected item attached. No direct unsafe
  edit buttons and no visible “Mega Global” technical screen.

### 5. Professional Claude/Codex audit
- Audit Discord Codex on DrewAI and iMac and align Claude/Codex behavior without forcing identical
  internals.
- Do not change reasoning effort, model intelligence, or quality settings as part of this audit.
- Cover custom instructions, preferences/memory, skills, tools, plugins, MCP connections, hooks,
  Discord-injected context, duplication, and any built-in material that is actually loaded.
- Use official vendor guidance and deterministic checks for scope, size, duplication, permissions,
  loading behavior, and dead configuration; do not spend tokens on subjective grading without an
  answer key.
- Shared source for portable preferences, safety rules, project knowledge, memory, and skills;
  thin harness-specific adapters for tools, permissions, model settings, and hooks.
- Classify items Keep, Fix, Move to project, Load only when needed, or Remove. Disable/quarantine
  first, verify both harnesses, then remove only after proving unnecessary.

### 6. Parallel `/gowork` and automatic build splitting
- Preserve Ralph's fresh session per small task and deterministic git/progress state.
- Start every dependency-ready task whose owned files do not overlap; no fixed `/gowork` worker
  cap. Real relay/provider/machine backpressure may queue execution.
- Every simultaneous task gets its own branch and worktree; one integration owner combines
  verified results in dependency order.
- Parent planning thread automatically splits clearly independent builds, asks only when uncertain,
  and accepts “make this separate.”
- Child planning threads receive a compact structured handoff: goal, project, decisions,
  dependencies, restrictions, and parent link.
- Planning can share a repository read-only; implementation never shares the same writable copy.

### 7. Model-capacity resilience
- A temporary “model at capacity” or provider saturation result must not be the final task outcome.
- Preserve the task and show a waiting/retrying/fallback status. The exact safe fallback policy must
  distinguish temporary provider saturation from subscription quota exhaustion and relay capacity.
- Do not hide genuine provider limits or silently spend on a different provider without an allowed
  policy.

## Research Findings
- The main checkout is dirty with unrelated user work and is not safe for this program.
- Existing branch `session/1550757693784989707` contains three planning commits on top of the
  live `feat/task-loop` foundation: bidirectional handoffs, control-center shortcuts, and custom
  AI setup views.
- The live bot imports from `/home/drewp/main-projects/wt-task-loop` at commit `410d5e9`.
- The live relay has a global session capacity of 10, but that is infrastructure backpressure,
  not a `/gowork` scheduling rule.
- Current `/gowork` is intentionally sequential. Older Ralph-derived documents also rejected
  parallel workers after an earlier failure, while the newer feature coordinator proves safe
  dependency-ready dispatch with non-overlapping owned paths.
- The current thread's recent “model at capacity” report is not caused by the relay semaphore:
  live capacity was 2 running, 0 queued, limit 10. No matching phrase appeared in the recent
  service journal, so the implementation must normalize backend/provider saturation separately.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use the restored session worktree | It preserves the existing planning commits and isolates this work from the dirty main checkout. |
| Use OpenSpec per feature and the Ebi coordinator for builds | This is the repository's established visible, recoverable multi-worker workflow. |
| One owner per file | Required for safe parallel implementation and deterministic integration. |
| Preserve explicit approval references | Build authorization is recorded by the user's messages at Discord message IDs `1551106260437442621` and `1551122399519449099`. |
| Model recovery wraps a logical turn | Classification, persistence, fallback authority, and accepted-result deduplication must be shared by interactive sessions and automated workers. |

## Validated Build Packages

- `discord-command-surface`: proposal, two capability specs, design, and 16 small tasks.
- `trusted-agent-handoffs`: proposal, capability spec, design, and 16 small tasks.
- `shared-project-catalog`: proposal, capability spec, design, and 13 small tasks.
- `discord-my-ai-setup`: proposal, capability spec, design, and 15 small tasks.
- `professional-harness-audit`: proposal, capability spec, design, and 14 small tasks.
- `parallel-gowork`: proposal, two capability specs, design, and 17 small tasks.
- `model-capacity-resilience`: proposal, capability spec, design, and 16 small tasks.
- Every package passes `openspec validate <change> --strict` and reports 4/4 artifacts complete.

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| No durable feature-workflow binding existed for this thread | Restored the existing `session/1550757693784989707` branch into its prescribed worktree and initialized a pinned parent plan. |
| Discord history API returns at most 100 recent messages | Recovered older turns from the local Codex rollout and the prior Claude session record, then reconciled them with committed and untracked planning artifacts. |
| “Model at capacity” was not present in repository strings or recent relay logs | Treat it as a backend/provider result requiring explicit classification and recovery behavior, not as evidence that the relay's global capacity is full. |
| Seven detailed plans could take too long sequentially | Used three non-overlapping planning workers plus the parent, then reran strict validation centrally. |

## Resources
- Discord thread API and live session API
- Existing commits `6f5a9d1`, `6c1f4ff`, and `2fd9f8a`
- `docs/plans/gowork-v3.md` and `docs/plans/gowork-v3-handoff.md`
- `lockin-feature-workflow`, `parallel-feature-development`, and
  `task-coordination-strategies` skills
