# Go Work OSS Orchestration Patterns

Primary-source scan for Drew's Go Work orchestration audit. The goal is not to adopt a full framework; it is to copy small, proven shapes for envelopes, grouped parallel work, status/trace views, and dependency graphs.

## 1. Agent Handoffs And Message Envelopes

### A2A Protocol

Source: https://github.com/a2aproject/A2A/blob/main/docs/specification.md

- What to copy:
  - Use an explicit envelope split between a direct `Message` and a long-running `Task`.
  - Give every run a durable `taskId`, every conversation a `contextId`, and optionally link related tasks through references.
  - Model progress as task status events plus artifact events, not as unstructured chat text.
  - Support three update paths in the internal API: poll current task, stream live updates, and push notifications/webhooks.
  - Publish a small local "agent card" for each worker type: name, capabilities, supported input/output modes, and auth/permission notes.
- What NOT to copy:
  - Do not implement the whole public internet protocol surface, multi-transport equivalence, gRPC, authenticated extended cards, or cross-organization credential flows yet.
  - Do not pass credentials through handoff messages; A2A itself warns that credential handling needs secure out-of-band treatment.
- Why it maps to Go Work:
  - Go Work already has Discord threads, sessions, loops, claims, and task state. A2A gives us a clean envelope vocabulary so every agent handoff can say: "this is the task, this is the context, this is the status, these are the artifacts."

### OpenAI Agents SDK Handoffs

Source: https://openai.github.io/openai-agents-python/handoffs/

- What to copy:
  - Represent a handoff as a tool-like action with a predictable name such as `transfer_to_reviewer` or `transfer_to_frontend_worker`.
  - Attach typed handoff metadata such as `reason`, `priority`, `summary`, `blocked_on`, or `requested_output`.
  - Run an `on_handoff` callback to persist the transition before the next worker starts.
  - Add an input filter so the receiving agent gets only the useful context, not the whole transcript by default.
  - Make handoff destinations explicit; register one destination per worker role instead of asking the model to invent routing.
- What NOT to copy:
  - Do not let model-only routing be the source of truth for Go Work dispatch.
  - Do not forward full history by default; the SDK docs call out filtering and warn that summaries can still leak tool content.
  - Do not rely only on the hosted OpenAI trace UI for Go Work's local operational dashboard.
- Why it maps to Go Work:
  - Go Work handoffs need compact, durable transition records between planner, worker, reviewer, and Drew. The SDK's handoff shape is a good local model: named destination, typed payload, callback, filtered context.

### LangGraph Swarm

Source: https://github.com/langchain-ai/langgraph-swarm-py

- What to copy:
  - Keep an `active_agent` field in shared state so a resumed thread knows which specialist was last responsible.
  - Use explicit handoff tools that update state, append a handoff marker, and route to the next named agent.
  - Compile with checkpointing for multi-turn continuity.
  - Allow custom handoff tools to pass a task description or narrowed context to the next agent.
- What NOT to copy:
  - Do not use one shared message list as the only memory for all workers; the README notes this can expose one agent's internals to another.
  - Do not make Go Work a fully free-form swarm where workers endlessly hand off to each other.
- Why it maps to Go Work:
  - Go Work's Discord threads already act like long-running conversations. Copy the `active_agent` and checkpoint idea so a run can resume cleanly after interruption, compaction, or a human reply.

## 2. Swarm / Orchestrator Grouping Of Parallel Work

### LangGraph Workflow Patterns

Source: https://docs.langchain.com/oss/python/langgraph/workflows-agents

- What to copy:
  - Use the orchestrator-worker pattern: one planner breaks work into subtasks, workers run in parallel, then a synthesizer merges results.
  - Use explicit graph edges for prompt chaining, routing, parallel fan-out, and aggregation.
  - Treat worker output as structured state collected under a shared key, not as scattered chat logs.
  - Render the graph with Mermaid for cheap visual inspection.
- What NOT to copy:
  - Do not adopt LangGraph itself unless Go Work needs a Python runtime dependency.
  - Do not make every Go Work task dynamic; predictable checklist tasks are still easier to test and audit.
- Why it maps to Go Work:
  - Go Work's plan checkboxes and session workers line up with planner -> worker fan-out -> reviewer/synthesizer. The useful pattern is the control shape, not the library.

### AutoGen Swarm

Source: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/swarm.html

- What to copy:
  - Let a planner/coordinator delegate to named specialists and require specialists to hand back to the planner when done.
  - Include a human target as a first-class handoff destination when the run needs Drew's input.
  - Use explicit termination conditions, not "conversation got quiet" heuristics.
  - Record message count, finish reason, token totals, and duration in the run summary.
- What NOT to copy:
  - Do not copy shared full-context group chat as the default.
  - Do not allow parallel tool calls to create multiple simultaneous handoffs; AutoGen specifically warns this can behave unexpectedly.
- Why it maps to Go Work:
  - Go Work needs a bounded "planner delegates, worker returns, planner closes" rhythm. AutoGen's examples show that pattern clearly, including human pause/resume.

### AutoGen GraphFlow

Source: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html

- What to copy:
  - Model strict workflows with a directed graph when order matters.
  - Support sequential chains, parallel fan-outs, joins, conditional branches, and loops with safe exits.
  - Start from source nodes and complete when no nodes remain executable.
  - Validate the graph before running.
- What NOT to copy:
  - Do not depend on experimental APIs or expose a low-level graph builder to Drew as the main planning surface.
