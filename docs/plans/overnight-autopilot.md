# Overnight autopilot: your transcripts become work while you sleep

Status: idea, not built. Extends [task-loop.md](task-loop.md). The task loop is
the engine; this document covers the part that decides what the engine works on.

## The flow

1. **Idle trigger.** You haven't sent a message in any channel for 3 hours
   (adjustable). The bot's scheduler checks this every 30 s, and nothing runs
   while you're active.
2. **Harvest.** It reads every transcript that hasn't been used yet, from
   Claude Code, Codex and DSH. A ledger records which ones are done, so no
   transcript is read twice.
3. **Extract.** It pulls out candidates: things you said you wanted, "later" or
   "v2" items, work that stalled, bugs you hit, and questions left unanswered.
   Each candidate keeps your exact quote, the date and the thread link.
4. **Verify.** A quick check against the real project: is it already done
   (git log, the code), already planned, or no longer relevant? Anything dead is
   dropped, with the reason recorded.
5. **Plan.** Each survivor becomes a small `PLAN.md` using the skills: the
   goal, 3–8 tasks, what "done" means, and what not to do.
6. **Approve.** You get a short menu with yes/no per idea (see the approval
   options below).
7. **Build.** The task loop runs the approved plans one at a time, on a branch,
   never on main.
8. **Morning brief.** One message in plain English: what ran, what finished,
   what's waiting for you, and one yes/no per item to keep or discard.

## What makes it good instead of noisy

| Guard | Why |
|---|---|
| **Evidence or it doesn't exist.** Every idea quotes you, with date and thread. | Stops invented work. You can tell at a glance it came from you. |
| **Freshness check before planning.** | Half of old ideas are already done or dead. Cheapest filter first. |
| **Ranked, capped at 3 a night.** Score = how often you mentioned it × how recent × how confident the check was × how easy it is to undo. | A few finished things beat ten half-done ones. |
| **Remembers your no.** A rejected idea never comes back unless you raise it again. | The menu gets sharper every night. |
| **Branch-only builds.** Nothing is pushed, merged, deployed or paid for overnight. | The worst night is a branch you delete. |
| **Git-verified done** (from task-loop.md). | Workers can't claim finished work that isn't committed. |
| **Budget and time cap per night**, plus a kill switch (`/autopilot off`). | No surprise bills, and you can stop it from your phone. |
| **Stops the moment you're back.** It finishes the current task, then pauses. | Your live sessions never compete with it. |
| **Same for every harness.** The contract is files (`PLAN.md`, `PROGRESS.md`, ledger), not any one harness's features. | Your rule. |

## The approval problem (the one real decision)

You're asleep when it would need your yes. There are three ways to handle that:

- **A. Menu before bed.** When you go idle, it proposes and pings you. Whatever
  you say yes to runs overnight, and anything unanswered waits for morning.
- **B. Plans overnight, builds by day.** Overnight it only harvests, verifies
  and writes plans, which is safe. In the morning you tap yes, and the builds run
  while you work.
- **C. Standing yes per project.** You pre-approve projects ("realpage: yes,
  overnight OK"). Ideas for those run without asking; ideas for new projects
  only get a plan.

The recommendation is C combined with B. It gets real overnight work on the
projects you trust, and everything else waits as a ready-to-approve plan.

## What already exists

- **Scheduler:** the SQLite scheduler with a 30 s master loop, used for the idle trigger.
- **Session DB:** holds last-message timestamps, which is how "you're idle" is detected.
- **Transcript locations:**
  - `~/.claude/projects/*/*.jsonl`
  - `~/.codex-ccdb/sessions/`
  - `~/.local/state/ccdb/dsh/sessions/`
- **Task loop:** the engine that runs approved plans (`task-loop.md`).
- **Global skills:** `planning-with-files` and `one-question` for plan shape and questions.

## Build order

1. Harvest, extract and verify, reporting only: no building, nothing touched. Run it
   on the last week's transcripts and see whether the ideas are any good.
2. The task loop (task-loop.md).
3. Idle trigger, approval menu and morning brief.
4. Standing per-project yes.
