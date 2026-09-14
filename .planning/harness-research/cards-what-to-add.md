# What We Can Add or Improve

## What the bot already does well
- ✅ **A written "done":** every gowork plan is a checklist plus one test command.
- ✅ **Proof, not claims:** after each step the bot runs the test itself and checks the AI really saved (committed) its work.
- ✅ **One retry with the reason:** if the test fails, the AI gets the exact error and one more try, then it stops and says "stuck" in plain words.
- ✅ **Saved progress:** each step writes a progress note, and builds pick up where they left off after a bot restart.
- ✅ **Fresh checker at the end:** a new session does the "How to try it" checks before you get the final card.
- ✅ **Backup AI:** it switches to a backup AI by itself when it hits a usage limit.
- 🟢 So layers 1, 4 and 5 from the article are mostly there, but only inside gowork.

## Biggest gaps (my top picks)
### ⭐ 1. A "can't do that" wall that works for every AI
- Today "never push, never deploy, never spend money without Drew" is only a sentence in the instructions. Claude, Codex and DeepSeek can each ignore it.
- Add real blocks in the bot's work copies: pushing to GitHub, deploying, and deleting outside the project get stopped unless you said OK.
- Works the same for every model, because it sits in git and the bot, not in one AI's settings.
- **Size:** small, about 2–3 steps.
### ⭐ 2. A run log for every step
- Today the progress note is whatever the AI felt like writing.
- Add a short fixed record the bot writes itself for each step: which AI, how long, whether the test passed, how many retries, which files changed, and why it stopped.
- Then you can see things like "DeepSeek gets stuck 3× more than Sonnet on this repo."
- **Size:** small–medium.
### ⭐ 3. A mistake book (turn repeat failures into rules)
- When a step gets stuck, the bot sorts it into a type: missing tool, missing info, bad test, plan too big, or AI error.
- A repeat mistake becomes a one-line rule or a test that every future build reads.
- This is the article's "one failure improves every future run."
- **Size:** medium.

## Other good additions
### 4. A fresh reviewer after each step (not only at the end)
- A second AI with fresh eyes checks each step's change against what the step asked for.
- It catches the "solved an easier version and said done" problem early.
- ⚠️ Every step costs more AI time. It could be optional per plan.
### 5. A "done means" line on each step
- Today there's one test for the whole plan.
- Each step also gets its own "done when…" line that the bot or the reviewer checks.
- It stops steps from quietly shrinking.
### 6. A slimmer briefing per session
- Every session gets the whole skill list, the lounge and all the rules, even for a 15-minute step.
- Give gowork steps only what the step needs, plus a map of where to find the rest.
- Leaves more of the AI's attention for the work, which matters most for smaller models.
### 7. "Prove it" for normal chats
- Outside gowork there's no check at all: when a chat AI says "done", nothing verifies it.
- Add an optional "prove it" reply that runs the project's test and a quick fresh-eyes check.

## Ideas to borrow from other projects
- **cc-connect:** one chat bridge across many AIs, and bots in a group chat can hand work to each other. Worth a look for how it keeps AIs behaving the same.
- **ralph:** keeps looping until every item in a requirements doc is done. Close to gowork, so compare its stop rules.
- **ECC:** plan first, test gates, fresh-context self-review. Good ideas for #4 and #5.
- **AWS Agent Orchestrator:** a boss AI supervising worker AIs, with good crash recovery.

## Suggested order
- ⬜ First: **#1 the wall** (safety for every AI) and **#2 the run log** (you can't improve what you can't see).
- ⬜ Next: **#3 the mistake book**, which feeds on the run log.
- ⬜ Then: **#5 "done means"** and **#4 the reviewer**.
- ⬜ Later: **#6 the slim briefing** and **#7 prove-it for chats**.
