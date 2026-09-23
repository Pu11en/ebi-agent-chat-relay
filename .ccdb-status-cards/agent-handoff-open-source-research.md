# Open Source Agent Handoff Research

## Best answer for DrewAI and David

- ✅ **Use Discord as the transport**, not Tailscale or a public API.
- ✅ **Copy the protocol ideas** from open-source agent systems.
- ✅ **Do not install a huge multi-agent framework** just to make two Discord bots talk.
- ✅ Build a small `handoff` system inside ccdb:
  - David bot creates a handoff request.
  - DrewAI bot sees the request in Discord.
  - DrewAI starts the right worker.
  - DrewAI posts the result back to David’s original thread.
  - Both sides use a `handoff_id` so replies cannot get lost.

## What to copy from A2A

- Source: A2A GitHub repo — https://github.com/a2aproject/A2A
- Source: A2A spec — https://github.com/a2aproject/A2A/blob/main/docs/specification.md
- Source: Google A2A announcement — https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/

- ✅ A2A has the closest shape to what we need:
  - **client agent** asks a **remote agent** to do a task.
  - every request becomes a **task** with a lifecycle.
  - task output becomes an **artifact**.
  - agents send messages with context, replies, artifacts, and user instructions.
  - long-running tasks can send updates and final results later.

- ✅ What we should copy:
  - `handoff_id`
  - `from_agent`
  - `to_agent`
  - `source_thread_id`
  - `reply_thread_id`
  - `task`
  - `status`
  - `artifact/result`
  - `created_at`
  - `parent_handoff_id`

- ⚠️ What we should not copy yet:
  - the full HTTP/JSON-RPC server shape.
  - network discovery.
  - public Agent Cards.
  - push webhooks.

- Why: A2A assumes agents can reach each other by network URL. Drew wants the agents talking through Discord, so we only need the task/result model.

## What to copy from LangGraph Swarm

- Source: LangGraph Swarm GitHub repo — https://github.com/langchain-ai/langgraph-swarm-py
- Source: LangGraph Swarm license — https://raw.githubusercontent.com/langchain-ai/langgraph-swarm-py/main/LICENSE

- ✅ LangGraph Swarm has a good handoff idea:
  - agents have specialized roles.
  - one agent can hand control to another agent.
  - the system remembers which agent is active.
  - custom handoff tools can include a task description for the next agent.

- ✅ What we should copy:
  - make “Ask DrewAI” a tool-like action, not a normal chat message.
  - require a clear `task_description`.
  - keep the original thread as the place where the answer returns.
  - record who currently owns the task.

- ⚠️ What we should not copy yet:
  - the whole LangGraph runtime.
  - shared message history between agents.
  - model-level autonomous handoff between lots of agents.

- Why: ccdb already has Discord threads, workers, session state, and SQLite. We need the handoff pattern, not the full graph engine.

## What to copy from AutoGen

- Source: AutoGen GitHub repo — https://github.com/microsoft/autogen
- Source: AutoGen code license — https://raw.githubusercontent.com/microsoft/autogen/main/LICENSE-CODE

- ✅ AutoGen is useful for the old multi-agent idea:
  - agents can communicate through messages.
  - one agent can use another agent like a tool.
  - a higher-level assistant can choose an expert agent.

- ✅ What we should copy:
  - “agent as tool” thinking.
  - one agent asks another agent a bounded question.
  - return value comes back as the last useful message.

- ⚠️ What we should not copy:
  - AutoGen as a dependency.
  - its full runtime.

- Why: AutoGen is now in maintenance mode, and its own repo points new users toward Microsoft Agent Framework. It is still good as a pattern source, not as the thing to build on.

## What to copy from discord.py

- Source: discord.py docs — https://discordpy.readthedocs.io/en/stable/

- ✅ ccdb already uses Discord and Python.
- ✅ discord.py already supports:
  - async message handling.
  - rate-limit handling.
  - commands.
  - background tasks.

- ✅ What we should build with it:
  - a hidden or quiet **handoff inbox thread** per bot.
  - structured handoff messages posted by bots.
  - a listener that accepts bot-authored handoff messages only when they match the schema.
  - a result poster that sends the answer back to the original thread.

- ⚠️ Important guard:
  - regular bot messages should still be ignored.
  - only signed/structured handoff envelopes should bypass the “ignore bots” rule.

## Recommended build

- ⏳ **Build a Discord Handoff Ledger.**
  - SQLite table: one row per handoff.
  - Fields: id, source agent, target agent, source thread, target thread, status, request text, result text, timestamps.

- ⏳ **Build a Handoff Inbox.**
  - Each bot has a configured Discord thread/channel where structured handoff messages land.
  - Example message:
    - `HANDOFF v1`
    - `handoff_id: ...`
    - `to: drewai`
    - `from: david`
    - `reply_thread_id: ...`
    - `task: search Drew’s projects for realpage folder`

- ⏳ **Build a Handoff Runner.**
  - DrewAI sees a request addressed to DrewAI.
  - It starts a project lookup worker.
  - It records the worker thread.
  - When the worker finishes, it posts the result back to David’s original thread.

- ⏳ **Build loop protection.**
  - max hops.
  - idempotency key.
  - only one active handoff per id.
  - reject if `from_agent == to_agent`.
  - reject duplicate completed requests.

- ⏳ **Build natural text later.**
  - After handoff works, “ask DrewAI to search Drew’s projects” becomes a normal phrase that creates the structured handoff.

## Final recommendation

- ✅ Build the small Discord-native handoff system.
- ✅ Use **A2A’s task/artifact idea** for the data shape.
- ✅ Use **LangGraph Swarm’s handoff-tool idea** for the agent behavior.
- ✅ Use **AutoGen’s agent-as-tool idea** for the mental model.
- ✅ Use **discord.py** for the actual transport.
- ⚠️ Do not expose DrewAI’s API over the network just to solve this.
- ⚠️ Do not install a whole orchestration framework unless the handoff system grows beyond two or three bots.
