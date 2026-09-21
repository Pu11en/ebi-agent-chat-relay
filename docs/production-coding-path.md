# Discord agent server: coding path to production

## 1. What we have today

✅ This is an existing Python application with reusable Discord commands, multiple agent backends, saved sessions, automated checks, and startup recovery code.

✅ The local health endpoint reported **OK with zero overdue notifications** during this inspection.

⚠️ This is a focused code and configuration inspection, **not a completed production audit**: tests, security isolation, load handling, and recovery were not exercised.

**The first priority is making the code we edit, test, and run unambiguous.**

## 2. Where changes actually run

- The service starts from `/home/drewp/main-projects/ebi-agent-chat-relay`.
- A development setting at `~/.ccdb-dev-worktree` points to `/home/drewp/main-projects/wt-task-loop`, on branch `feat/task-loop`.
- A fresh Python process in the service environment loads both the Discord package and shared agent core from that development checkout; this checks import routing, not the modules already loaded inside the running process.
- **Editing the main folder alone does not update the configured Discord application code.** Personal extensions may load from separately configured locations and need their own check.
- The main folder contains pre-existing uncommitted work; the development checkout also has an untracked document. Preserve both when preparing changes.
- Startup code can pull changes, refresh dependencies, and select a recovery checkout. A restart therefore needs review of the checkout and startup settings before it happens.

## 3. Follow a message through the code

**Discord message → command handler → run coordinator → selected agent → response renderer → Discord reply.**

Paths below are relative to the checkout being changed.

- **Receive messages and choose the thread/session:** `claude_discord/cogs/claude_chat.py`.
- **Build the prompt and include attachments:** `claude_discord/cogs/prompt_builder.py`.
- **Coordinate a run and process its events:** `claude_discord/cogs/_run_helper.py` and `claude_discord/cogs/event_processor.py`.
- **Run Claude, Codex, or DeepSeek:** `claude_code_core/runner.py`, `codex_runner.py`, and `dsh_backend.py`.
- **Display progress, buttons, and replies:** `claude_discord/discord_ui/`; reusable message splitting lives in `claude_code_core/rendering/`.
- **Store sessions and scheduled work:** `claude_discord/database/`, with shared session support in `claude_code_core/`.
- **Accept control requests from agents and scripts:** `claude_discord/ext/api_server.py`.

## 4. Pick the right place for a change

- **New reusable Discord command:** add a command module under `claude_discord/cogs/`, wire it into setup, and add tests; consumers should receive it by updating the package.
- **Personal workflow for your server:** use the custom extension loader, `claude_discord/cog_loader.py`, with the examples under `examples/ebibot/cogs/`; keep personal configuration out of the reusable package.
- **Agent behavior, cancellation, or resuming work:** inspect the run coordinator and selected backend together; changing only the display can hide an agent that is still running.
- **Reply cards, formatting, and attachments:** work in `claude_discord/discord_ui/`, using shared rendering where the behavior also applies outside Discord.
- **Automatic multi-task builds:** in the development checkout, start with `claude_discord/cogs/task_loop.py` and `claude_code_core/task_loop.py`.
- **Startup, version selection, or recovery:** start with `scripts/pre-start.sh`, `scripts/deploy-checkout.sh`, and `claude_discord/deployment.py`.

Example: fixing a Stop button means following the button handler through the run coordinator to subprocess cancellation, then checking that the child process actually exits and the thread can run again.

## 5. What production work should prove

These are priorities to verify and scope, not claims that every feature is missing.

**Release reliability comes first.** Establish one reviewed code version, a separate test instance with its own bot credentials, database, and ports, plus a demonstrated route back to the previous working version.

**Cover the actual application in automated checks.** Current CI runs lint, format, and type checks over `claude_discord/` and `claude_teams/`, but omits `claude_code_core/` from those explicit commands. Tests may still exercise the core; coverage enforcement currently targets the Discord package. Extend the checks with a measured baseline.

**Prove session reliability.** Exercise cancellation, timeouts, failed agents, interrupted delivery, simultaneous threads, and restart behavior; define what resumes and what reports failure.

**Verify access and isolation.** Check who can trigger commands and control endpoints, what files and credentials an agent can reach, and how those boundaries change if people outside your own server use it.

**Prove recovery and visibility.** Restore a database backup in a test instance, observe stuck jobs and delivery failures, and report the exact running version. A successful health response alone cannot prove the full message-to-agent-to-reply path works.

## 6. The coding and release loop

1. Choose one visible outcome and a check that proves it.
2. Confirm the intended checkout and claim the affected area before editing.
3. Write a failing test for the bug or new behavior, then implement the smallest complete change.
4. Run the relevant tests and required lint, formatting, type, and security checks; save a local commit containing only that change.
5. Try the behavior in an isolated Discord test instance, including its failure case.
6. Let Drew try it and confirm the behavior before publishing; then use the agreed GitHub review and release process.
7. Switch the live server only with active sessions accounted for, check end-to-end behavior, and retain the previous working version for recovery.

✅ This inspection changes documentation only; it does not restart the server, publish code, launch paid model tests, or implement the production work above.

**Scope decision:** a dependable personal server, a private team server, a public installable project, and a hosted product require different access controls and operating commitments. Choose that audience before creating the implementation tasks.
