# Lockin AI

The new feature planning workflow is installed and its two-feature Discord
trial passed. Read [the current usage guide](docs/feature-workflow-setup.md).
The maintained installed checkout is `/home/drewp/main-projects/lockin-workflow-runtime`;
the canonical relay folder and existing projects remain intact.

Lockin AI is Drew's Discord-based development workspace. Ebi Agent Chat Relay
is the upstream software powering it; Codex runs the coding sessions on WSL.

## Open this project

The easy-to-find folder is `/home/drewp/main-projects/Lockin AI`.
It is a directory shortcut (symlink) to the one existing repository at
`/home/drewp/main-projects/ebi-agent-chat-relay`. Both paths open the same files.
There is no second installation, copied repository, or new runtime.

In Discord, use the dedicated
[Lockin AI — setup and improvements](https://discord.com/channels/1546639912848199742/1546680749397250050)
thread (created with no AI run started).
Continue replying in that thread to keep the conversation. To start another
conversation about this project, run `/cdnew` in `#control-center`, type
`lockin`, and choose the `Lockin AI` folder suggestion. The existing picker
resolves shortcuts to the real folder, so its newly created thread may be
named `ebi-agent-chat-relay`. It still targets the correct Lockin AI project.

## What belongs here

| Part | Where it lives |
| --- | --- |
| Bot code and upstream documentation | This repository; `claude_discord/` and `docs/` |
| Local project-picker extension | `extensions/project_picker/` |
| Local deployment settings and credentials | `.env` in the real repository; never post its contents or commit it |
| Session-to-project mappings and runtime state | `data/sessions.db` |
| Cheat sheet and setup/research notes | `docs/` |
| Service that keeps the bot running | `/home/drewp/.config/systemd/user/ebi-agent-chat-relay.service` |
| Shared Codex configuration and installed skills | `/mnt/c/Users/drewp/.codex` (outside this repository) |
| Live channels, roles, and messages | Discord's servers, managed through Discord or its API |

Meme Explorer, Jobs, and other development projects remain separate folders
under `/home/drewp/main-projects`; they do not move inside Lockin AI.

## Example requests in the Lockin AI thread

- “Explain how this Discord setup works. Read LOCKIN-AI.md first.”
- “Help me improve the way I choose projects.”
- “Research better planning skills for this setup. Don't install anything yet.”
- “Update the cheat sheet to explain this feature.”
- “Show me the channel changes you recommend before making them.”

Opening this folder does not itself change Discord or activate a new planner.
Bot code or configuration changes may need a service restart; a restart can
interrupt active conversations, so check running sessions before scheduling it.
The folder shortcut did not require any restart or core-code changes.

## Planning research status — September 7, 2026

Drew selected the feature-scoped direction: OpenSpec plans, the two selected
wshobson coordination skills, and Ebi worker threads. GSD is excluded.
Portable Planner's plugin was already disabled; the stale auto-use instruction
has now been removed from the shared Codex AGENTS.md. Existing plans remain
reference material. Until the replacement is set up, planning uses normal
Codex conversation, not Portable Planner.

The selected replacement has now passed the two-feature trial. Follow
[the current usage guide](docs/feature-workflow-setup.md). The research notes
in the canonical checkout are preserved as historical reference.
