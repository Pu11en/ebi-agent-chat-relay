# Go Work: Plan Once, Build Many Things

Check: uv run pytest tests/test_task_loop.py tests/test_loop_store.py tests/test_gowork_records.py tests/test_gowork_ending.py tests/test_work_copy.py -q
Try: uv run pytest tests/test_task_loop.py -q
Goal: One planning conversation produces several coordinated build plans, with independent work running together and finished workers closing automatically.
Done when: A local practice run proves dependencies, the chosen worker limit, recovery after interruption, combined checks, and worker closure while Drew's final review remains pending.

## 1. What Is Settled

- **Draft for discussion, not a build launch.** Drew requested a research-backed plan followed by grilling.
- Keep one master planning conversation, with separate small build tasks beneath it.
- A business can have website, product and marketing plans; each can have smaller plans of its own.
- Plan in detail before building; later small changes remain possible.
- Independent work may run together; work that needs another task's result must wait.
- Drew requested **up to 10 parallel workers**; whether this is shared across builds is still open.
- A finished worker must stop consuming a session even if Drew has not tried the result yet.
- Keep building the agreed work until it is finished or there is a real blocker.
- Drew can try the combined result after the work is finished; publishing is a separate step.
- **Already changed locally:** the parallel-step cap and default session capacity were raised from 3 to 10.
- **Observed now:** the live session API reports a capacity of 10; this includes ordinary conversations and does not prove 10 build workers can run alongside them.

## 2. What We Reuse and Improve

