# Go Work: Plan Once, Build Many Things

**Latest direction:** improve the existing planning experience and its execution handoff using selective prompt-engineering inspiration from the three cloned projects. Drew requested a real-plan walkthrough and improvements on both sides; long card displays usually go unread. The wholesale replacement skill remains unaccepted and uninstalled; prompt-refinement.md is the current focused draft.

Check: uv run pytest tests/test_task_loop.py tests/test_loop_store.py tests/test_gowork_records.py tests/test_gowork_ending.py tests/test_work_copy.py -q
Try: uv run pytest tests/test_task_loop.py -q
Goal: One planning conversation produces several coordinated build plans, with independent work running together and finished workers closing automatically.
Done when: A local practice run proves dependencies, automatic worker sizing, recovery, combined checks, one repair attempt, message-linked blocker answers, and automatic completion with a short what-and-why recap and no looks-good wait.

## 1. What Is Settled

- **Draft for discussion, not a build launch.** Drew requested a research-backed plan followed by grilling.
- **Planner requirement:** existing brainstorming, question-answering and task-writing should understand execution requirements through shared instructions or a focused supporting skill; preserve useful existing behavior and reduce reading burden.
- Keep one master planning conversation, with separate small build tasks beneath it.
- A business can have website, product and marketing plans; each can have smaller plans of its own.
- Plan in detail before building; later small changes remain possible.
- **Planning interaction settled:** keep plans detailed internally, while asking short multiple-choice questions with useful distinct options until every required user decision is answered. Gather technical facts from the project instead of making Drew supply them; retain saved answers and show little at a time.
- **Name and direction:** keep Go Work as the name and evolve its existing loops.
- **Recommended execution shape:** one persistent coordinator tracks child plans and starts independent ready tasks within automatic capacity. Each task gets a fresh worker session; finished workers close while the coordinator retains results and advances the lanes. Existing Go Work already has parallel groups.
- Independent work may run together; work that needs another task's result must wait.
- **Capacity decision, updated September 20:** automatically use as many useful workers as the computer can support, including more than ten when resources allow; Drew should not choose a count for every launch.
- The planning session identifies independent work and reads available capacity; the running bot keeps checking actual machine load and controls worker starts across all builds.
- **Scheduling priority settled:** prefer ready tasks that unblock other work, with fair turns across builds so one large dependency chain cannot keep another build waiting indefinitely. Preserve waiting history across restart; exact scoring is an implementation detail, not another user decision.
- Use a protective ceiling when needed, based on measured conditions; ten is the current implementation limit, not the desired permanent maximum.
- A finished worker must stop consuming a session even if Drew has not tried the result yet.
- **Thread retention settled:** archive each finished worker thread immediately after its result is saved; retain chat history and work rather than deleting the thread. Do not wait for sibling workers or the full master plan. Keep the main planning thread and its actionable blocker messages available.
- Keep building the agreed work until it is finished or there is a real blocker.
- **Failure policy settled:** after the original failed attempt, allow one automatic repair attempt, while independent work continues. Dependent tasks wait for a checked result; the repair shares capacity and cannot reset its allowance by switching model, splitting into children or entering another automatic repair loop.
- **Completion settled:** successful automated checks and required reviews finish the build without requiring Drew to say looks good; end worker execution and post the result in the main planning thread. Do not keep a success waiter or send approval reminders.
- **Issue handling settled:** report problems and any successful workaround briefly in the planning thread; if one repair cannot resolve an issue, post an actionable blocker there. When no remaining task can proceed, provide an honest blocked summary of all unresolved issues rather than claiming completion.
- **Answer routing settled:** Drew uses Discord's Reply action on the specific blocker message in the planning thread. Route that referenced message to the correct build, task and blocker; an ordinary message in the same thread is not automatically a blocker answer.
- **Recap settled:** short bullets stating what was completed and why it mattered to the original request, plus concise check results and any workaround or remaining limitation. Distinguish completed, worked-around and blocked outcomes.
- **Communication requirement:** short output must still supply enough context to understand it independently: name the project/goal, what changed or is blocked, why it matters, and any decision needed. Build on One Question guidance; no routine full-plan card dumps and no unexplained technical labels. Detailed worker plans remain complete.
- **Clarification preference settled:** when an explanation is unclear, first use one concrete example from the project being discussed; label hypothetical examples and do not imply their work is already done.
- **Local integration settled:** automatically combine each finished build into its local project after required checks and reviews pass, while other builds continue. Serialize integrations into the same project, preserve existing local edits, and check the combined result before reporting success. Conflicts follow the one-repair/blocker policy rather than forcing an overwrite. Publishing remains governed by the separate existing rules.
- Drew can try the combined result after the work is finished; publishing is a separate step.
- **Already changed locally:** the parallel-step cap and default session capacity were raised from 3 to 10.
- **Observed during planning:** the live session API reports a capacity of 10; the shared execution helper uses a configured semaphore. Automatic resource-based sizing is proposed, not implemented.

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
- **Planner-specific follow-up:** Superpowers, Get Shit Done and BMad were cloned and inspected; planner-audit.md maps the reusable parts, and planner-skill-draft contains a concrete, uninstalled skill draft, output contract, example evaluations and retained licenses.
- Use a short global routing rule, one shared master-planning skill, small templates and saved project decisions; runtime code still owns dependency enforcement and adaptive capacity.

