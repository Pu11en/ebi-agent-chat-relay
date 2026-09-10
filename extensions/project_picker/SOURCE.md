# Fork-derived project picker

Source: https://github.com/cKreymborg/claude-code-discord-bridge/commit/780510047b2317d5ba8a45a6c60d963341209568

Original files: `claude_discord/workdir.py`, `claude_discord/cogs/workdir_command.py`, and their two test files. MIT license retained in LICENSE. Imported 2026-09-07; Ebi core at 7fc303c remains unchanged.

Local adaptations: combined the two modules into one supported custom Cog, added its setup entry point, connected current Ebi permissions/channel settings, checked autocomplete authorization, offloaded directory scanning from the Discord event loop, normalized chosen paths, avoided overlong Discord choice values, prevented `/cd` during active runs using Ebi's thread lock, deferred `/cdnew` responses, and joined the requesting user to the new thread. No change to Ebi planning, execution, backend selection or worker coordination.

Set `CUSTOM_COGS_DIR` to this folder and `CCDB_PROJECT_ROOTS=/home/drewp/main-projects` in the existing deployment .env. These are standard configuration hooks. Removing CUSTOM_COGS_DIR and restarting disables this extension.

Everyday use: in #control-center run `/cdnew`, type part of a project name in `path`, choose the suggested folder, then open the linked thread and talk normally. Existing folders do not need a GitHub remote for selection or planning. Coding worktrees need local Git initialization. Continue an existing conversation by reopening its Discord thread, not `/cdnew`. `/cd` starts fresh context in the current thread; it is not the resume command.

The project root config controls suggestions, not a filesystem security boundary. This extension is owner-only, like the configured Ebi chat. Existing directory paths are also accepted explicitly, matching the fork. It does not create or move projects.

Verification: `uv run pytest -q extensions/project_picker/tests` (29 passed) and `uv run ruff check extensions/project_picker`. Live command registration, autocomplete lookup, actual Discord thread creation through the command callback, real session database bindings, Codex execution in the selected directory, and continuation of the same Codex session were checked. Actual Discord-client slash interaction clicks still belong to the user's first try; no user account or browser was automated.
