# Planning brief: add Slack as a third frontend

Written 2026-09-23 from The Aldus outreach session (Discord thread 1552216851805900820).
This is the starting context for a dedicated planning session. It is a brief, not a plan.

## Why this exists

The Aldus has decided **Slack is the client surface**. Discord stays the internal
workshop. The Aldus will run its own Slack first, dogfood it, then show prospects the
real thing. One of the assistants living in that Slack does outreach and email.

Before any of that, the relay needs to speak Slack.

## The key finding: this is NOT a fork

Drew's instinct was to clone the repo and make a Slack version. **Don't.** The repo is
already a frontend x backend platform, decided in ADR-0006 (2026-08-11) and shipped as v4.

- `claude_code_core/frontend.py` (640 lines) defines the contract: two Protocols,
  **`ConversationSurface`** and **`SessionFrontend`**, plus the shared value types
  (`SurfaceCapabilities`, `Notice`, `ChoicePrompt`, `FormPrompt`, `OutboundFile`,
  `InboundMessage`, `InboundAttachment`, `ActivityHandle`, `InterruptHandle`, `TextStream`,
  `StatusKind`, `NoticeLevel`, `Mention`, `ThreadKey`).
- `claude_teams/` is a **complete worked example** of a non-Discord frontend:
  `frontend.py` (201 lines) + `surface.py` (570 lines), plus `cards.py`, `commands.py`,
  `mentions.py`, `interactions.py`, `files.py`, `endpoint.py`, `auth.py`, `jwks.py`,
  `manifest.py`, `pacer.py`, `capabilities.py`, `conversation.py`, `relay/`.
- Frontends are selected at runtime by **`CCDB_FRONTENDS`**, which defaults to `discord`.

So the job is: **add `claude_slack/`, implement the same two protocols, register it as a
selectable frontend.** Teams is the template. The core, every backend (Claude Code, Codex,
local, AG-UI), storage, sessions, approvals, lounge, claims and gowork all come free.

ADR-0006 point 5 says an isolated backward-compatible frontend addition ships as a
**minor release**, so this does not need a new major version or a new ADR.

## What the planning session has to decide

Not code. Decisions, one at a time, Drew choosing:

1. **Which Slack app model.** Socket Mode (no public HTTPS, easiest for an internal
   workspace) versus HTTP Events API with a public receiver (what Teams does). This is the
   biggest fork in the road and it decides the whole deployment story.
2. **What a "thread" maps to.** Discord uses threads, Teams uses conversations. Slack has
   channels, threads and DMs. `derive_thread_key` / `issue_thread_key` in core expect a
   stable external id.
3. **Capabilities to declare.** `SurfaceCapabilities` covers streaming edits, rate limits,
   attachments, choice prompts, forms. Slack has Block Kit, modals, and its own rate limits.
   Each one is a real decision about what the assistant can do in Slack.
4. **How the cards render.** The Discord side sends colored status cards; Teams has
   `cards.py`. Slack's equivalent is Block Kit. Needs its own mapping.
5. **Approvals and buttons.** Slack interactive components need a request-handling path.
6. **Multi-tenant or single workspace first.** The Aldus's own Slack is one workspace;
   selling to clients eventually means many. Decide what v1 assumes.
7. **What v1 deliberately leaves out.**

## Constraints carried in

- Everything stays local until Drew has tried it himself. No push, no PR, no deploy first.
- Local commits are expected and fine.
- Small chunks. One outcome per sitting, 15 to 30 minutes each, written as checkboxes.
- Ask Drew before starting a long build whether it runs with `/gowork` or a normal session.

## Files to read first

- `claude_code_core/frontend.py` — the contract. Read this before anything else.
- `claude_teams/frontend.py` and `claude_teams/surface.py` — the worked example.
- `docs/adr/0006-publish-the-multi-frontend-platform-as-v4.md` — why the boundary is where it is.
- `docs/teams-setup.md` — the shape of an operator guide the Slack one will mirror.
- `README.md` — the frontend x backend explanation.

## Out of scope for that session

- The Aldus's outreach assistant itself. That is product work and belongs in
  `the aldus/product/`. Slack only needs to be able to carry it.
- Anything about warm-up, domains or cold email.
