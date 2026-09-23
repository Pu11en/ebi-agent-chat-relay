## Why

The current Discord installation exposes roughly thirty overlapping commands and buries control
panels in channel history. Drew needs a small, location-aware interface that keeps common actions
visible and moves advanced operations behind clear menus without deleting working behavior early.

## What Changes

- Keep one refreshed control row near the bottom of each control-center with New session,
  Sessions, and Settings, plus an always-visible computer status line.
- Make New session open Favorites, Recent, Browse, Create, and Clone, then create a clean idle
  workers thread using the computer's default model.
- Make Sessions searchable and newest-first with Open, New in same folder, and Close.
- Limit the visible control-center command surface to `/new`, `/sessions`, `/settings`, and
  `/help`; limit session threads to `/switch`, `/stop`, `/session`, `/close`, and `/help`.
- Make `/session` expose Fork, Rewind, Compact, Clear, Context, and Goal.
- Make `/switch` select a model directly and resolve its harness automatically.
- Add a shared close operation that wraps up, closes, and archives a session without deleting or
  permanently locking it, usable by humans and preauthorized agents.
- Retire superseded commands only after their replacements pass local tests and Drew's Discord
  try-out.

## Capabilities

### New Capabilities
- `discord-command-surface`: Location-aware control-center and session actions, including the
  persistent bottom control row and context-specific help.
- `session-lifecycle`: Reopenable close/archive behavior shared by slash commands, menus, natural
  language, and authorized agent workflows.

### Modified Capabilities

None.

## Impact

This affects Discord Cogs, command registration, launcher/session UI, session persistence, help,
tests, and the EbiBot instance wiring. Discord may still display other bots' registered commands to
administrators until trusted routing is deployed; execution must remain category-scoped.
