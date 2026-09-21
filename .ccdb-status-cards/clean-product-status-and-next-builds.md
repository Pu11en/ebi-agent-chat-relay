# Clean Product Status And Next Builds

## Where the project stands right now

- ✅ **The live Discord bot is on the dev branch**, not main.
- ✅ **The dev branch is pushed** and clean.
- ⚠️ **Main does not have this work yet.**
- ⚠️ **There are 153 commits on the dev branch that are not merged to main.**
- ✅ DrewAI already has the new local route pieces for agent-to-agent work.
- ⚠️ David’s bot is still missing the matching route/config, so David cannot reliably ask DrewAI yet.

## What is already built on the dev branch

- ✅ **Named agent routing:** the bot can expose a route called `drewai`.
- ✅ **Federated routing support:** a different bot can send a request to another bot’s API instead of needing the same Discord session.
- ✅ **DrewAI project lookup worker:** DrewAI can start a read-only worker to search Drew’s main projects folder.
- ✅ **Handoff memory:** shared agent instructions now say that “ask DrewAI” means a real agent-to-agent lookup, not “I do not know DrewAI.”
- ✅ **Planning foundations:** prior wave work added pieces for go-work, setup inventory, audit inventory, session lifecycle, handoff ledger, run state, and project catalog support.

## What is still missing before this feels clean

- ⬜ **David to DrewAI route:** David’s bot needs the route that points to DrewAI’s project lookup endpoint.
- ⬜ **Result return:** when DrewAI finds something, the useful answer should come back into David’s original thread.
- ⬜ **Natural text trigger:** in a normal thread, phrases like “ask DrewAI to search Drew’s projects” should route automatically.
- ⬜ **Auto-close finished worker threads:** worker threads should close/archive after the controlling session has the result.
- ⬜ **Command cleanup:** remove or hide old commands, simplify `/switch`, and keep different command behavior for Control Center versus session threads.
- ⬜ **Global inventory view:** show tools, skills, harness settings, and custom add-ons in one place so waste or broken config is visible.
- ⬜ **Full test cleanup:** the full test suite still has environment-sensitive failures from live Discord config leaking into tests.

## What the final product should feel like

- ✅ In a session thread, Drew can type natural words, not hunt for commands.
- ✅ If Drew says, “ask DrewAI to look in Drew’s projects,” the current bot sends the right request to DrewAI.
- ✅ DrewAI uses cheap/read-only workers to search the project folder and returns paths, summaries, and useful context.
- ✅ Finished worker threads close themselves after the main session has what it needs.
- ✅ The Control Center stays clean and is for starting, finding, settings, health, and help.
- ✅ Session threads are for working: switch model, stop, close, handoff, and continue the actual task.
- ✅ Drew can open one dashboard-style view to see what tools, skills, harness settings, and custom pieces exist across the bot setup.

## Recommended build order

- ⏳ **1. Auto-close done worker threads first.**
  - This fixes the daily clutter problem Drew keeps hitting.
  - It also makes parallel work less annoying immediately.
- ⏳ **2. David to DrewAI route second.**
  - This makes “ask DrewAI” real from David’s side.
  - It must include a simple return path back to the original David thread.
- ⏳ **3. Natural text trigger third.**
  - This is what makes the bot feel automatic instead of slash-command heavy.
  - It should start with safe phrases only: “ask DrewAI,” “look in Drew’s projects,” and “close done threads.”
- ⏳ **4. Command cleanup fourth.**
  - Once natural routing exists, old commands can be removed with less risk.
  - `/switch` should become fast and simple, replacing old backend/model flows.
- ⏳ **5. Inventory dashboard fifth.**
  - This gives Drew the professional “what do I have installed and is it wasting tokens?” view.
- ⏳ **6. Full-suite cleanup and main merge last.**
  - This is the clean release step.
  - Main should not take the whole branch until the live behavior is proven and tests are not polluted by the live environment.

## Clean release meaning

- ✅ “Live dev” means Drew can test it in Discord now.
- ⚠️ “Clean product” means the features work together, finished threads close, David can reach DrewAI, commands are simplified, and tests are clean.
- ⚠️ “Merged to main” should happen only after that clean-product pass, unless Drew explicitly says to merge early.
- ✅ The current branch is a good testing branch, not the final polished main release yet.
