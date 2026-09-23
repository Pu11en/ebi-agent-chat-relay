# Agentic Workflow: Everything in One Place

## The starting point
### One harness for all our AIs?
- **No.** The bot is one shared front door (Discord, the lounge, gowork, claims, the session list), but each AI works inside its own harness.
- **Claude** runs inside Claude Code. **Codex** and **local models** run inside the Codex CLI. **DeepSeek / GLM** run inside DSH (DeepSeek Harness). Outside agents can connect through AG-UI.
- So features built into one harness don't reach the others. **/goal** is one example: it only works on Claude, because it hands off to Claude Code's own /goal.
- Anything we build **in the bot itself**, like gowork, works for every AI.
### The article (Ichigo, "Harness Engineering: 6 Layers")
- Its main line: **a better prompt improves one answer, a better harness improves every run.**
- **1. Contract:** turn the request into a clear task with a written "done" test before starting.
- **2. Context compiler:** give the AI a short map and fetch details only when needed, not one giant prompt.
- **3. Tool gateway:** every tool has one clear job, a timeout and a clear success or failure answer.
- **4. Durable state:** keep progress, decisions and the next step in a file, not only in the chat.
- **5. Evidence gate:** work is done only when checks pass, and a fresh second AI reviews anything that needs judgment.
- **6. Trace and recovery:** record each run, name the failure, fix the missing piece, rerun that case, and turn repeat mistakes into rules or tests.
- **Build order:** define done → wrap one tool → one state file → one retry path → save the trace → turn repeat failures into rules. Add more AIs only after that.
- ✅ The full text is saved in the relay's planning notes. The post's example code was pictures, so only the words came through.

## What we already have in this Discord
### Doing work
- **Chat threads with any AI:** Claude, Codex, DeepSeek/GLM or local models, switched per thread with /backend, /model and /effort. You can also fork, rewind and compact a thread.
- **/goal** *(Claude only)*: one chat keeps working until a condition you set is met.
- **gowork:** a plan runs one step at a time, each in a fresh session on a safe copy of the project.
  - The bot runs the plan's test itself and checks the AI really saved its work.
  - A failed step gets one retry with the exact error, then it says "stuck" in plain words.
  - Each step writes a progress note, and builds resume after a bot restart.
  - It switches to a backup AI on a usage limit, and the planning session can change steps that haven't started.
  - At the end, a fresh checker does the "How to try it" checks and posts a final card.
- **Lockin feature workflow:** approved features are handed to several AI workers, each in its own Discord thread, then merged back.
- **Swarm:** a separate multi-agent front door, used only when you say "swarm".
### Starting work
- **allwork:** turns your voice notes into a lettered list of build ideas and queues the ones you pick.
- **Scheduler and /remind:** AI jobs that run on a timer.
- **Webhooks, alert responder, job-failure triage and watchdog:** outside events can start AI work.
### AIs working together
- **Spawn:** one session can open a new thread with a new session.
- **Thread-to-thread messages:** one session can message another, either waiting its turn or interrupting.
- **Lounge, claims and session list:** sessions can see what the others are doing.
- **/ask:** sends one anonymized question to an outside AI.
### Skills
- research, grilling, one-question, to-spec, to-tickets, wayfinder, planning-with-files, prototype and handoff.
### How it lines up with the article
- ✅ Contract, durable state and evidence gate: mostly there, **but only inside gowork**.
- ⚠️ Context: every session gets the whole skill list, the lounge and all the rules, even for a 15-minute step.
- ⚠️ Trace and learning: progress notes are free text, nothing records how each AI performed, and mistakes don't become rules for next time.

## The big gap
- Every piece needs **you** to connect it to the next. You pick the plan, pick the AI, start the build, read the result and write the next plan.
- gowork runs **one step at a time with one AI**, even when steps don't depend on each other.
- When a step gets stuck, the build **stops and waits**. It doesn't try something smarter first.
- The AIs don't really **team up**: there's no "Codex builds it, Claude reviews it" and no "three AIs try, the best one wins".
- Lockin (parallel) and gowork (step by step) are **two separate systems** that don't share their strengths.