- Go Work already has separate build copies, parallel groups, restart recovery, model selection, reviews, retries, goal checks and a queue.
- The code already checks the combined work and can review parallel steps; an older planning document incorrectly says parallel steps are not reviewed.
- Current parallel workers finish as a batch: a quick worker waits for the slowest before its result is processed, and its thread is then deleted.
- Proposed change: preserve each result and finish its worker as soon as possible; combine results and run the required checks before releasing dependent tasks.
- Current grouping asks an AI which steps belong together; proposed change: saved dependencies and file ownership determine what is eligible to run.
- Earlier audit evidence showed review catches, model-choice mistakes, repeated status retries and pauses around owner actions; increasing capacity alone will not address these.
- Existing review errors can count as no objection; required reviews need an explicit unfinished or unavailable state.
- Reuse the bot's existing handoff work after checking its current contract; do not create a competing message format.
- **Research patterns:** borrow clear task identity and result messages from [A2A](https://github.com/a2aproject/A2A), delegation followed by combined results from [LangGraph](https://docs.langchain.com/oss/python/langgraph/workflows-agents), dependency validation from [AutoGen GraphFlow](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html), and visible task states from [Prefect](https://docs.prefect.io/v3/concepts/tasks).
- The saved research also covers filtered handoffs and trace history from the OpenAI Agents SDK, persisted responsibility from LangGraph Swarm, reusable role views from AutoGen Studio, and saved flow state from CrewAI.
- **Recommendation:** adapt these patterns inside the existing bot; do not choose a replacement framework without evidence that it removes more work than it adds.
- Copying actual source later requires checking that source's license and preserving required notices; this draft proposes patterns, not vendored code.

## 3. Proposed Everyday Flow

- **Plan:** the master thread records the goal, what counts as finished, separate plans, dependencies and unresolved decisions.
- **Prepare:** each task gets a stable identity, one outcome, its project, allowed files, required inputs, expected result and a check.
- **Start:** show what can run together, what must wait and what needs Drew; retain existing AI and mode choices.
- **Build:** start ready tasks within the chosen shared capacity; record ownership before dispatch so restarts cannot silently duplicate work.
- **Finish a worker:** save its result, checks and local commit; release its capacity and close its execution session.
- **Combine:** bring compatible changes into a local review copy, run combined checks and required reviews, then release dependent tasks.
- **Finish a build:** report ready for Drew, blocked or failed; never call unfinished work complete just because no workers are running.
- **Try it:** keep one combined result per project available; a business spanning several projects gets a short checklist covering them together.
- **Resume:** use saved results and states; completed tasks do not rerun just because a thread or bot restarted.
- **Improve:** summarize repeated stalls, avoidable questions, retries and review failures from run records; suggest changes instead of silently rewriting the workflow.

## 4. Small Build Tasks

Each checkbox targets one outcome and roughly 15-30 minutes in a fresh session; split any task further if implementation evidence makes it larger.
Task dependencies below are a proposed design, not syntax the current runner already understands.
The Check command above is the existing baseline; every implementation task also adds focused tests, and the final practice runner becomes the future Try command.

### Foundation

- [ ] **T01: Describe one master plan and its child plans.** Record stable IDs, parent links, project location and plan version; existing single-plan files still work. Needs: none. Proof: old and new plan fixtures load correctly.
- [ ] **T02: Describe one worker task completely.** Add dependencies, file ownership, expected inputs, output and completion check. Needs: T01. Proof: duplicate task IDs and incomplete task definitions are rejected.
- [ ] **T03: Reject impossible schedules before starting.** Detect missing dependencies, dependency loops and conflicting file ownership among proposed simultaneous tasks. Needs: T02. Proof: each invalid fixture gives a useful explanation.
- [ ] **T04: Save task state and results.** Persist attempts, ownership, plan version, commits and check evidence using existing storage patterns. Needs: T02. Proof: saving and reopening preserves every relevant state without changing old runs.

### Scheduling and Worker Life

- [ ] **T05: Select ready tasks from dependencies.** Only tasks with accepted prerequisite results can start; incomplete or failed prerequisites keep children waiting. Needs: T03, T04. Proof: website work waits for the required product result while independent marketing continues.
- [ ] **T06: Enforce the chosen capacity policy.** Account for builders, reviewers and ordinary chat, and share capacity fairly across active plans. Needs: T05 and decision Q1. Proof: two plans cannot exceed their chosen limit or deadlock by holding all slots while waiting for workers.
- [ ] **T07: Give each worker a compact, saved assignment.** Reuse the current handoff contract, including task ID, attempt, inputs and expected result. Needs: T04. Proof: a duplicate delivery does not create another attempt, and unrelated conversation history is omitted.
- [ ] **T08: Finish workers individually.** Persist successful or failed outcomes and release execution capacity without waiting for sibling workers; preserve results before any thread cleanup. Needs: T06, T07. Proof: a fast worker closes while a slow sibling continues, including exception and cancellation paths.
- [ ] **T09: Combine results in a controlled order.** Bring saved worker commits into the project's review copy; retain conflicting work for repair. Needs: T08. Proof: independent results combine and a conflict cannot release dependent tasks.
- [ ] **T10: Make completion depend on evidence.** Run combined checks and required reviews; a missing review stays visibly unfinished under modes that require it. Needs: T09. Proof: a failed check or unavailable required review never produces a completed result.

### Recovery and Changes

- [ ] **T11: Recover after interruption without duplicate work.** Reconcile saved attempts with actual worker and commit state at startup. Needs: T08, T09. Proof: interruptions before dispatch, after result save and after combination resume correctly.
- [ ] **T12: Limit repairs and isolate blockers.** Apply the chosen retry and failure policy, while keeping unaffected work eligible where allowed. Needs: T10, T11 and failure-policy decision. Proof: repeated failure reaches a clear bounded state instead of running forever.
- [ ] **T13: Handle edits to plans already running.** Save plan versions, update unstarted work and detect results based on superseded requirements. Needs: T04, T05 and change-policy decision. Proof: an older result cannot silently satisfy a changed task.

### Drew's View and Proof

- [ ] **T14: Show one master status in Discord.** Summarize running, waiting, blocked and ready-for-Drew tasks with the next relevant action. Needs: T04, T08, T10. Proof: mixed task states display accurately without requiring worker-thread reading.
- [ ] **T15: Separate finished workers from Drew's review.** Keep results available after execution ends, using the chosen archive/retention policy and a local test handoff. Needs: T14 and retention decision. Proof: all workers are closed while the parent still says ready for Drew.
- [ ] **T16: Report repeated workflow friction.** Extend existing run records with wait time, repair attempts, review outcomes and repeated owner questions. Needs: T04, T12. Proof: a saved sample produces reproducible counts; unavailable cost data stays unknown.
- [ ] **T17: Refresh the planning instructions.** Teach the master thread to create small tasks, dependencies, checks and clear stop conditions; document any agreed plain-English launch names. Needs: T03 and workflow-naming decision. Proof: website/product/marketing examples produce valid plans without duplicate questions already answered.
- [ ] **T18: Provide a local practice run.** Use simulated workers to demonstrate 10 ready tasks, dependent tasks, two competing plans, a failure, a restart and a plan edit. Needs: T06-T17. Proof: no paid model calls, all assertions pass, and final results remain available for review.

**Parallel build opportunities:** T03 and T04 can proceed after T02; T07 can run while T05/T06 are built; T13, T16 and T17 can proceed once their own prerequisites exist.
Tasks changing the same existing loop or Discord files must be sequenced or given distinct ownership even when their conceptual dependencies allow parallel work.
Cross-project builds preserve separate local review copies and an explicit final cross-project check; they do not pretend separate repositories have one atomic merge.

## 5. Decisions for the Grilling

Settled answers are listed in section 1; the following are proposals or open choices, not assumed approval.
Ask one question at a time and record the answer before moving to dependent questions.

- **Q1, first: What does the 10-worker limit cover?** Recommended: one shared pool across builds, with room for normal conversations; alternatives are per master plan, per project or a count chosen at each launch.
- **After Q1: How is capacity shared?** Decide fairness between builds and how ordinary chat and reviews fit within actual process capacity.
- **Failure policy:** when a product task is stuck, should independent website or marketing work continue, and how much automatic repair is allowed?
- **Completion and retention:** execution must end without waiting for Drew; decide whether finished threads are archived, deleted after results are saved, or kept as inactive history.
- **Review strength:** retain cheap/balanced/careful behavior, or change which tasks need a separate reviewer and what happens when one is unavailable?
- **Changes during a build:** apply new directions only to unstarted tasks, stop affected workers, or finish their current attempts and then replace stale work?
- **First release boundary:** include multi-project business plans immediately, or first prove multiple lanes within one project while preserving the same data format?
- **Names and reporting:** one improved Go Work command or distinct named workflows; milestone updates or mainly a final report?
- **Implementation method, after the design is settled:** choose fresh-session Go Work or small normal-session increments; this planning request does not launch either.

## 6. How to Try It

- **Available now:** the Try command runs existing local loop tests; it does not demonstrate the proposed master-plan behavior yet.
- **Baseline checked:** the full Check command passed 168 tests in 7.76 seconds on this worktree; this verifies existing behavior, not the unbuilt proposals.
- **Once T18 exists:** one local practice command will run simulated builders and show a compact result, without starting the live bot or paid agents.
- **30-second check 1:** independent website and marketing tasks run together; a dependent product task visibly waits.
- **30-second check 2:** a finished worker closes while another is still running, and its result remains accessible.
- **30-second check 3:** one failed task is shown honestly, a restart does not duplicate completed work, and the final result waits for Drew's review.
- Before implementation finishes, replace Try with the proven practice command and record its actual output and runtime.
- No web page is part of this draft, so there is no local web address to open yet.
