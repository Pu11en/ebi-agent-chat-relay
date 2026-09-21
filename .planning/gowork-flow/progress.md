# Go Work Planning Progress

## 2026-09-20

- Read planning-with-files and grilling skills; adapted grilling to Drew's one-question-at-a-time preference.
- Checked local worktree, active sessions and claim ownership before writing.
- Read saved OSS research and checked current group execution, review behavior and capacity.
- Created a draft with 18 small implementation tasks, explicit dependencies, checks, research mapping and open decisions.
- Current phase: design interview in progress; automatic resource-aware sizing and unblock-first scheduling with fair turns are settled. Existing planning flow and selective OSS prompt improvements remain the scope.
- No new implementation, paid worker launches, runtime restart or publication performed.
- Planning files are pinned to .planning/gowork-flow; previous named plans remain untouched.
- Validation: the plan's Check command passed 168 tests in 7.76 seconds; plan-directory resolution returned the selected gowork-flow directory.
- Pre-commit scope review: Markdown only, with no changed subprocess paths, credentials, dependencies or runtime configuration.
- Updated capacity design from Drew's answer; split T06 into three small tasks (measurement, shared admission, automatic adjustment), bringing the draft to 20 tasks.
- Capacity refinement verified against _run_helper.py's existing fixed semaphore; no resource thresholds invented and no machine stress test performed.
- Documentation-only follow-up: no test rerun needed; the earlier 168-test baseline remains the last runtime validation.
- Audited existing planning configuration and cloned three OSS references; wrote planner-audit.md and an uninstalled master-planning skill draft with templates and evaluation prompts.
- Split T17 into four focused planner tasks, bringing the proposed build to 23 tasks. Planner skill and routing can proceed before scheduler implementation.
- No new global instructions, shared skill installation, live model evaluation or bot restart performed.
- Draft validation: skill-creator quick_validate passed, evaluation JSON parsed successfully, and git diff --check was clean. These are structural checks, not evidence of model behavior.

## Inspection Notes

- A broad filename search returned unrelated projects; subsequent reads were restricted to this worktree.
- Optional parent AGENTS.md and session-specific worktree were absent; project AGENTS.md is a symlink to CLAUDE.md and was read.
- A handoff filename probe found no match in this branch; reuse is an implementation prerequisite, not a claim that the contract was inspected.
- git check-ignore returned 1 because the planned files are not ignored; this is expected.
- A guessed session_capacity.py path did not exist; located the actual capacity implementation in cogs/_run_helper.py.
- A guessed unsuffixed skill-creator path was absent; read the catalog's synced skill-creator location instead. One-question's symlink target was read successfully.
- Initial quick_validate lacked PyYAML in the project environment; reran with uv run --with pyyaml in an isolated tool environment and received "Skill is valid!" without changing project dependencies.

## Next Turn

- Drew now wants improvements to the existing planning experience, selective prompt inspiration from all three clones, a real-plan walkthrough, and improvements to both planner and executor. Do not treat that as acceptance of the wholesale replacement skill.
- Long card displays usually go unread. Keep detailed artifacts internal by default in this discussion and show only the current short example or decision.
- Created prompt-refinement.md with planner/executor instruction drafts and a walkthrough of the existing six-step report plan; no live prompt installation, worker launch or model evaluation.
- Interaction answered: detailed plans, short multiple-choice questions with good options until required answers are complete. Keep Go Work name and evolve existing loops.
- Explained recommended lifecycle: coordinator continues, independent lanes advance through tasks, and each task worker finishes and closes. Existing Go Work already has parallel groups.
- Drew chose A for stuck tasks: independent work continues while Go Work attempts bounded repairs; dependent tasks wait. Saved this policy in the plan and prompt draft; runtime unchanged.
- Drew chose B: one repair after an original failed attempt; independent tasks continue. No hidden extra allowance from escalation, splitting or restarts.
- Drew removed the looks-good completion gate: automatically finish with short what-and-why bullets, report workarounds, and surface unresolved blockers in the planning thread.
- Drew requires actual Discord Reply on the specific blocker message to answer it; normal thread conversation must not be captured as an answer. Added separate persistence, routing and concise reporting tasks.
- Code inspection: current waiters are keyed by channel ID; _wrap_up waits for a verdict and looks good also invokes keep_work to integrate locally. Therefore the next unresolved decision is local integration timing, not whether to require completion approval.
- No runtime changes, paid calls or test rerun; this is a documentation-only update.
- Drew clarified that minimal words still need sufficient standalone context; raw brevity and long cards both miss the need. Added a communication check built on One Question for goal, impact, evidence and next decision, preserving complete internal plans.
- The latest message's "bc" means because in context, not a selection of local-integration options B and C; local integration remains unanswered.
- Drew chose A for clarification style: explain unclear points with a concrete example from his project. Recorded in planner communication guidance; global/runtime instructions remain unchanged.
- Drew chose A for local integration: automatically bring each checked build into the local project while other builds continue; no looks-good gate. Saved in the plan and prompt draft.
- Integration must preserve existing edits, serialize same-project merges and verify combined results; conflicts use the existing one-repair/blocker rule. Runtime remains unchanged during planning.
- Drew chose A for thread retention: immediately archive finished worker threads after saving results, preserving history. Main planning thread and blocker messages stay available; runtime unchanged during planning.
- Drew chose A after clarification: prefer tasks that unblock other work, while sharing fairly across builds so none waits indefinitely. Saved in the plan and prompt draft; runtime unchanged.
- Next open question: how a new direction in the planning thread should affect a task already running; preserve all settled decisions and use a concrete example.
- One documentation patch used out-of-order contexts and did not apply; reapplied the same changes in file order successfully.
- Existing Go Work reads task checkbox labels for grouping (open_tasks -> _groups_for -> group_prompt); detailed task bodies are not included in that grouping prompt. Workers separately read the full plan.
- Rechecked native Goal/Done when/Check parsing and the existing transcript-feedback plan; no product code or runtime changes made.
- Keep alternatives open until answered; do not launch the draft as a build plan.
