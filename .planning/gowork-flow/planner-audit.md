# How Every Planning Session Should Know the Workflow

## 1. What You Already Have

- **Shared instructions:** Claude, Codex and DeepSeek point to the same global rules, so small task sizing, local checks, Go Work launch instructions and your short-question preference already have a common home.
- **Planning With Files:** saves the plan, discoveries and progress so a session can continue later.
- **Grilling:** explores unresolved decisions; its default asks a whole round of questions, which needs adapting to your one-question preference.
- **One Question:** provides your short, lettered question format and describes a separate optional question-verification tool; that paid tool was not run here.
- **To Spec and To Tickets:** turn conversation into specifications and dependent tasks, but currently describe tracker publishing and manual invocation, not automatic Go Work preparation.
- **Wayfinder:** maps large unresolved decisions, with tracker and handoff conventions that are heavier than this everyday planning flow.
- **Go Work itself:** executes plans, groups workers, preserves work and checks results; it is runtime code, not just a skill.
- **Missing connection:** no single inspected skill consistently connects brainstorming, saved answers, complete requirements, small build lanes and a format Go Work can enforce.
- Two existing instructions also conflict in style: global chunk planning mentions yes/no questions, while your current communication preference specifies four or five choices; the draft follows the latter.

## 2. Recommended Setup

- **A short shared rule:** when a session is brainstorming or planning a project, load the master-planning skill; do not require you to remember a command.
- **One master-planning skill:** guide the conversation from ideas to decisions to detailed plans, remembering answers and asking one useful question at a time.
- **Small reference templates:** specify what belongs in the master plan and each worker task, loaded when needed rather than pasted into every conversation.
- **Saved project state:** keep goals, decisions, tasks and results with the project, with separate planning records for unrelated efforts.
- **Code-backed enforcement:** the bot validates dependencies, controls available worker capacity and records completion; a skill can describe these rules but cannot guarantee they are obeyed.
- **Shared installation:** when activated, place the skill once under the existing shared skills directory; add only the short routing rule to global instructions and check discovery in each harness.
- Proposed routing rule: "For project brainstorming, requirements discussion or creating a build plan, read the master-planning skill and resume the project's saved planning state; skip this workflow for an already scoped small fix, factual question or execution of an existing plan."
- **Current status:** a concrete skill draft and templates exist for review; global routing and installed skills have not been changed.

## 3. What We Cloned and Can Reuse

All three repositories were cloned locally for inspection; no installers, hooks or upstream agent workflows were run.

- **Superpowers:** use its connection between understanding an idea and producing small plans, including clear inputs and outputs for workers; omit its repeated stage approvals and mandatory execution-skill routing. [Inspected brainstorming](https://github.com/obra/superpowers/blob/5bf4e78011075bcfc0dc295f0724994cd123ee71/skills/brainstorming/SKILL.md), [inspected planning](https://github.com/obra/superpowers/blob/5bf4e78011075bcfc0dc295f0724994cd123ee71/skills/writing-plans/SKILL.md).
- **Get Shit Done:** use its saved decisions, requirement coverage, dependency fields, file-ownership declarations and conflict checks; adapt its grouped execution to our dependency-driven runner and retain the bot's existing storage. [Inspected planner](https://github.com/gsd-build/get-shit-done/blob/bdcaab2c752d9a33a1a1ca9acf3a3c81fb991815/agents/gsd-planner.md), [inspected execution checks](https://github.com/gsd-build/get-shit-done/blob/bdcaab2c752d9a33a1a1ca9acf3a3c81fb991815/get-shit-done/workflows/execute-phase.md).
- **BMad:** use its check that recorded requirements actually turn into buildable tasks and its small, stage-specific instruction files; omit the many roles, repeated continue menus and installer-specific configuration. [Inspected readiness check](https://github.com/bmad-code-org/BMAD-METHOD/blob/f033e70a2c0a3751aaab17dfdd29839ac621f541/skills/bmad-sprint-planning/references/readiness-gate.md).
- The earlier A2A, LangGraph, AutoGen, CrewAI and Prefect research remains useful for execution, handoffs and recovery; these three new sources are more directly about how the planning conversation works.
- Each inspected repository includes an MIT license; source revisions and notices are preserved with this work. The draft rewrites selected ideas into your workflow rather than importing all three instruction sets.
- No measured coverage percentage or claim that the upstream systems solve adaptive machine capacity is justified by this inspection.

## 4. What the Draft Now Does

- Recognizes whether you are exploring ideas, answering questions, writing tasks or reviewing readiness, then continues from that stage.
- Preserves your settled answers, including automatic worker sizing beyond ten when resources allow.
- Keeps website, product, marketing and their smaller plans connected inside one master planning conversation.
- Requires each task to state its goal, dependencies, owned files, inputs, result and proof it worked.
- Checks that agreed requirements have tasks and that a builder will not need to invent an unanswered decision.
- Distinguishes a finished worker from your final review, so its session can close while results stay available.
- Detects that today's runner cannot enforce all proposed metadata yet; it must not pretend that writing dependency fields implements a scheduler.
- Includes four evaluation scenarios: a new business, continuing from an existing answer, conflicting/dependent tasks, and a tiny fix that should skip heavy planning.
- The draft has not undergone live model testing or been activated across your sessions; this turn changes planning artifacts only.

## 5. Implementation Order and Evidence

- First refine the master skill, its templates and the transition from brainstorming to task-writing.
- Then wire the shared routing rule and check discovery in Claude, Codex and DeepSeek; keep the existing Go Work format working.
- Add requirement and dependency validation before claiming that plans are ready for automatic parallel execution.
- Connect the scheduler and capacity controller from the existing build plan, then demonstrate them with simulated workers.
- Evaluate actual session behavior separately from static file checks; a valid SKILL.md alone does not prove reliable activation or good questions.
- Local reference root: `/home/drewp/.local/share/ccdb/research/gowork-planner-1551416259974008862`.
- Superpowers revision: `5bf4e78011075bcfc0dc295f0724994cd123ee71`; GSD: `bdcaab2c752d9a33a1a1ca9acf3a3c81fb991815`; BMad: `f033e70a2c0a3751aaab17dfdd29839ac621f541`.
- Local wiring inspected: `~/AGENTS.md`, harness instruction links, the shared skill directory, planning/grilling/question/spec/ticket skills, and Go Work's plan and worker paths.
