# Progress

## 2026-09-10 (planning session, Claude Code in the terminal)
- Stopped all three running Discord sessions (SIGINT): realpage QA, audio-content, DSH ebi-agent-chat-relay.
- Disarmed the forced bot restart the DSH agent had armed.
- Four read-only search agents mapped what each session left behind → findings.md.
- Saved the start-fresh nudge on `feat/task-loop` as 2f818dc. Full suite: 3,090 passed, pyright clean.
- Wrote task_plan.md: 10 tasks, to be run with `/gowork` after Drew approves and restarts the bot.
- Drew: tasks must be small, and big work must never run as one session. Added a "Big tasks get chunked" rule to /home/drewp/AGENTS.md (backup: /home/drewp/.retired-harness/AGENTS.md.before-big-tasks-20260910). Split this plan from 10 tasks into 17.
- Fixed /gowork's check to ignore untracked files (9822974 on feat/task-loop). Merged plan tasks 1+2 so the first round leaves no modified tracked files; the plan now has 16 tasks.
- Restarted the bot onto wt-task-loop (merged main + origin/main; 3,126 tests pass) at 20:50.
- Drew asked to plan /gowork fully before building more. Decisions are recorded in findings.md. Q1: the harness and model are chosen with buttons at start.
- Q6 answer: Drew rejected a final 'merge?' question ('not effective'). Drew wants the full flow shown and an ending that is easy to deal with on return. Wrote docs/plans/gowork-flow.md (flowchart plus the Welcome back card idea). The ending is still open.
- Drew reframed the ending as his journey: see it and test it with little reading, on a localhost preview before going live. Proposed a Try-it card (local link, 3 checks, looks-good / something's-off buttons). The plan gets a 'How to try it' section.
- Drew had been answering as if this were another session. Re-oriented him, then got fresh answers: one worker thread with a fresh session per task, the bot runs tests, and the worker thread is deleted at the end with the output posted to the planner thread.
- Added a 'GitHub is always last' rule to /home/drewp/AGENTS.md. Inserted a Drew-tries-it-locally task before the push in this plan (17 tasks now).
- Finished the /gowork design questions (Q1–5 re-confirmed, holes 1–6 answered). Next: turn them into a small-task build plan.
- Building /gowork v2 in /home/drewp/main-projects/wt-task-loop. Step 1 (quiet worker threads; ping only for questions, stops and the end) is e1042b9. Step 2 (the bot runs the plan's Check: line) is 47f7cc1. Added the Check: line to the plan-writing rule in /home/drewp/AGENTS.md.
- Steps 3 (own copy, 7da4589), 4 (harness and model buttons, b267c1e) and 5 (plan picker, 04049c1) are done. 3,145 tests pass. Made a practice project at /home/drewp/main-projects/gowork-practice (a 2-task PLAN.md with a Check line) for the demo.
- The practice run found that typed answers were ignored (the loop waited for a button). Drew: no buttons ever. Rewrote /gowork so every question takes a typed reply and mid-task typing becomes a note (commit on feat/task-loop). 3,157 tests pass.
- Auto-resume (a641511). After the restart the practice build resumed in its own thread and finished all 6 tasks (calculator tests pass), and the bot forgot the finished build. /gowork works end to end.

## 2026-09-12
- /gowork: plan list by letter after a cleared session; free text releases the picker to chat; start + finished cards (embeds); the bot runs the plan's check + "How to try it" checks itself (no localhost); a stuck/paused/stopped build waits (keep going / skip / throw it away); Check-line note parsing fix. 3,243 tests pass. Bot restarted 03:17 on wt-task-loop.
- Global AGENTS.md: planning asks "gowork or normal session?" every time; plan Check lines must be self-contained and ~2 min.
- realpage: added tooling/qa/check-local.sh + quick-check.py; PLAN-v5 Check line uses it (commit 5c40321).
- 04:30 Fixes committed on feat/task-loop, NOT live yet (Drew will say when to restart): lettered AI list (0db2e1a), start-by-words + ping fallback (5ffc9f0), other-session notice labelled not-yours (c734901, live), auto plan switch + stopped builds only take real answers (aab21ac). Running builds: realpage PLAN-v6, drew's eval PLAN.md. Leftover copy to clean later: ~/.local/state/ccdb/gowork/realpage-plan-v5-20260912-032454 (all its work is in realpage main).
- Open ideas not built: label gowork lounge notes; stop other sessions queueing messages into worker threads.
- 04:40 Review (not yet fixed): (1) _close_for_switch hangs 45 min when the old build is mid-ASK (ask() waits on worker thread) or in final review (_wrap_up loop) — neither watches wake/auto_finish; then start_loop refuses. (2) after that timeout the old waiter is orphaned by the new picker's waiter on the same channel. (3) worker thread deleted → build crashes (NotFound) instead of ending cleanly; copy left behind. (4) worker rounds end with "[ede_diagnostic] stop_reason=tool_use" / "user doesn't want to proceed" → counted as stuck; cause unknown. (5) heavy Discord 429s from message edits in thread 1548215983670304800. (6) sqlite "database is locked" once.
- 04:55 Fixed review items 1-3 (3cf23f2, and stop-button fix). #3 cause: Stop button / /stop interrupted one step; loop retried it. 3,266 tests pass. Still waiting on Drew's restart.
- 04:59 Restarted (Drew's OK). All fixes live. Both builds (realpage PLAN-v6, drew's eval PLAN.md) resumed.
- 05:15 Slowdowns: 429s mostly 09-10 (old); today 62 PATCH in one busy thread (discord.py retries itself, left as is). DB lock (once ever) fixed: 30s busy timeout in session_repo (committed, not live). 3,267 tests.