## The ideas (most agentic first)
### ⭐ 1. Goal mode: one sentence in, finished project out
- You give a goal, for example "lead finder that works for 5 states".
- A lead AI writes the plan, runs gowork, reads the results, writes the next plan, and repeats until the goal is met or it truly needs you.
- You only get pinged for real decisions.
- It works for **every AI**, unlike /goal, because it lives in the bot. Similar to **ralph**.
- **Size:** big. It's best built after ideas 2–4.
### ⭐ 2. Parallel gowork
- The planner marks which steps don't depend on each other.
- Those steps run at the same time, each in its own copy, then get merged, reusing Lockin's parallel machinery.
- Builds could finish **2–4× faster**.
- **Size:** medium.
### ⭐ 3. The right AI for each step
- Each step is tagged with the AI that suits it: a cheap, fast model for boring steps, the strongest for hard ones, and a **different AI to review** than the one that built it.
- The planner chooses and you can override in plain words.
- Uses every model we have, not just one per build.
- **Size:** small. It makes everything after it better.
### 4. Smart unsticking instead of stopping
- When a step gets stuck, the bot first tries on its own: a stronger model, splitting the step into smaller ones, or a quick research session.
- It only asks you if all of that fails.
- **Size:** small–medium.
### 5. AI team-ups
- **Builder + reviewer:** one AI builds, a different one reviews each step with fresh eyes and catches "solved an easier version and said done".
- **Bake-off:** 2–3 AIs try the same hard step and the one that passes the test wins.
- **Research helper:** a step can spin off a quick research thread and use its answer.
- **Size:** medium. ⚠️ Costs more AI time per step, so it would be used when it's worth it.
### 6. An overnight build queue
- Voice notes (allwork), scheduled jobs and plans all feed one queue.
- Builds run back-to-back while you sleep.
- You get one morning summary: what got built, what's waiting for you, and how to try each thing.
- **Size:** medium.
### 7. The bot learns from every build
- The bot writes a short fixed record for each step: which AI, how long, test result, retries, and why it stopped.
- After each build it notes what slowed things down, which AI did best at which kind of step, and what to plan differently.
- The next plan uses that automatically, and it feeds idea 3's AI choice.
- **Size:** medium.
### 8. A slimmer briefing per step
- Give each gowork step only what it needs, plus a map of where to find the rest.
- Leaves more attention for the actual work, which helps smaller and cheaper models most.
- **Size:** small.
### 9. "Prove it" for normal chats
- Outside gowork, when a chat AI says "done", nothing checks it.
- An optional "prove it" reply would run the project's test and a quick fresh-eyes review.
- **Size:** small.

## Repos to copy from
- **ralph** (github.com/snarktank/ralph, ~22k ⭐): loops until every item in a requirements doc is done. Model for **goal mode (1)**.
- **Multica** (github.com/multica-ai/multica, ~50k ⭐): a board of issues that about 20 different AI tools pick up like teammates. Model for **AI per step (3)** and the **queue (6)**.
- **AWS CLI Agent Orchestrator** (github.com/awslabs/cli-agent-orchestrator, ~1.3k ⭐): a boss AI running Claude and Codex workers side by side, with good recovery. Model for **parallel (2)** and **team-ups (5)**.
- **cc-connect** (github.com/chenhg5/cc-connect, ~15.5k ⭐): the closest twin of our bot, putting Claude, Codex, Gemini and OpenCode into Discord, Slack and Telegram, where bots in one chat can hand work to each other. Model for **team-ups (5)**.
- **ECC / everything-claude-code** (github.com/affaan-m/ECC, ~258k ⭐): plan first, test gates and fresh-context self-review, with adapters for other AIs. Model for **reviewer (5)** and **learning (7)**.
- **OpenHarness** (github.com/HKUDS/OpenHarness, ~16k ⭐): an open agent harness with a built-in agent loop.
- Smaller ones: myclaude, claude_codex_bridge, polpo. Lists for more: awesome-cli-coding-agents and awesome-agent-orchestrators.

## Suggested order
- ⬜ **3. The right AI per step:** small, and makes everything after it better.
- ⬜ **2. Parallel gowork:** a big speed win, reusing Lockin.
- ⬜ **4. Smart unsticking:** fewer builds waiting on you.
- ⬜ **7. Learning:** feeds better AI choices into 3.
- ⬜ **1. Goal mode:** the headline feature, built on the steps above.
- ⬜ **6. Overnight queue**, **5. team-ups**, **8. slim briefing** and **9. prove-it**: after that.
### Review notes
- ✅ Checked against the code: gowork's retry, test, resume, backup AI and end checker all exist.
- ⚠️ Correction to earlier: **/goal is Claude only**, so it isn't a cross-AI feature today.
- ⚠️ gowork lives on its own branch and isn't merged into the bot's main line yet. New gowork features would build on that branch.