## 3. Proposed Everyday Flow

- **Plan:** the master thread records the goal, what counts as finished, separate plans, dependencies and unresolved decisions.
- **Prepare:** each task gets a stable identity, one outcome, its project, allowed files, required inputs, expected result and a check.
- **Start:** show independent work and the initial capacity estimate; retain existing AI and mode choices without asking Drew to pick a worker count.
- **Build:** gradually start ready tasks while machine headroom allows; record ownership before dispatch so restarts cannot silently duplicate work.
- **Adjust:** monitor all builds and other computer activity together, including workers' browsers, tests and child processes; leave room for the operating system, normal chat and reviews.
- **Back off:** stop admitting new workers when pressure rises, then grow gradually after recovery; use a cooldown to avoid repeatedly raising and lowering capacity.
- **Protect:** estimate worker peak memory and CPU needs, watch available memory, swap pressure and disk space, respect actual host/container limits, and use a conservative fallback if measurements are missing. Do not load-test the machine to failure.
- **Separate limits:** available hardware does not bypass provider rate limits, existing spending permissions, task dependencies or file ownership. More workers are useful only when they can make independent progress.
- **Emergency behavior:** prefer finishing running work while launches are paused; test a defined emergency stop/recovery path for critical pressure. Monitoring reduces overload risk but cannot guarantee that a computer never crashes.
- **Finish a worker:** save its result, checks and local commit; release its capacity and close its execution session.
- **Combine:** bring compatible changes into a local review copy, run combined checks and required reviews, then release dependent tasks.
- **Finish a build:** after required checks pass, end automatically and post a short what-and-why recap; no looks-good question. If work remains blocked, report the blockers and retain results without an idle worker session.
- **Answer a blocker:** use the replied-to Discord message ID to recover its saved blocker and attempt; a resolving answer makes only the affected work eligible again, with dependency and capacity checks still enforced.
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
- [ ] **T06a: Measure available capacity.** Provide a read-only machine-pressure snapshot and observed worker resource estimates, including subprocesses and missing-data fallback. Needs: T04. Proof: simulated memory, CPU, swap and disk pressure produce explainable readings; measurement does not start paid workers.
- [ ] **T06b: Share one capacity controller across builds.** Enforce admission atomically for builders and reviewers, account for chat, preserve coordination capacity, and prefer tasks that unblock work while giving every build fair turns. Needs: T05, T06a. Proof: simultaneous plans cannot claim the same headroom or deadlock waiting for workers; a steady stream of high-priority tasks cannot starve another ready build, including after restart.
- [ ] **T06c: Adjust worker count automatically.** Start conservatively, use observed resource peaks to grow, slow admissions under pressure, and recover with cooldowns and a protective ceiling; update both the fixed parallel-group cap and the underlying process gate. Needs: T06b. Proof: simulated healthy capacity permits more than ten workers, pressure reduces admissions, missing measurements trigger fallback, and provider limits remain respected.
- [ ] **T07: Give each worker a compact, saved assignment.** Reuse the current handoff contract, including task ID, attempt, inputs and expected result. Needs: T04. Proof: a duplicate delivery does not create another attempt, and unrelated conversation history is omitted.
- [ ] **T08: Finish workers individually.** Persist successful or failed outcomes and release execution capacity without waiting for sibling workers; preserve results before any thread cleanup. Needs: T06c, T07. Proof: a fast worker closes while a slow sibling continues, including exception and cancellation paths.
- [ ] **T09: Combine results in a controlled order.** Bring saved worker commits into the project's review copy; retain conflicting work for repair. Needs: T08. Proof: independent results combine and a conflict cannot release dependent tasks.
- [ ] **T10: Make completion depend on evidence.** Run combined checks and required reviews; a missing review stays visibly unfinished under modes that require it. Needs: T09. Proof: a failed check or unavailable required review never produces a completed result.

