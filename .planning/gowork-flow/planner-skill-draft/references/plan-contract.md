# Planner Output Contract

This is an authoring contract for the draft skill. It is not a claim that today's Go Work reads every field below.

## Master Record

- Effort ID, name, plan version and current planning stage.
- Goal, intended audience, explicit scope and observable completion evidence.
- Settled decisions with stable IDs, unanswered questions with their prerequisites, assumptions and deferred ideas.
- Child plans with project locations and the requirements each owns.
- Relationships between child plans, shared interfaces and the cross-project completion check.
- Execution method if already chosen; otherwise leave it visibly unresolved.
- Capability snapshot identifying what the installed runner can actually consume.

## Task Record

- Stable ID and one outcome a fresh worker can implement and check.
- Parent plan, project, requirement IDs and applicable decision IDs.
- Prerequisite task IDs and exact required inputs.
- Files or resources owned, plus expected interfaces and outputs.
- Concrete verification command and expected observable result.
- Required review policy, owner-only decisions and any external prerequisites.
- Result record: attempt ID, plan version, local commit or artifact, check outcome and remaining blocker.

## Current Go Work Compatibility

Keep the native executable plan readable:

```markdown
# Build Name

Goal: A concrete outcome
Done when: Observable completion evidence
Check: A real standalone command verified in this repository
Try: A real command for trying the result locally
Open: A local URL only when applicable

## Tasks

- [ ] One independently checkable outcome
  Inputs: Required context and earlier results
  Files: Exact ownership
  Result: What must exist when finished
  Verify: Concrete check and expected result

## How to try it

- One short observable check
- A second short observable check
- A third short observable check
```

The example above is a template, not a runnable plan. Replace every example value before export; omit Open when there is no local web interface.

- Use one top-level checkbox per worker task. Do not make each test command or acceptance bullet an extra worker checkbox.
- Do not use shell chaining in Check unless the actual runner explicitly supports it; use an existing standalone checker where appropriate.
- Validate the plan's check within about two minutes without calling paid models or relying on a prestarted server.
- Keep dependency-rich master records separate from executable checklists until the runtime supports them.
- Unsupported dependency semantics must never silently become parallel execution: report the capability gap and use a supported, verified sequential path when available, otherwise leave the plan unlaunched.
- Capacity remains a runtime concern; an authoring field cannot remove the current hardcoded cap or fixed process semaphore.

## Example Checks for the Planner

- A product interface needed by a website task creates a dependency; unrelated marketing research can remain independent.
- Two otherwise independent plans editing the same shared configuration file require sequencing or changed ownership.
- A completed worker with saved output closes its execution session; the parent can remain ready for the user's review.
