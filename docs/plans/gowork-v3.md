# gowork v3: goals and the agentic roadmap

Written 2026-09-15 with Drew. Scope: **gowork only**, never the model harnesses or the
rest of the bot. Everything stays on branch `feat/task-loop` and goes live in **one
restart, when Drew says so**. Built in a normal session (not /gowork), one idea at a time.

Check: uv run ruff check claude_discord/ claude_code_core/ && uv run pytest tests/test_task_loop.py tests/test_task_loop_cog.py tests/test_gowork_ending.py tests/test_work_copy.py -q
Sources: `.planning/harness-research/cards-agentic-workflow.md` (the ideas),
`.planning/harness-research/goal-elicitation.md` (the goal interview research).

## Waiting for the restart already (done)
- Usage limit pauses and asks in the thread (plus an optional backup AI from another session).
- "close" in the thread; only bare commands end or delete a build.
- The person's words come first; PAUSE / SKIP / PLAN endings.
- Everything is said in the build's thread; "checking the work…" while the check runs.
- After every step is done the thread is a normal chat; "looks good" keeps it.
- The planning session can change steps the build hasn't started.

## G. A goal built into every build (Drew's pick: no separate keyword)
- [x] **G1 Goal lines.** A plan may have `Goal:` and `Done when:` lines. Every step's prompt
  starts with them, and the end checker checks "Done when" as one of its checks.
- [x] **G2 Goal interview.** A plan with no goal starts with a short interview in the build's
  thread: the AI reads the project first, opens with 2–3 guesses (lettered, recommended
  first), asks at most 5 questions one at a time, then shows the goal and done test for
  approve / stricter / smaller / change / start over. The approved lines go into the plan.
- [x] **G3 Goal not met.** When every step is done but "Done when" fails, the finished card
  says why and proposes the missing steps; "add them" adds them and the build goes on.

## Ideas from the roadmap (in the doc's order)
- [x] **3 Right AI per step.** (Drew picked: the bot decides.) "A" on the start list lets a
  quick, cheap AI pick the AI before every step; an AI that hit a usage limit is skipped.
- [x] **2 Parallel steps.** (Drew picked: a quick AI groups them.) Up to 3 independent steps
  run at the same time, each in its own copy and short-lived thread, then merge; a step that
  fails or clashes runs again on its own. The start card shows the groups.
- [x] **4 Smart unsticking.** Before asking Drew, a stuck step tries the strongest AI (same kind
  first), then a fresh session splits it into 2–3 smaller steps; then it asks. The build goes
  back to its usual AI after. Steps that need only Drew (keys, money, decisions) aren't split.
- [x] **7 Learning.** Every step appends a record (AI, time, how it ended) to
  `step-records.jsonl` next to the build copies. The step picker gets each AI's track record;
  the finished card and the progress log get 2–3 "next time" bullets.
- [x] **1 Goal mode across builds.** (Drew picked: on its own, capped.) When the goal isn't
  met, the build adds the missing steps and keeps going by itself, up to 3 rounds; then the
  finished card shows the proposal and waits for "add them".
- [x] **6 Overnight queue.** (Drew picked: "queue it", starts right away, 8 am summary.)
  `POST /api/loops` with `"queue": true`; builds run one after another and the line moves on
  when one finishes or waits for Drew; no goal interview, AI picked per step; the 8 am summary
  goes to the thread of the last queued build.
  **At the restart:** add the "queue it" rule to `~/AGENTS.md` (not before — the old bot
  ignores `"queue"` and would start the build at once).
- [x] **5 Team-ups.** (Drew picked: review every step.) After a step passes the bot's checks,
  a different AI (another kind first, the strongest first) reviews the diff; "CHANGES" unticks
  the step and the builder gets the notes (not a try). One bounce per step; a second
  "CHANGES" is only written to the progress log. Parallel steps aren't reviewed yet.
  Cost trim (Drew, 2026-09-15): only **hard** steps are reviewed (the picker chose the
  strongest AI, the step got stuck, or Haiku says HARD), by a **mid-level** AI, not the priciest.
- [x] **8 Slim briefing.** Build steps skip the concurrency notice (their copy is private) and
  get a one-line file rule instead of the chat-session card rules; no lounge (done earlier).

## Modes (Drew, 2026-09-15): cost, speed and quality
- **balanced** (default): as built — hard steps reviewed by a mid-level AI, strongest AI when
  stuck, up to 3 goal rounds.
- **cheap**: no reviews, a stuck step goes straight to being split, goal steps are proposed
  and wait for "add them".
- **careful**: every step reviewed by the strongest other AI, strongest AI when stuck.
- Chosen in words ("go work, careful"): `/gowork mode:`, `"mode"` in `POST /api/loops`
  (queue too). **At the restart:** add the mode words to the `~/AGENTS.md` start-by-words rule.

## How to try it (after the one restart)
- Start `go work` on a plan with no `Goal:` line: the build's thread asks a few lettered
  questions and writes the goal into the plan.
- Watch a step's messages: each one names the goal it's working toward.
- At the end, the finished card says whether the goal's done test passed.