### Recovery and Changes

- [ ] **T11: Recover after interruption without duplicate work.** Reconcile saved attempts with actual worker and commit state at startup. Needs: T08, T09. Proof: interruptions before dispatch, after result save and after combination resume correctly.
- [ ] **T12: Limit repairs and isolate blockers.** Allow one repair after the original failed attempt, continue unaffected work, and keep dependent tasks blocked until a checked result is accepted. Needs: T10, T11. Proof: repair allowance survives restart and cannot reset through model escalation or task splitting; exhaustion records a blocker while independent tasks progress.
- [ ] **T13: Handle edits to plans already running.** Save plan versions, update unstarted work and detect results based on superseded requirements. Needs: T04, T05 and change-policy decision. Proof: an older result cannot silently satisfy a changed task.

### Drew's View and Proof

- [ ] **T14a: Persist actionable blocker messages.** Link planning-thread message ID to build, task, attempt, question and unresolved/resolved status. Needs: T04, T12. Proof: several builds can have separate blockers in one planning thread and mappings survive restart.
- [ ] **T14b: Route direct Discord replies to the right blocker.** Use message.reference.message_id plus channel and authorized-user checks; resolve answers against the saved task version and resume eligible work. Needs: T14a. Proof: normal conversation is not consumed, two blockers receive the right answers, duplicate replies cannot dispatch twice, and stale/resolved/unknown references get an explanation instead of altering another run.
- [ ] **T14c: Report progress and final outcomes briefly.** Name the relevant project/goal, result or blocker, and why it matters; announce workarounds without asking for approval; finish with short what-and-why bullets, checks and remaining issues. Needs: T04, T08, T10, T14a. Proof: a reader can understand representative messages without prior chat or files, each choice explains its consequence, claims come from saved results/goals, and an all-blocked run lists issues without claiming success.
- [ ] **T15: Finish automatically without looks-good approval.** Automatically integrate each checked build into its local project, serialize same-project integration and check the combined result; close workers, archive each finished worker thread after its result is saved, and remove success waiters/reminders. Needs: T14a-T14c. Proof: success needs no user response, history/results survive archiving, other workers can continue, existing edits survive integration, and failed integration or archive retries cannot lose results or duplicate the build.
- [ ] **T16: Report repeated workflow friction.** Extend existing run records with wait time, repair attempts, review outcomes and repeated owner questions. Needs: T04, T12. Proof: a saved sample produces reproducible counts; unavailable cost data stays unknown.
- [ ] **T17a: Refine existing planner prompts.** Preserve saved answers and ask short multiple-choice questions with useful options until required user decisions are resolved; retain detailed plans internally and add clear worker-task preparation using selected OSS patterns. Needs: existing examples and settled interaction preference. Proof: real-plan examples remain complete internally, readable in chat and free of repeated settled questions; focused draft exists in prompt-refinement.md.
- [ ] **T17b: Define compatible planning templates.** Capture the master record, decisions, ownership, dependencies and completion evidence; export only formats the installed runner actually supports. Needs: T17a. Proof: business examples have full requirement coverage and unsupported dependency behavior is clearly rejected or serialized through a verified supported path.
- [ ] **T17c: Connect planner instructions and execution context.** Put the agreed execution guidance in the existing shared instructions or a small supporting skill; pass complete task inputs/outputs/ownership to the grouping helper, not just titles. Needs: T17a, T17b. Proof: all three harnesses use the same guidance and grouping receives relevant task details; runtime dependency enforcement remains code-backed.
- [ ] **T17d: Evaluate planner behavior.** Check the proposed scenarios for new plans, resumed answers, ownership conflicts and tiny changes; compare live behavior only within separately authorized evaluation scope. Needs: T17b, T17c and T03 for executable dependency checks. Proof: saved evidence distinguishes static validation from actual model behavior and records any failures.
- [ ] **T18: Provide a local practice run.** Use simulated workers and machine readings to demonstrate adaptive capacity, dependencies, two plans, one repair, message-linked blocker replies, restart, plan edits and automatic completion. Needs: all preceding implementation tasks, including T14a-T14c. Proof: no paid calls or real stress test, no looks-good wait, and saved results and accurate short recaps remain available.

