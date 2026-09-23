# Keeping your bots, and letting them hand work to each other

## Your idea, in plain words
- An agent in one category writes a prompt into another computer's thread, and **that computer's agent picks it up and does the work**, reporting back in Discord.
- Discord itself is the wire between computers. No remote desktop, no extra network setup.
- ✅ This is the right shape, and it does **not** require merging the bots.

## Do you still need three bots? Yes
- Keep **DrewAI**, **I mac codex** and **david**. Each one is the computer.
- The one-identity idea is a **small code change** (each computer logs in with the same key and only answers its own category), but it's a **big trust and clarity change**: David's computer would hold DrewAI's login key, and every reply anywhere would be labelled "DrewAI".
- ⚠️ And it gives you nothing for computer-to-computer work. **Recommendation: drop it.**

## What's actually blocking the handoff
- Right now every bot **ignores messages written by other bots**, so a task posted by DrewAI into David's thread just sits there until a human retypes it.
- The fix is small and local: accept a message when it comes from a **known partner bot**, in the right category, carrying a clear task marker, and tag each task with an id so a retry never runs twice.
- Replies, acknowledgements and results must never count as new tasks, or two bots talk to each other forever.
- ⚠️ Not built yet on any computer.

## Why this also cleans up the duplicate commands
- Your command list is cluttered because **all three bots register the same commands** in the same server, and Discord shows the owner everything.
- Once the handoff works, **only DrewAI registers commands**. You type /new in David's category, DrewAI hands it to David's agent, David's computer does the work.
- One clean command list, three computers, no merged identities.
- ⚠️ Until then the duplicates stay, because turning David's commands off before routing exists would leave him with nothing to type.
