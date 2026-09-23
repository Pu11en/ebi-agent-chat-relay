# Discord Agent Flow — Approved Build Program

## ✅ What is locked in

- The work is split into **seven independent builds**, each with its own behavior contract, technical design, small implementation tasks, and checks.
- Parallel implementation uses a **separate branch and worktree for every simultaneous task**. One integration owner combines only verified work.
- The parent Discord planning thread remains the coordinator. Clearly independent ideas get linked child planning threads with a compact handoff instead of a token-heavy transcript.
- All work stays local until Drew tries it in Discord and separately approves putting it on GitHub.

### Safe build order

- All seven planning packages must validate before implementation begins.
- Capacity recovery, command flow, handoffs, catalog, setup inventory, audit, and parallel `/gowork` can then start as separate workers where their owned files do not overlap.
- Shared database, setup wiring, and integration files are assigned to one owner at a time even when feature work is parallel.
- Focused tests run per task, followed by lint, format, type checks, full tests, and required security review.
- Drew receives one short Discord try-out for every feature before any GitHub publication decision.

## ⬜ Control Center and session flow

- Keep one refreshed control row at the bottom of each Control Center so it never gets buried. The prior row is removed before the new one is posted.
- Show computer status plus **New session**, **Sessions**, and **Settings**.
- New session includes Favorites, Recent, Browse, Create, and Clone, then creates a clean idle thread with that computer's default model.
- Sessions is searchable and newest-first, with Open, New in same folder, and Close.
- Control Center commands become `/new`, `/sessions`, `/settings`, and `/help`.
- Session-thread commands become `/switch`, `/stop`, `/session`, `/close`, and `/help`.
- `/switch` opens model choice directly and resolves the harness automatically.
- `/session` contains Fork, Rewind, Compact, Clear, Context, and Goal.
- `/close` finishes the active turn, writes a wrap-up, marks the session closed, and archives the Discord thread without deleting or permanently locking it. Sessions can reopen it.
- An agent may invoke close only when Drew requested it or the workflow was already approved to close on completion.
- Existing commands stay available until replacements pass local tests and Drew's Discord try-out.

## ⬜ Computers, handoffs, and projects

- DrewAI, iMac, and David can send tasks to one another in both directions through one shared `agent-handoffs` channel, with one visible thread per job.
- Every handoff has a stable task ID, explicit sender and recipient, explicit project owner and folder, goal, existing authority, relevant findings, expected result, and reply location.
- Trusted guild and bot identities are checked; duplicate task IDs, unbounded replies, and bot-to-bot reply loops are rejected.
- Offline computers show queued status and automatically resume when they reconnect. The origin sees acknowledgement, blockers, and the final result; detailed work stays in the handoff thread.
- Read-only work is automatic. Edits remain authorized only when the original request authorized edits. Deletion, deployment, paid services, external messages, permission changes, or unclear scope still require authority.
- On David's computer, **“Drew's projects”** explicitly means DrewAI's projects. Pronouns are never guessed across owners.
- Every direct child of an approved main-projects root appears automatically in New session and is resolvable by local Claude, Codex, and DSH sessions.
- Project folders are queried on demand instead of injecting folder trees or contents into every prompt. Remote folders are reached through trusted handoffs, not treated as local paths.
- DrewAI and iMac share Drew's profile behavior but may have deliberate machine-specific paths and availability; unsupported options stay hidden.

## ⬜ My AI Setup and professional audit

- Settings gains **My AI Setup**, a read-only Discord view of custom preferences, instructions, memory, skills, tools, plugins/connectors, commands, hooks, and harness settings.
- It opens on Browse by kind and includes Where it lives, Compare computers, search, filters, and Recent changes.
- Every item shows its source, scope, verified harness availability, prerequisites, measurable size/token estimate, and last change. Built-in defaults stay hidden unless requested.
- Ask Setup Agent creates a scoped management session for the selected item. There are no unsafe direct-edit buttons and no visible “Mega Global” technical screen.
- A separate professional audit covers Discord Claude and Codex on DrewAI and iMac, including custom additions and built-in content actually loaded.
- The audit uses official vendor guidance plus deterministic checks for scope, duplication, size, permissions, loading behavior, dead configuration, and misplaced project content.
- Results are classified Keep, Fix, Move to project, Load only when needed, or Remove. Questionable items are disabled or quarantined first, both harnesses are verified, and removal happens only after proof.
- Portable preferences, safety rules, project knowledge, memory, and skills use a shared source; tools, permissions, models, and hooks retain thin harness-specific adapters.
- Reasoning effort, model intelligence, and quality settings are explicitly outside this audit.

## ⬜ Parallel builds and automatic planning splits

- `/gowork` keeps one fresh session per small task and deterministic git/progress state.
- It dispatches every dependency-ready task whose owned files do not overlap. There is **no arbitrary `/gowork` worker limit**; real relay, provider, and machine pressure may still queue execution safely.
- Dependencies, owned paths, cycles, and collisions are checked before dispatch. Every simultaneous task receives its own branch and worktree.
- Verified results are integrated by one owner in dependency order; failed or conflicting results do not silently land.
- Planning automatically creates linked child threads for clearly independent builds, asks only when the boundary is uncertain, and honors “make this separate.”
- Each child receives only its goal, project, locked decisions, dependencies, restrictions, and parent link. The parent displays shared status and integration results.

## ⬜ “Model at capacity” recovery

- “Model at capacity” no longer ends a task or forces Drew to repeat the prompt.
- The system distinguishes local relay queueing, temporary model saturation, provider rate limiting, subscription/quota exhaustion, authentication failure, and permanent errors.
- Temporary saturation keeps the logical turn pending with one live waiting/retrying/fallback status and bounded exponential backoff.
- Retry state survives a bot restart, reacquires normal relay capacity, and uses a stable turn identity so a restart or late provider answer cannot execute or post the result twice.
- Automatic fallback uses only a chain explicitly authorized for that task, session, or computer. It never silently introduces a paid provider or a materially different data boundary.
- Quota, authentication, permanent, or exhausted-retry outcomes stop automatically and explain the next valid action.
- This is a separate build from parallel `/gowork`: provider saturation recovery applies to ordinary Discord turns and automated workers alike.
