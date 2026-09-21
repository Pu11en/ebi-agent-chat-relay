# /switch speed-up and the command cleanup — where things stand

## The command cleanup plan (stopped halfway)
- ✅ **Listed all 34 commands** the Lenovo bot has, grouped by what they do.
- ✅ **Agreed for the control-center:** `/new` (pick a folder, open an empty session), `/sessions` (your sessions, newest first, with Open / Close / New in same folder), `/settings` (one menu: Default model, Skills, Shared instructions, Connections, Bot status, Updates), `/help`.
- ✅ **Agreed inside a session thread:** `/stop`, `/switch` (change model), `/help`. Everything else should be doable by just asking in plain words.
- ✅ **Built so far:** only David's `/new` project picker.
- ⏳ **Where we stopped:** talking through the details of each `/settings` section.
- ⬜ **Not done:** building `/sessions` and `/settings`, then removing the old commands. Rule we agreed: **no old command is removed until its replacement is built and you've checked it.**

## Why /switch feels slow today
- ⚠️ When you type `/switch`, the first box is an optional "backend" box, and the model box is also optional. Discord only pops a list open by itself for a **required first box**, so today you have to click into the model box before you see anything.
- ⚠️ There are **35 models** but Discord shows **25 at most**, so some never appear unless you type part of the name.
- ⚠️ 11 of those are older dated Claude versions (Opus 4.5, 4.6, 4.7…) that crowd out the newest models.
- ⚠️ The very first list of the day takes about 1.5 seconds to load, because it asks the model providers what's available.

## The proposed fix
- **One box only:** type `/switch` and the model list opens straight away.
- **Newest models first:** your current model on top with a ✅, then the newest models from each AI; older dated versions stay hidden until you type part of their name.
- **Typing just works:** `/switch opus` then Enter switches, even without clicking a list entry.
- **Ready instantly:** the model list is loaded when the bot starts, so the first try isn't slow.
- The AI (Claude, Codex, DeepSeek, local) is still picked automatically from the model, like the plan says.

## Safety
- ✅ **Nothing has been changed yet**: no code, no commits, no restart.
- The live bot runs from a separate copy of the code. The fix would be built on its own branch, tested, and only switched on when you say it's OK to restart the bot.
