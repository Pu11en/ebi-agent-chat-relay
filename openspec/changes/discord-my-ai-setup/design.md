## Context

See `proposal.md` for motivation and `specs/custom-ai-inventory/spec.md` for the behavior contract.
The repository has a generic settings store, a custom Cog loader, runtime backend settings, and a
prototype of the selected Discord experience, but it has no domain model that can describe where a
custom AI item came from or whether Claude, Codex, or DSH actually loads it. Settings is also being
redesigned in the separate command-surface change, so this change must publish a clean My AI Setup
entry/view rather than own the entire Settings command.

Inventory sources span the shared home configuration, harness-specific homes, project-local files,
loaded custom Cogs, and remote computers. Merely walking every file would be noisy and dangerous;
adapters need explicit boundaries, secret redaction, provenance, and a distinction between
configured, discovered, and verified-loaded state.

## Goals / Non-Goals

**Goals:**

- Normalize custom AI additions into one read-only inventory model with factual provenance.
- Render the approved Browse, location, comparison, search, filters, and recent-change experiences
  within Discord's component limits.
- Make local and remote verification states honest and timestamped.
- Create a safe, scoped management-session handoff for deeper questions or requested changes.
- Let new harnesses and custom item kinds add adapters without changing the UI model.

**Non-Goals:**

- Editing, deleting, disabling, installing, or synchronizing configuration from inventory controls.
- Judging whether an item is professionally written; that belongs to the harness-audit change.
- Forcing Claude and Codex to use identical internal formats or model/effort settings.
- Showing secret values or reading arbitrary user files outside declared adapter sources.
- Exposing the internal Mega Global ownership terminology to users.

## Decisions

### Build a frontend-neutral inventory model and adapter registry

Define immutable inventory item, source, scope, availability, prerequisite, measurement, and
snapshot types outside the Discord Cog. Each adapter declares the item kinds and source roots it
understands and returns safe metadata plus diagnostics. A collector merges items by stable identity
and records adapter failures without aborting the full inventory.

A Discord-only scraper was rejected because comparison, audits, and future surfaces need the same
facts. One universal filesystem walker was rejected because it cannot distinguish built-ins from
custom overrides, effective scope, or actual harness loading semantics.

### Separate configured, discovered, and verified-loaded states

Availability is a structured state per harness and computer: configured, discovered, verified
loaded, unsupported, missing prerequisite, stale, unreachable, or unknown. Adapters may claim
verified-loaded only from deterministic evidence such as the loader's resolved source set or a
harness-generated inventory; the mere presence of a file is only discovered.

This prevents the UI from turning assumptions into green checks. Treating every readable file as
active was rejected because that is the token-waste and broken-loading problem the view must reveal.

### Classify custom versus built-in at the adapter boundary

Adapters tag every item as custom, overridden built-in, or unchanged built-in using authoritative
source boundaries and loader metadata. The collector defaults to custom plus overrides and retains
built-ins only for an explicit filter. Counts remain split by classification.

A name-based heuristic was rejected because a custom file can have a vendor-like name and a vendor
item can be copied into a custom directory.

### Redact before persistence or rendering

Adapters return metadata, fingerprints, and source locators, never raw secret-bearing configuration.
A shared redactor rejects common credential keys, private-key blocks, environment values, and
connector secrets before an item can enter a snapshot. Content fingerprints are one-way and used
only for parity comparison. Diagnostics and logs follow the same safe representation.

Relying on Discord ephemerality was rejected: ephemeral messages, logs, snapshots, and spawned
sessions are still disclosure surfaces. Redacting only at render time was rejected because unsafe
data could already have been persisted.

### Persist compact local and remote snapshots with source timestamps

Use a dedicated repository for safe inventory snapshots, per-source diagnostics, content
fingerprints, verification timestamps, and declared exceptions. Local collection is refreshed on
opening and can be cached briefly. Remote collection requests a bounded snapshot through the
trusted handoff interface; when offline, the last snapshot remains viewable with a stale state.

Live remote filesystem reads from the DrewAI process were rejected because paths and permissions
belong to the destination computer. Keeping no snapshot was rejected because Compare computers
would become useless whenever one machine sleeps.

### Render one ephemeral, owner-bound Discord view

Add a persistent Settings entry point supplied to the command-surface build and an ephemeral
owner-bound `MyAISetupView`. Its state contains only filters, selected tab, selected safe item ID,
and page cursor. Browse by kind is the default; Where it lives and Compare computers use the same
snapshot. Search and Recent changes are deterministic metadata operations with pagination capped to
Discord limits.

Posting a permanent technical dashboard was rejected because it becomes stale, exposes more setup
than necessary, and would be buried like earlier session buttons. Direct edit buttons were rejected
by the approved safety boundary.

### Treat internal ownership classes as mapping inputs, not UI vocabulary

The collector may use an internal universal/default ownership class for inheritance and audit logic.
The renderer maps it to `Everywhere` or `Shared Drew profile` based on effective scope. No route or
tab is named Mega Global.

### Spawn Setup Agent through the existing session contract

Ask Setup Agent serializes a bounded safe context packet containing item ID, kind, display name,
source locator, user-facing scope, availability evidence, prerequisites, measurements, and the
user's question. It creates a normal thread through the existing session creation contract. The
packet points the agent to the source but excludes raw content and secrets; subsequent reads and
edits follow normal task authority.

Running an agent during ordinary browsing was rejected for token cost. Editing directly from the
item view was rejected because it bypasses planning, project rules, and validation.

### Make cross-build ownership explicit

This change owns inventory domain/adapters, safe snapshot persistence, the My AI Setup view, and its
tests. The command-surface build owns Settings navigation and consumes the registered entry point.
The trusted-handoff build owns transport; this change owns the inventory packet and snapshot
semantics. The professional-audit build consumes inventory facts but owns evaluation and remedies.

## Risks / Trade-offs

- **[Harness formats change]** → Keep adapters isolated, return source-level diagnostics, and test
  fixtures from supported Claude, Codex, and DSH layouts.
- **[Secret detection misses an unfamiliar field]** → Whitelist safe metadata fields, never persist
  raw values, and fail closed for sources that cannot be summarized safely.
- **[Remote comparison looks current when a machine sleeps]** → Put source computer, verification
  time, and freshness state on every snapshot and item view.
- **[File modification time is an imperfect change history]** → Label it as source modification
  time and reserve exact audit history for changes observed by the inventory repository.
- **[Token estimates differ by model]** → Prefer measured byte/character size, label tokenizer and
  model when known, and otherwise show an estimate or unknown rather than false precision.
- **[Large inventories exceed Discord limits]** → Paginate every collection, cap labels safely,
  and keep item details in a separate owner-bound view.
- **[Parallel work collides in setup wiring]** → Publish registration interfaces first; leave final
  Settings button wiring to the command-surface integration owner.

## Migration Plan

1. Add inventory types, adapter protocol, safe redaction, and local fixture-based adapters without
   exposing a Discord entry point.
2. Add snapshot persistence and remote snapshot ingestion, keeping all raw configuration out of the
   database.
3. Build the owner-bound Discord views against fixture snapshots and verify platform limits.
4. Add Setup Agent session creation and test that only safe context is attached.
5. Integrate the registered entry point into Settings after the command-surface interface lands.
6. Enable in a dev worktree, verify local DrewAI facts, then compare a trusted iMac snapshot and a
   deliberately different David profile.
7. Roll back by unregistering the Settings entry point; configuration sources and stored safe
   snapshots remain untouched and no source edit needs reversal.
