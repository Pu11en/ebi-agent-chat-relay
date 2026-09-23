## Context

See `proposal.md` for motivation and `specs/shared-project-catalog/spec.md` for the behavior
contract. Today `ProjectLauncherCog` independently scans `CCDB_PROJECT_ROOTS`, stores raw paths for
favorites and recents in the generic settings table, and binds a chosen path directly through
`ClaudeChatCog.spawn_session`. There is no shared identity or query surface for Claude, Codex, and
DSH, and no owner-aware distinction between a local project and a project on another computer.

The framework must remain reusable and zero-config for consumers. Personal machine roots, aliases,
and trusted peers are deployment configuration, while discovery and resolution belong in the
framework. The trusted handoff change owns remote execution; this change owns catalog resolution
and the typed result handed to it.

## Goals / Non-Goals

**Goals:**

- Define one catalog domain model and resolver used by Discord and all local backend adapters.
- Preserve local filesystem truth while retaining personal Favorite and Hide metadata.
- Make owner-qualified remote requests explicit and safe to hand to the handoff subsystem.
- Keep queries bounded and out of normal prompt context.
- Provide compatibility migration from existing launcher favorites and recents.

**Non-Goals:**

- Indexing project contents, source symbols, or arbitrary nested folders.
- Synchronizing a single absolute path database across computers.
- Replacing the trusted handoff transport or copying remote projects locally.
- Choosing models, effort, or subscription capabilities for a session.
- Removing the launcher's unrestricted manual folder browser; catalog projects become its normal
  project view, while explicit Browse remains a separate local-path action.

## Decisions

### Use a framework-owned catalog service with deployment-owned configuration

Add a frontend-neutral catalog service under `claude_discord/` with immutable project/root/result
types. Approved roots, computer identity, owner aliases, and machine capabilities enter through a
small config object populated from optional setup arguments with environment fallbacks. Expose the
service through `BridgeComponents`, so built-in and custom Cogs share it without reconstructing
configuration.

This follows ccdb's zero-config setup pattern and gives every caller the same validation rules.
Keeping the entire feature in a personal Cog was rejected because Claude/Codex/DSH and the built-in
launcher would continue to disagree.

### Derive identity from owner, configured root identity, and direct-child name

Each approved root receives a stable configured key; a project identity is the normalized owner,
computer, root key, and child name, while the canonical absolute path remains local runtime data.
The display label uses the folder name and adds root/computer qualifiers only where required to
disambiguate.

Raw absolute paths alone were rejected because Windows, macOS, and Linux cannot share them and a
path rename would silently collide with per-user metadata. Folder name alone was rejected because
same-named projects in multiple roots are valid.

### Scan one directory level on demand and cache only briefly

Catalog list/search/resolve operations enumerate direct children of the approved roots in a worker
thread, sort deterministically, and apply a bounded result limit. A short-lived in-process cache may
avoid repeated disk reads, but session creation always revalidates the chosen directory. Root and
project availability is returned as data rather than turning one inaccessible root into a failed
catalog.

A persistent filesystem index and recursive watcher were rejected: they add platform complexity,
stale-state risk, and unnecessary I/O for a small on-demand directory list.

### Store metadata by catalog identity, not by path

Introduce a small repository for per-guild, per-user Favorite/Hide state and recency keyed by stable
project identity. Discovery remains filesystem-derived. Existing `launcher.favorites:*` and
`launcher.recents:*` raw paths are lazily mapped to current identities when they fall under an
approved root; unmappable values remain available through the legacy manual browser until the user
removes them.

Using the generic key/value store forever was rejected because concurrency, identity lookups, and
metadata queries become fragile JSON rewrites. Treating favorites as registration was rejected
because new folders would not appear automatically.

### Expose a bounded local query interface to every harness

Add catalog list/search/resolve operations to the local REST control plane and a small CLI-facing
wrapper that prints bounded structured results. All runners already receive the local ccdb control
plane; their shared developer instructions need only describe how to invoke a lookup, never embed
the projects themselves. The Discord launcher calls the service directly.

Separate per-harness catalogs were rejected because they recreate the inconsistency this change is
meant to remove. Injecting the current list into every prompt was rejected for token cost, staleness,
and disclosure of irrelevant folder names.

### Return a typed remote target instead of a path

Resolution returns one of: local available project, local unavailable project, owner-qualified
remote target, ambiguous owner, or no match. Remote targets carry the trusted computer identity and
requested project terms but never a local working directory. The trusted-agent-handoffs integration
converts that result into a compact handoff packet and supplies any remote snapshot with its source
and verification time.

Trying to translate remote paths locally was rejected because the same text can identify unrelated
folders and would bypass the destination computer's permissions and subscriptions.

### Keep profile defaults and machine capabilities separate

Profile configuration can share owner aliases and policy between DrewAI and iMac. Each deployment
still supplies its roots and capability set, and the catalog result filters unsupported actions.
This supports behavioral alignment without pretending the machines or subscriptions are identical.

## Risks / Trade-offs

- **[A root with thousands of direct children slows a query]** → Run scans off the event loop,
  bound returned matches, and use a short cache without skipping pre-launch revalidation.
- **[Root-key changes orphan personal metadata]** → Treat root keys as durable configuration,
  report orphan counts, and provide an explicit migration helper.
- **[Legacy favorites outside approved roots disappear from the normal list]** → Keep them in the
  existing manual-folder path during migration and never delete the old settings automatically.
- **[Remote snapshots become stale]** → Mark every remote result with owner, source, availability,
  and verification time; never label cached remote state as locally verified.
- **[Project names disclose information to an unauthorized caller]** → Reuse existing operator and
  category authorization for Discord and bind control-plane queries to the local authenticated API.
- **[Catalog files overlap parallel command-surface work]** → This build owns catalog modules,
  repository/schema, API operations, and launcher catalog adapter; the command-surface build owns
  the final `/new` and Settings navigation and consumes the catalog interface.

## Migration Plan

1. Add the catalog types, local discovery/resolution service, and repository behind tests without
   changing the live launcher.
2. Wire the service through `BridgeComponents` and add authenticated, bounded control-plane queries.
3. Add the launcher adapter and lazy migration for existing favorites/recents while preserving the
   old values and manual folder browser.
4. Add the owner-aware handoff adapter after `trusted-agent-handoffs` publishes its interface.
5. Activate in a dev worktree on one computer, compare Discord/Claude/Codex/DSH resolution, then
   enable the same profile behavior on iMac with its own roots.
6. Roll back by disabling catalog-backed launcher selection; legacy favorites, recents, session
   records, and folders remain untouched.
