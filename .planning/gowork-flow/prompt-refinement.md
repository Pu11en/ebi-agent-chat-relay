# Existing Planning Flow: Prompt Refinement

Status: draft for discussion, not installed in global instructions or the live bot.
Latest direction: improve the existing experience and its execution handoff, using selective inspiration from Superpowers, GSD and BMad. Drew chose a real-example walkthrough plus improvements on both planner and executor sides. Long card displays usually go unread.

## Planner Instructions to Refine

```text
Continue the user's existing planning conversation and preserve settled answers.
Keep detailed plans on disk; do not automatically send the whole plan or a stack
of cards whenever it changes. Show the part that helps the current decision.
Use a short concrete example before introducing new workflow terminology.
Keep chat brief and ask one unresolved decision at a time, with meaningful
lettered choices and a recommendation. Explain unfamiliar options simply.
Look up facts yourself; do not ask the user to solve implementation details.
Record the answer and its consequences before asking the next question.
Continue until required user decisions are resolved; do not mistake short chat
for permission to leave gaps in the detailed plan. Obtain technical facts from
the project and record routine engineering choices without needless questions.
For each agreed outcome, specify a small worker task, required inputs, owned
files/resources, output and check. Preserve all agreed scope across the tasks.
Identify what can run independently and what must wait for another result.
Do not add workers just to increase their count; useful ready work determines
demand, and the running system determines available capacity.
Check the plan against the goal before calling it ready. A short user-facing
summary must not mean an underspecified worker assignment.
```

## Execution Instructions to Refine

```text
Read the complete task assignment, saved decisions and required inputs before
deciding whether tasks can run together; titles alone are insufficient.
Honor explicit prerequisites and ownership. If information is missing, inspect
the project and saved plan before guessing a dependency or asking the user.
Separate task readiness from current machine/provider capacity.
Assign only the intended outcome and enough context to a fresh worker.
Preserve its result and check evidence; release execution capacity when done.
Validate combined outputs before unblocking dependent work.
When a task gets stuck, keep independent work moving and attempt bounded
repairs within the agreed repair allowance and shared capacity. Keep its
dependents waiting; do not mark a failed result complete to unblock them.
Report only the blocker or milestone that changes what the user needs to know.
Do not reopen answered questions or infer permission to launch from a planning
answer. Report unsupported runtime behavior honestly.
```

These instructions require code integration: today's grouping helper receives checkbox labels, not full task blocks. Changing a prompt string alone cannot provide missing context, enforce dependencies, adapt capacity or guarantee cleanup.

## Walkthrough Using the Existing Report Plan

Source: local-plans/generation-wealth-feedback-gowork.md, inspected as a plan, not proof of current execution status.

- Current six-task chain: inventory transcripts, extract statements, group themes, draft report, create PDF, review PDF.
- Each task consumes the preceding output, so the current plan is mostly sequential; raising the worker cap does not make its downstream tasks ready.
- The existing plan has details under task titles. Workers are told to read the full plan, but the grouping helper sees only titles.
- Proposed planner improvement: say plainly that the report first needs a reliable source list; keep paths, speaker rules and checks in the worker assignment.
- Proposed executor improvement: consume the task inputs and outputs when deciding readiness, rather than infer those from titles.
- Possible future split, only if worth doing: after inventory, workers could extract different independent sources into separate files, followed by a combining/checking task. Do not invent source counts or launch this change; shared output files would need separate ownership first.
- Example short user-facing explanation: "The report needs a source list first; then separate sources could be reviewed together before one worker combines the findings."
- The original Check/try fields and checklist format remain the compatibility starting point; this prompt discussion does not modify or rerun the report plan.

## What We Borrow

- Superpowers: establish intent from existing context, ask focused questions, and provide concrete worker inputs and outputs.
- GSD: preserve decisions, record what each task needs and creates, check ownership conflicts and cover agreed requirements.
- BMad: judge whether tasks can be implemented without inventing missing decisions, loading only the relevant stage instructions.
- Do not import whole upstream roles, installers, mandatory menus, publishing behavior or repeated approval ladders.
- Exact inspected revisions, links and retained license files are recorded in planner-audit.md and planner-skill-draft/references/licenses/.

## Review Cases

- Report plan: preserve its real dependency chain; no unsupported promise of six-way parallel execution.
- Business plan: retain website/product/marketing scope while showing only the current useful decision.
- Answer continuation: keep automatic resource-aware sizing settled; do not ask for a numeric cap again.
- Readability: no automatic full-plan card dump, one unresolved question, and no technical detail the user must decode to answer.
- Evidence boundary: these are prompt examples reviewed against local code and plans, not results of a live-model evaluation.

## Still Open

- Interaction settled: short multiple-choice questions with strong distinct options, continuing until required answers are recorded; detailed internal plans remain essential.
- Name settled: improve Go Work and its loops, not a separately named replacement.
- Recommended loop model: durable coordinator, independent child-plan lanes and fresh short-lived task workers; the coordinator owns retries, dependency release and shared capacity instead of letting workers spawn unlimited private loops.
- Failure policy settled: keep independent work moving and try limited repairs; dependent tasks wait for a checked result.
- Repair allowance, capacity priority across builds and retention remain open.