- Why it maps to Go Work:
  - Go Work plans are already dependency graphs hiding inside checkboxes. GraphFlow gives a clean vocabulary for "these tasks can run now, these are waiting for a join, this branch depends on a result."

## 3. Run / Status Dashboards And Trace Views

### OpenAI Agents SDK Tracing

Source: https://openai.github.io/openai-agents-python/tracing/

- What to copy:
  - Use trace/span hierarchy: one workflow trace, child spans for task run, model turn, agent, tool call, handoff, guardrail, and custom events.
  - Use a `group_id` to tie multiple traces to the same conversation or Discord thread.
  - Add metadata to traces so the dashboard can filter by plan, worker, branch, model, status, and cost bucket.
  - Flush trace exports at the end of background jobs so completed Go Work sessions show up quickly.
  - Support custom processors/exporters so Go Work can store traces locally and optionally mirror them elsewhere.
- What NOT to copy:
  - Do not make OpenAI-hosted tracing the only source of truth.
  - Do not log sensitive prompt, tool, or environment data without a redaction policy.
- Why it maps to Go Work:
  - Go Work needs a run view that answers "what happened, where is it stuck, which handoff changed ownership, and what artifact came out?" Trace/span hierarchy is the right mental model.

### AutoGen Studio

Sources:
- https://microsoft.github.io/autogen/stable/user-guide/autogenstudio-user-guide/usage.html
- https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-studio/README.md

- What to copy:
  - Provide a visual builder/card view where teams, agents, tools, and termination conditions are visible.
  - Keep a JSON/declarative representation behind the visual view so runs are reproducible.
  - Let users inspect and edit a team/agent node through a side panel rather than forcing raw JSON.
  - Use a component library/gallery for reusable worker roles and run templates.
- What NOT to copy:
  - Do not copy AutoGen Studio as a production app; its README says it is a prototype and lacks production security features.
  - Do not make Go Work a drag-and-drop agent builder before the core run/status view is solid.
- Why it maps to Go Work:
  - Drew needs to see active runs and reusable worker patterns without reading raw logs. The useful bit is "visual surface backed by declarative JSON," not the whole Studio product.

### Prefect OSS

Sources:
- https://docs.prefect.io/v3/get-started
- https://docs.prefect.io/v3/concepts/tasks
- https://github.com/PrefectHQ/prefect

- What to copy:
  - Treat tasks as small retryable units with tracked runtime, final state, logs, and state transitions.
  - Use futures for concurrent task submission, and resolve downstream dependencies automatically when a task consumes an upstream result.
  - Surface a local server/UI for recent runs, states, logs, and flow graphs.
  - Keep task-level observability granular enough to identify bottlenecks.
- What NOT to copy:
  - Do not turn Go Work into a general data pipeline orchestrator.
  - Do not copy Prefect Cloud concepts that are not needed locally.
- Why it maps to Go Work:
  - Prefect is not agent-specific, but its OSS task/run visibility is exactly the boring operational layer Go Work needs: queued, running, failed, retrying, completed, logs, and dependencies.

## 4. Task Dependency Graphs

### CrewAI Flows

Source: https://docs.crewai.com/en/concepts/flows

- What to copy:
  - Use `start`, `listen`, `router`, `and`, and `or` style concepts for Go Work dependency semantics.
  - Give every flow/run state a stable ID and preserve state across steps.
  - Support structured state for important plans so validation catches malformed run data.
  - Generate an HTML/interactive plot of the flow so users can inspect nodes, edges, and data flow.
  - Use persisted state for resume/fork semantics.
- What NOT to copy:
  - Do not copy CrewAI's agent abstraction wholesale.
  - Do not make every Go Work dependency a decorator/API concern; Drew's authored plan still needs to stay readable as checkboxes.
- Why it maps to Go Work:
  - Go Work can keep plans human-readable while internally compiling them into start/listen/router/dependency edges. CrewAI's flow vocabulary is a simple bridge from checklist to graph.

### AutoGen GraphFlow, Again

Source: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html

- What to copy:
  - Directed graph execution with nodes as agents/tasks and edges as allowed transitions.
  - Parallel fan-out plus fan-in join for independent workers that must be reviewed together.
  - Conditional edges for branch decisions based on worker output.
- What NOT to copy:
  - Do not expose graph internals as the authoring format unless Drew explicitly wants a graph editor.
- Why it maps to Go Work:
  - The planner can compile a plain-English task list into a graph, run the ready frontier in parallel, then summarize the join result back into Discord.

## Recommended Copy Set For Go Work

- Envelope: A2A-style `contextId`, `taskId`, `message`, `task`, `statusUpdate`, `artifactUpdate`.
- Handoff: OpenAI/LangGraph-style named handoff with typed payload, ownership change, filtered context, and persisted callback.
- Parallel grouping: LangGraph/AutoGen-style planner -> parallel workers -> join/synthesizer.
- Dashboard: OpenAI trace/span hierarchy plus Prefect-style task states/logs, stored locally as Go Work's source of truth.
- Dependency graph: CrewAI/AutoGen-style start/listen/router/fan-out/fan-in model, generated from Drew-readable checklist plans.

## Short Implementation Implication

Go Work should not become A2A, LangGraph, AutoGen, CrewAI, or Prefect. It should stay Discord-native and local-first, but adopt their small durable shapes: explicit envelopes, typed handoffs, visible ownership, structured status events, run traces, and a dependency graph behind the checklist.
