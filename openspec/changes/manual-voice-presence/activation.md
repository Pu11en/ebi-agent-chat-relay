# Voice work closed — 2026-09-27

Drew selected activation and a normal-session Discord-bot audit, requested a
grilling conversation before future project decisions, then explicitly said
"first push save and be done with the voice thing". This authorized publication
without another user-run voice test; do not claim he tested the new commands.

## Saved and published

- Branch: `fix/voice-manual-control` on Pu11en/ebi-agent-chat-relay.
- Voice continuation repair: f388890 (same change as the user-tested 50ff65f).
- Manual presence: d2fad81 (same voice files as the tested faa3044).
- Based directly on origin/main 53fa8a7, excluding the unrelated DeepSeek Stop
  commit and every shelved Jester/Goku naming patch.
- Source/package files are byte-identical to the previously verified voice
  change: 165 voice tests passed, with the main quality gate recorded in evidence.md.
- `make verify` also passed on this voice-only publication branch: formatting,
  lint, zero type errors/warnings, and 6,061 Python tests (17 test warnings,
  144.07 seconds). Its lower test count excludes the unrelated Stop-fix tests.
- Feature branch pushed and remote SHA verified; no pull request, merge, force
  push, branch/worktree deletion or GitHub deployment was performed.

## Activated locally

- Applied only the tested manual-presence delta to the permanent voice checkout,
  preserving its existing last-sentence repair and all transcripts/settings.
- All 22 source/package files match the published voice code. Runtime README
  retained its historical differences outside the targeted manual-presence edits.
- 47 focused tests passed in the actual runtime checkout, covering both repairs.
- Preflight lounge and /api/sessions found two unrelated running agents and no
  queued agents. Main bot was deliberately not restarted.
- Only drew-ai-voice-transcripts.service restarted, at 18:12:25 CDT; PID 750788.
  Discord ready logged at 18:12:32; no automatic service restart.
- After startup: zero recording sessions, zero pending audio jobs; the existing
  single failed transcription job remains untouched. Discord's current-bot voice
  state lookup returned 404/10065 (no voice state).
- Main bot remains PID 387476 and its health endpoint returns 200/ok.
- No fabricated owner command, speech-model benchmark, paid model call or extra
  agent was run. Separate Jester Voice was not inspected or changed.

## Final behavior and next boundary

In voice-transcripts, the owner uses `!voice join` after entering the configured
room, or `!voice leave` to remove DrewAI immediately; `!voice` explains usage.
Presence, worker restarts, health checks and old Resume buttons cannot start a
new voice connection. Already captured speech is kept for processing.

Legacy voice work ends here. Historical live-draft/Edit/Retry plans and the local
Jester/Goku naming patches are shelved, not future authorized work. Next is a
one-question-at-a-time grilling conversation about the Discord project, in this
normal session; no implementation of its future design until Drew agrees.

GitHub reported one moderate default-branch dependency advisory during push.
It was not investigated or fixed as part of this voice-only change; assess it
during the later Discord-bot audit rather than asserting this patch caused it.
