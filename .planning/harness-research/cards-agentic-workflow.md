# Making the Discord More Agentic

## What we already have in this Discord
### Doing work
- **Chat threads with any AI:** Claude, Codex, DeepSeek/GLM, local models, switched per thread.
- **/goal:** one chat keeps working until a condition you set is met.
- **gowork:** a plan runs one step at a time in fresh sessions, the bot tests each step, then a fresh checker reviews the build at the end. It switches to a backup AI on usage limits, resumes after restarts, and the plan can be edited mid-build.
- **Lockin feature workflow:** approved features go out to several AI workers at the same time, then get merged back.
- **Swarm:** a separate multi-agent front door.
### Starting work
- **allwork:** turns your voice notes into build ideas and queues the ones you pick.
- **Scheduler and reminders:** AI jobs that run on a timer.
- **Webhooks, alert responder, job-failure triage, watchdog:** things outside the bot can start AI work.
### AIs working together
- **Spawn:** one session can start a new thread.
- **Thread-to-thread messages:** one session can message another.
- **The lounge, claims and session list:** sessions can see each other.
- **/ask:** sends one question out to an outside AI.
### Skills
- research, grilling, to-spec, to-tickets, wayfinder, planning, handoff.
- 🟢 **Lots of pieces, but they mostly work alone.** Each AI still waits for you between steps.

## The big gap
- Every piece needs **you** to connect it to the next. You pick the plan, pick the AI, start the build, read the result, then write the next plan.
- gowork runs **one step at a time with one AI**, even when steps don't depend on each other.
- When a step gets stuck, the build **stops and waits**. It doesn't try something smarter first.
- The AIs don't really **team up**: no "Codex builds it, Claude reviews it" and no "three AIs try, the best one wins".

## Ideas (most agentic first)
### ⭐ 1. Goal mode: one sentence in, finished project out
- You give a goal ("lead finder that works for 5 states").
- A lead AI writes the plan, runs gowork, reads the results, writes the next plan, and repeats until the goal is met or it needs you.
- You only get pinged for real decisions.
- Joins /goal and gowork into one loop. Similar to **ralph**.
### ⭐ 2. Parallel gowork
- The planner marks which steps don't depend on each other.
- Those steps run at the same time in their own copies, then get merged.
- Uses the Lockin parallel machinery inside gowork. It could finish builds 2–4× faster.
### ⭐ 3. The right AI for each step
- Each step gets the AI that suits it: a cheap, fast model for boring steps, the strongest one for hard ones, and a different AI to review than the one that built it.
- The planner chooses and you can override. It uses every model we have, not just one per build.
### 4. Smart unsticking instead of stopping
- When a step gets stuck, the bot first tries on its own: a stronger model, splitting the step into smaller ones, or a quick research session.
- It only asks you if all of that fails.
### 5. AI team-ups
- **Builder + reviewer:** one AI builds and a different one reviews each step.
- **Bake-off:** 2–3 AIs try the same hard step and the one that passes the test wins.
- **Researcher helper:** a step can spin off a quick research thread and use its answer.
### 6. A build queue that runs overnight
- Voice notes (allwork), scheduled jobs and plans all feed one queue.
- Builds run back-to-back while you sleep.
- You get one morning summary: what got built, what's waiting for you, and a link to try each thing.
### 7. The bot learns from every build
- After each build, it writes down what slowed it down, which AI did best on which kind of step, and what to plan differently.
- The next plan uses that automatically. Builds get better over time, not just safer.

## Repos to copy from
- **ralph:** goal loop until everything is done (idea 1).
- **Multica:** a board of issues that about 20 different AIs pick up like teammates (ideas 3 and 6).
- **AWS Agent Orchestrator:** a boss AI running Claude and Codex workers side by side (ideas 2 and 5).
- **cc-connect:** bots in one group chat hand work to each other (idea 5).

## Suggested order
- ⬜ **3, the right AI per step**: small, and makes everything after it better.
- ⬜ **2, parallel gowork**: big speed win, reusing Lockin.
- ⬜ **4, smart unsticking**: fewer builds waiting on you.
- ⬜ **1, goal mode**: the headline feature, built on 2–4.
- ⬜ **6 overnight queue**, **5 team-ups** and **7 learning**: after that.
