# Actual commands you can type

**These are the 34 top-level slash commands registered on DrewAI in the latest check.** David and iMac may have different installed lists; this is not a proposed list of new commands.

Type `/` and select the command; Discord shows its required inputs. This lists what each command does so we can decide which ones you use and which ones to remove. Nothing has been removed.

## Start and find work

- **/launcher** — Open the folder/session buttons.
- **/cdnew** — Open a NEW session thread in a chosen folder.
- **/cd** — Move THIS thread to another folder and start a fresh session there.
- **/sessions** — List known sessions.
- **/search** — Find an old thread by keywords.
- **/resume** — Resume a previous session in a NEW thread.
- **/resume-info** — Show a terminal command for resuming this session outside Discord.

## Control the current conversation

- **/stop** — Stop the current run; keep its conversation.
- **/fork** — Branch the current conversation into a new thread.
- **/compact** — Summarize the conversation to free context space.
- **/context** — Show context-window usage.
- **/rewind** — Return to an earlier conversation point; does not undo edited files.
- **/clear** — Reset this thread’s session; the next message starts fresh.
- **/goal** — Set or check the condition the agent should work toward.

## Choose the agent and its settings

- **/switch** — Choose an agent and model together.
- **/backend** — Show or change the agent, such as Claude or Codex.
- **/model** — Show or change the model; also has a local-model install option.
  - Existing subcommands: `/model show`, `/model set`, `/model install`.
- **/effort** — Show or change supported thinking-effort settings.
- **/skill** — Run an installed skill.
- **/usage** — Show the usage information the backend provides.
- **/engine-status** — Control the Codex usage footer after replies; not a bot-health check.

## Plans, reminders and tools

- **/gowork** — Work through a written plan, one task at a time.
- **/stopwork** — Stop the plan loop after its current task.
- **/remind** — Set a reminder for a specified time.
- **/tools-show** — Show the current allowed tools.
- **/tools-set** — Change allowed tools using a picker.
- **/tools-reset** — Remove that tools override and return to configured defaults.

## Import and maintenance

- **/sync-sessions** — Import local terminal sessions as Discord threads.
- **/sync-settings** — Configure how terminal-session import works.
- **/worktree-list** — List session working copies.
- **/worktree-cleanup** — Remove clean orphaned session working copies.
- **/ollama** — Inspect and manage locally installed models.
  - Existing subcommands: `/ollama status`, `/ollama list`, `/ollama ps`, `/ollama show`, `/ollama pull`, `/ollama rm`, `/ollama use`.
- **/upgrade** — Trigger a framework update.
- **/help** — Show the available commands and descriptions.
