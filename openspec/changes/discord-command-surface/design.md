## Context

See `proposal.md` for motivation. The repository already has most underlying operations, but they
are spread across `ClaudeChatCog`, `SessionManageCog`, `BackendCommandCog`, and
`ProjectLauncherCog`. The launcher already saves a bot-owned message id and refreshes a quiet
bottom shortcut; the current persistent view instead exposes Favorite folders, New session, and
Resume. Session search is already newest-first, model catalogs already associate choices with
harnesses, and the chat cog already implements stop, compact, goal, clear, rewind, fork, and a
destructive `close_session` helper.

Discord distributes one guild command tree to the client and does not provide a reliable way for a
bot to hide individual slash commands dynamically for one channel or thread. Therefore
"location-aware" means a minimal union of registered commands, a filtered `/help`, different
buttons and menus by location, and fail-closed invocation checks. The Discord chooser can still
show the union in both locations.

## Goals / Non-Goals

**Goals:**

- Give buttons and slash commands one shared application-service layer so their behavior cannot
  drift.
- Keep one current, useful control message near the latest control-center activity without
  producing message spam.
- Preserve session data and working files through stop, close, reopen, clear, and model switching.
- Allow each computer to filter models and settings according to its own configuration while using
  the same reusable framework behavior.
- Make command retirement independently reversible after local and Discord acceptance testing.

**Non-Goals:**

- Hiding commands registered by other Discord applications from a server administrator.
- Making every computer expose identical models, subscriptions, or settings.
- Moving personal server ids, favorite folders, or subscription rules into the OSS framework.
- Changing model reasoning effort or automatically choosing a more expensive model.
- Deleting historical Discord messages, threads, project folders, or session records.

## Decisions

### One command-surface coordinator owns context and shared actions

Add a framework-level coordinator that classifies an interaction as control center, managed session,
or unsupported, then exposes reusable operations for new, sessions, settings, switch, stop,
session actions, close, and help. Cogs and views become thin adapters around those operations.
Instance configuration supplies channel/category ids and supported-feature policy.

This keeps the zero-config extension point in ccdb while avoiding EbiBot-specific branching in the
framework. Keeping the current commands as separate implementations was rejected because menu and
slash behavior would continue to drift.

### Register the minimal command union and enforce location at runtime

During the compatibility phase, replacements are added without unregistering old commands. After
acceptance, the final registered surface is the union `/new`, `/sessions`, `/settings`, `/help`,
`/switch`, `/stop`, `/session`, and `/close`. Each handler uses the coordinator's location check;
`/help` renders only the relevant subset.

Trying to mutate the guild command tree as users move between channels was rejected because command
registration is guild-wide, cached by Discord, and would create races. Depending on manual Discord
integration permissions was rejected because it is not zero-config and cannot reliably distinguish
individual threads.

### The control message combines live status and three stable buttons

Replace the launcher's old bottom shortcut with a persistent view whose visible buttons are New
session, Sessions, and Settings. The message contains a compact computer-status block immediately
above the row. A debounced listener responds only to normal human/control-center activity, sends a
new message, atomically saves its id, and then deletes the prior bot-owned control message. Startup
repairs missing/stale ids and removes no unrelated message. Personal follow-up menus remain
ephemeral.

Editing one old message was rejected because it remains buried. Sending a new row without deleting
the prior one was rejected because it leaves stale controls throughout history.

### New session stays idle until the first real task

Favorites, Recent, and Browse extend the existing folder browser. Create validates a single
destination beneath configured project roots before making it. Clone validates the destination,
runs version-control commands without a shell, removes or quarantines only a destination created by
that failed attempt, and never starts an AI turn. Every path produces the same bound empty session
record and thread prompt, with the computer default model shown for clarity.

Auto-running a greeting/model task was rejected because it spends subscription tokens without a
user task. Allowing arbitrary creation paths was rejected because a Discord action must not escape
the configured project roots.

### Sessions is a view over durable records plus live Discord visibility

Extend the current repository query so title/summary/folder search and lifecycle state are
available newest-first. The UI resolves each record to an accessible Discord thread before showing
it. Open, New in same folder, and Close call the same coordinator methods used elsewhere. Closed
records remain queryable.

Using Discord history alone was rejected because archived or uncached threads are incomplete and it
cannot preserve working-directory/session metadata.

### Model catalog entries resolve their own harness

The existing discovered model catalogs feed one normalized choice list containing model id, display
name, harness, availability, recency rank, and current-selection state. `/switch` takes only a model
value; autocomplete searches the full normalized list while the initial Discord list is capped.
Selecting a model writes the harness and model together at thread scope and uses existing
cross-harness handoff safeguards.

Keeping backend as a first field was rejected because it prevents Discord from opening the model
choices immediately. Inferring a harness from a free-form string after selection was rejected in
favor of catalog-owned mappings.

### Session management is one ephemeral action view

`/session` opens buttons or selects for Fork, Rewind, Compact, Clear, Context, and Goal. The view
calls the existing operations after moving reusable logic out of decorator methods. Actions that
discard conversation state require an explicit confirmation, while read-only Context does not.

Six separate top-level commands were rejected because they are the main source of slash-command
clutter. Encoding every action as a required slash subcommand was rejected because the user wants a
quick visual choice after typing `/session`.

### Close becomes a durable lifecycle transition

Add lifecycle fields to session storage (`open`, `closing`, `closed`), a close request marker, and a
stored wrap-up. An idle close transitions through closing to closed. An active close records the
request and the run-finalization path performs wrap-up and archive after the turn completes. The
session mapping is retained and the thread is archived but not locked. Reopen reverses the state and
unarchives the thread.

The current helper's kill/delete/archive behavior is retained only for genuinely disposable
internal threads under a separately named operation; it cannot back `/close`. Treating close as an
alias for stop was rejected because stop is intentionally resumable without archival or wrap-up.

### Human and agent entry points use the same authorization object

The close service accepts a typed authority source: direct authorized interaction, explicit current
user instruction, or a workflow configured for close-on-done. Internal callers must provide that
source; absence fails closed and is auditable. Natural-language recognition may request the service
but cannot mint authority from model intent.

## Risks / Trade-offs

- [Discord still shows the eight-command union in its picker] → Make the buttons and `/help`
  genuinely location-specific and return a short correction for invalid locations.
- [Refreshing after every message can hit Discord rate limits] → Debounce, serialize per channel,
  ignore bot/system messages, and replace only when the saved row is no longer last-useful content.
- [Deleting the old control message can fail] → Save the new id first; a later repair pass may
  remove an older bot-owned row, but no action is lost.
- [Create/Clone can alter disk state] → Constrain targets to approved roots, avoid shell execution,
  and use attempt-owned cleanup only.
- [Close during a run can be lost on restart] → Persist the closing marker before acknowledging and
  let startup reconciliation complete it idempotently.
- [Retiring commands can strand an edge case] → Keep retirement as a separate final task with a
  feature/config rollback and preserve all repositories and service methods.

## Migration Plan

1. Add schema fields and shared services with backward-compatible defaults; existing rows read as
   open.
2. Add the new views and slash commands while all old commands remain registered.
3. Run focused unit/integration checks, then enable the replacement surface in the local Discord dev
   worktree for Drew's try-out.
4. After acceptance, remove only superseded command registrations and old launcher buttons; retain
   the underlying shared operations and stored data.
5. Roll back by restoring old command registration and launcher view. New lifecycle columns and
   closed records remain backward-compatible and must not be dropped.
