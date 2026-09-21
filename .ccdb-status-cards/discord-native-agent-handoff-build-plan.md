# Discord-Native Agent Handoff Build Plan

## What we are building

- ✅ **Goal:** David’s agent can ask DrewAI’s agent to do a task, and DrewAI sends the result back to David’s original thread.
- ✅ **Transport:** Discord messages/threads, not Tailscale and not a public network API.
- ✅ **First use case:** David asks DrewAI to search Drew’s main projects folder.
- ✅ **Later use cases:** iMac ↔ DrewAI, DrewAI ↔ David, and other bot-to-bot task handoffs.
- ✅ **User experience:** Drew says normal words like “ask DrewAI to look in Drew’s projects for Realpage,” and the bot makes the handoff.

## Important finding from the code

- ✅ The repo already has a strong handoff foundation.
- ✅ Existing files already cover the hard parts:
  - `claude_code_core/handoffs/protocol.py`
  - `claude_code_core/handoffs/state.py`
  - `claude_discord/database/handoff_repo.py`
  - `tests/test_handoff_protocol.py`
  - `tests/test_handoff_repository.py`
- ✅ Existing tables already include:
  - `handoff_tasks`
  - `handoff_events`
  - `handoff_attempts`
  - `handoff_results`
  - `handoff_outbox`
- ✅ This means we should **not invent a new ledger**.
- ⏳ The missing part is the Discord-facing wiring that turns this existing protocol into real bot-to-bot behavior.

## The handoff packet

- Use the existing trusted handoff protocol.
- Each handoff has:
  - `task_id`
  - `sender`
  - `recipient`
  - `origin`
  - `reply_to`
  - `project`
  - `goal`
  - `expected_result`
  - `authority`
  - `created_at`
  - `expires_at`
- For Drew’s project lookup case:
  - `sender`: `david`
  - `recipient`: `drewai`
  - `project.owner`: `drew`
  - `project.folder`: `main-projects`
  - `authority`: read-only
  - `goal`: search Drew’s projects for the requested thing
  - `expected_result`: exact paths, relevant files, and short summary
  - `reply_to`: David’s original Discord thread

## Discord flow

- ⏳ **Step 1: David creates a handoff.**
  - David’s bot turns the request into a validated handoff task.
  - The task is stored locally before posting.
  - The task is posted into DrewAI’s configured handoff inbox thread/channel.

- ⏳ **Step 2: DrewAI receives it.**
  - DrewAI only accepts messages with the trusted handoff envelope.
  - Normal bot chatter is still ignored.
  - Duplicate task IDs are ignored safely.
  - Messages addressed to a different bot are ignored.

- ⏳ **Step 3: DrewAI runs the right worker.**
  - For `project.owner=drew` and `project.folder=main-projects`, DrewAI starts a project lookup worker.
  - The worker is read-only.
  - The worker runs in Drew’s project root.
  - The handoff ledger records the worker thread.

- ⏳ **Step 4: DrewAI returns the result.**
  - When the worker finishes, DrewAI creates a result event.
  - The result is stored in the ledger.
  - The result is posted back to David’s original thread from `reply_to`.
  - If posting fails, it stays in the outbox and retries later.

- ⏳ **Step 5: Worker thread closes.**
  - Once the result is captured and returned, the worker thread archives/closes.

## Build pieces

- ⬜ **1. Handoff message formatter/parser**
  - File: likely `claude_discord/handoff_messages.py`
  - Converts protocol events into Discord-safe text.
  - Parses Discord text back into a validated event.
  - Must include a clear marker like `CCDB_HANDOFF_V1`.

- ⬜ **2. Handoff inbox config**
  - Env/config:
    - `CCDB_AGENT_ID=drewai` or `david`
    - `CCDB_HANDOFF_INBOX_CHANNEL_ID=...`
    - `CCDB_TRUSTED_HANDOFF_AGENTS=david,drewai,imac`
  - The inbox can be one Discord thread/channel per bot.
  - No public API exposure required.

- ⬜ **3. Discord listener**
  - File: likely a new Cog, `claude_discord/cogs/handoff_inbox.py`
  - Watches messages in the configured inbox.
  - Allows only trusted structured bot messages.
  - Rejects malformed, duplicate, expired, wrong-recipient, or looped tasks.

- ⬜ **4. Handoff execution dispatcher**
  - Maps accepted tasks to local executors.
  - First executor: DrewAI project lookup.
  - Later executors: build task, research task, file search, content workflow.

- ⬜ **5. Result returner**
  - Reads `handoff_outbox`.
  - Posts result back to `reply_to`.
  - Marks delivery as delivered.
  - Retries later if Discord posting fails.

- ⬜ **6. Natural text trigger**
  - Detects safe phrases:
    - “ask DrewAI”
    - “ask DrewAI to search”
    - “look in Drew’s projects”
    - “use DrewAI to find”
  - Creates the handoff automatically.
  - Only triggers when the target agent and task are clear.

## Safety rules

- ✅ Do not let any remote agent start arbitrary local work.
- ✅ Only a valid `task` event can create work.
- ✅ `sender` and `recipient` must be different.
- ✅ `task_id` prevents duplicate execution.
- ✅ sequence numbers prevent out-of-order replay.
- ✅ expiration prevents stale work.
- ✅ read-only stays read-only unless Drew explicitly grants edit authority.
- ✅ normal bot messages stay ignored.
- ✅ malformed handoffs are recorded or rejected, not executed.

## Recommended build order

- ⏳ **Build 1: Discord envelope parser/formatter.**
  - Small, testable, no live Discord needed.
  - Proves handoff messages can survive Discord text.

- ⏳ **Build 2: Handoff inbox listener.**
  - DrewAI can receive a structured task in Discord and store it.
  - No worker execution yet.

- ⏳ **Build 3: Project lookup executor.**
  - DrewAI maps read-only project lookup handoffs to the existing project lookup worker.

- ⏳ **Build 4: Result return/outbox.**
  - DrewAI sends the answer back to David’s original thread.

- ⏳ **Build 5: David-side sender.**
  - David can create the handoff and post it into DrewAI’s inbox.

- ⏳ **Build 6: Natural text trigger.**
  - “Ask DrewAI…” becomes automatic.

## First acceptance test

- David thread says: “ask DrewAI to search Drew’s projects for Realpage.”
- David bot creates a trusted handoff event.
- DrewAI receives it in its Discord inbox.
- DrewAI starts a read-only project lookup worker.
- DrewAI posts a result back into David’s original thread.
- The worker thread archives after the result is captured.
- Reposting the same handoff does not run the lookup twice.

## What this means in plain English

- We are not building a network bridge first.
- We are not making DrewAI’s API public.
- We are using Discord as the handoff lane.
- The code already has the serious handoff ledger and safety state machine.
- The next real build is connecting that system to Discord messages and project lookup workers.
