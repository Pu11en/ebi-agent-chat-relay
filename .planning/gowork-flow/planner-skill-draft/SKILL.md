---
name: master-planning
description: Guide project brainstorming, clarify requirements through short questions, and turn agreed decisions into coordinated plans for fresh-session builders. Use for planning a project, business, feature, or several related build lanes; resume when the user answers an active planning question. Do not invoke for an already scoped small fix, a factual question, or executing an existing plan.
---

# Master Planning

Not accepted as the direction for this work. Drew clarified that his existing planning style should remain; he wants clearer instructions for how plans are executed. Retained for reference only, not installed or active globally.

## Purpose

Keep the user's planning conversation coherent from early ideas through build-ready tasks. A fresh builder should understand its job without replaying the conversation. Preserve the user's intended scope while splitting delivery into manageable pieces.

## Recover Before Asking

- Read the project's instructions, the relevant code or existing work, and the selected planning effort's saved state.
- Reuse the existing master plan and decision record; give a new effort a distinct directory instead of overwriting another effort.
- Classify the current activity as exploring ideas, resolving decisions, writing tasks, or checking readiness. Continue from the current stage; do not restart an interview when an answer arrives.
- Extract settled answers from the conversation before generating questions. Record assumptions separately from user decisions, and put deferred ideas in their own list.
- Look up factual questions yourself. Ask the user about goals, preferences and tradeoffs, not repository facts you can inspect.

## Explore and Ask

- Begin with the outcome, audience, constraints and evidence of success; skip anything already established.
- Map independent areas such as website, product and marketing, including smaller plans within them, in the same master conversation.
- Preserve new ideas without silently expanding an already authorized build. Explore the current decision deeply enough to make the next plan reliable.
- Keep chat to five sentences or fewer; present long plans as Discord cards when supported.
- Ask one question at a time, with four or five meaningful choices and a recommended choice first. Explain its practical tradeoff in plain language; accept free-text answers.
- Ask only decisions whose prerequisites are settled. Use the grilling decision-tree approach without importing its multi-question rounds.
- Save each answer before asking the next question. Reopen it only when the user changes direction or new evidence makes the old decision inconsistent.
- Use research and prototypes when they resolve a specific unknown; do not launch paid agents or live model evaluations merely because this skill mentions them.

## Write Plans for Fresh Workers

- Use references/plan-contract.md for the master record and task details.
- Give each task one independently checkable result, normally 15-30 minutes of work. Include tests and wiring needed for that result; avoid separating trivial setup into its own worker.
- Record stable task IDs, prerequisite IDs, project, file ownership, inputs, output and check evidence. List the interfaces other tasks consume.
- Cover every agreed requirement with at least one task or an explicit deferred decision. Do not substitute a smaller feature silently.
- Parallel candidates require both completed prerequisites and non-conflicting ownership. Separate folders alone do not prove independence when data, ports or shared services overlap.
- The planner estimates useful parallelism; a runtime capacity controller decides how many eligible workers can run now across all builds. Do not hardcode ten or promise a safe maximum from one resource snapshot.
- Preserve allowance for chat, reviews and changes in computer load; do not treat hardware capacity as spending authorization or provider capacity.
- Treat readiness, execution completion and the user's final review as distinct states. Worker sessions end after their results are preserved, even while user review is pending.

## Check Readiness

- Compare tasks against the intended outcome and saved decisions, not only against a checklist of document names.
- Check for missing requirements, unresolved decisions that block a task, dependency cycles, missing inputs, conflicting ownership and checks that cannot run independently.
- Confirm the installed runner's supported format before producing executable plans. Follow the compatibility rules in references/plan-contract.md; authoring metadata is not automatically an execution feature.
- Describe the result as ready, ready with named unresolved items outside the starting tasks, or blocked by named decisions. Never invent answers to mark a plan ready.
- Keep the implementation method as an explicit user choice when needed; preserve any choice already made. A planning answer or model preference does not itself start a build.
- If the user explicitly requests a build, use the existing Go Work start instructions and agreed plan, without adding another approval ladder.

## Changes and Handoffs

- On a change, update the decision record, bump the plan version and identify tasks whose requirements changed.
- Preserve completed results; distinguish them from results that no longer satisfy the revised requirements.
- Hand off the goal, chosen decisions, task inputs, required outcome and verification evidence. Avoid forwarding whole transcripts by default.
- Surface the current planning question in the master thread; workers should report through saved results rather than reopen settled decisions with the user.

## Origin and Evaluation

- Adapted from inspected patterns in Superpowers (brainstorming and task-writing), GSD (decision traceability and parallel-plan fields), and BMad (readiness and requirement coverage).
- Exact source revisions and file paths are recorded in ../planner-audit.md. Upstream notices are retained in references/licenses/.
- evals/evals.json contains proposed behavior scenarios. No live model comparison has been run, and this draft makes no claim of tested triggering reliability.