**Parallel build opportunities:** T17a-T17c can progress before scheduler implementation; T03 and T04 can proceed after T02; T06a and T07 can proceed after T04 while T05 is built; T13 and T16 can proceed once their own prerequisites exist.
Tasks changing the same existing loop or Discord files must be sequenced or given distinct ownership even when their conceptual dependencies allow parallel work.
Cross-project builds preserve separate local review copies and an explicit final cross-project check; they do not pretend separate repositories have one atomic merge.

## 5. Decisions for the Grilling

Settled answers are listed in section 1; the following are proposals or open choices, not assumed approval.
Ask one question at a time and record the answer before moving to dependent questions.

- **Q1 settled:** automatically choose the useful worker count from independent work and available machine resources, with a protective maximum if needed; do not enforce ten forever or ask for a count each launch.
- **Planning interaction settled:** detailed plans built through short multiple-choice questions until required user decisions are filled in; retain the Go Work name and existing loop concept.
- **Scheduling priority settled:** unblock other tasks first while sharing fairly across active builds; no build is left waiting indefinitely.
- **Engineering follow-through:** determine headroom thresholds, sampling, fallback and ceiling from read-only measurements and simulated tests during implementation; do not ask Drew to guess technical numbers or promise a safe count from one idle snapshot.
- **Failure policy settled:** one repair attempt, independent work continues, and unresolved blockers return to the planning thread for a direct reply to the blocker message.
- **Completion and integration settled:** no looks-good gate; automatically integrate each finished build into its local project after checks, without waiting for other builds in the master plan.
- **Retention settled:** archive each finished worker thread immediately after saving results; preserve history, and keep blocker messages available in the main planning thread.
- **Review strength:** retain cheap/balanced/careful behavior, or change which tasks need a separate reviewer and what happens when one is unavailable?
- **Changes during a build:** apply new directions only to unstarted tasks, stop affected workers, or finish their current attempts and then replace stale work?
- **First release boundary:** include multi-project business plans immediately, or first prove multiple lanes within one project while preserving the same data format?
- **Name and completion reporting settled:** keep Go Work, report issues/workarounds in the planning thread, and finish with short bullets explaining what changed and why; no success approval question.
- **Implementation method, after the design is settled:** choose fresh-session Go Work or small normal-session increments; this planning request does not launch either.

## 6. How to Try It

- **Available now:** the Try command runs existing local loop tests; it does not demonstrate the proposed master-plan behavior yet.
- **Baseline checked:** the full Check command passed 168 tests in 7.76 seconds on this worktree; this verifies existing behavior, not the unbuilt proposals.
- **Once T18 exists:** one local practice command will run simulated builders and show a compact result, without starting the live bot or paid agents.
- **30-second check 1:** independent website and marketing tasks run together; a dependent product task visibly waits.
- **Automatic capacity check:** the practice run shows worker count growing when simulated resources allow and new launches pausing when the computer becomes busy; the displayed reason explains each change.
- **30-second check 2:** a finished worker closes while another is still running, and its result remains accessible.
- **30-second check 3:** after one failed repair, a direct Discord reply resolves only its linked blocker; ordinary conversation is left alone, restart does not duplicate completed work, and success posts its recap without waiting for looks good.
- Before implementation finishes, replace Try with the proven practice command and record its actual output and runtime.
- No web page is part of this draft, so there is no local web address to open yet.
