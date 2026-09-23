# One Harness? + The 6-Layer Idea

## Is there one harness for all our models?
- **No, not today.** The bot is one shared front door (Discord, the lounge, gowork, claims), but each AI runs inside **its own** harness.
- **Claude** runs inside Claude Code.
- **Codex** and **local models** run inside the Codex CLI.
- **DeepSeek / GLM** run inside DSH (DeepSeek Harness).
- There is also an AG-UI option for outside agents.
- ⚠️ So rules, memory, checks and tools behave a bit differently per model. A "done" check in one may not exist in another.

## What the article says (the 6 layers)
- ✅ Full text saved in the relay's planning folder (the code pictures in the post didn't come through, only the words).
- **1. Contract:** turn the request into a clear task with a written "done" test before starting.
- **2. Context compiler:** give the AI a short map, fetch details only when needed, not a giant prompt.
- **3. Tool gateway:** every tool has a purpose, limits, a timeout and a clear success/fail answer.
- **4. Durable state:** save progress, decisions and next step in a file, not just the chat.
- **5. Evidence gate:** it's only "done" when checks pass, plus a fresh second AI reviews judgment work.
- **6. Trace + recovery:** record each run, name the failure, fix the missing piece, rerun only that.
- Build order: define done → wrap one tool → one state file → one retry path → save trace → turn repeat mistakes into rules/tests.

## Can we build it? Yes — we already have half
- ✅ **Layer 1 + 5:** gowork plans already have tasks and a `Check:` command run after each task.
- ✅ **Layer 4:** plans, progress files, shared memory folder, claims.
- ⚠️ **Layer 2:** AGENTS.md is a map, but the skill list and lounge get dumped into every session.
- ⬜ **Layer 3:** each harness has its own tool rules; no shared gateway.
- ⬜ **Layer 6:** no shared run log that says "failed because X" across all models.
- **Best move:** build these layers **once, in the bot**, around every model, so Claude, Codex, DSH and local all get the same contract, checks, state and trace.

## Repos to borrow ideas from
- **cc-connect** (15.5k ⭐) — closest twin of our bot: Claude, Codex, Gemini, OpenCode into Discord/Slack/Telegram. github.com/chenhg5/cc-connect
- **Multica** (49.8k ⭐) — hand issues to ~20 different agent CLIs like teammates. github.com/multica-ai/multica
- **ralph** (21.8k ⭐) — loop until every item in a requirements doc is done (like gowork). github.com/snarktank/ralph
- **ECC / everything-claude-code** (258k ⭐) — plan first, test gates, fresh-context self review, adapters for Codex/Gemini. github.com/affaan-m/ECC
- **OpenHarness** (15.7k ⭐) — open agent harness with tool permissions. github.com/HKUDS/OpenHarness
- **AWS CLI Agent Orchestrator** (1.3k ⭐) — boss agent runs Claude/Codex workers, good at recovery. github.com/awslabs/cli-agent-orchestrator
- Also: myclaude, claude_codex_bridge, polpo, and two "awesome" lists for more.
