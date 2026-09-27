## Why

Drew's voice-to-session router dropped the end of a request even though the recorded gap was only 1.788 seconds, below his agreed ten-second silence rule. The audio was successfully transcribed; routing sent the earlier text too soon, so reliability needs to be fixed before adding the live draft controls he selected.

## What Changes

- Join speech by actual capture activity and transcription completion, not merely by whichever text has arrived; submit one complete request after ten seconds of real silence.
- Show a quiet, growing draft inside the addressed session, including Listening and Processing audio states; previews must not start an agent.
- Queue completed requests visibly behind current work, without interrupting it.
- Provide owner-only Edit and Cancel buttons before submission; opening Edit pauses automatic sending, and Save keeps the edited draft held until the owner explicitly presses Send.
- Keep unclear-name drafts in the existing transcript channel with a Choose Session control; never guess a destination or send ordinary room conversation as an instruction.
- Preserve failed requests with Retry, Edit and Cancel; resend only after Drew clicks Retry, and prevent duplicate admission when an earlier response was lost.
- Keep speech recognition retries separate from instruction-delivery retries; this proposal changes the latter, not the former.
- Correct documentation that currently describes both a ten-second collected request and an obsolete ninety-second forwarding window.
- Leave restart behavior alone: no new restart-recovery workflow or automatic replay is part of this change.
- Keep the missing-sentence repair independent from later draft controls; their remaining details do not block that repair or justify more low-value questions.

This began as planning for normal, sequential sessions. Drew subsequently approved only the local missing-sentence repair, now implemented and checked offline. The later controls remain unapproved for implementation; no paid model run, `/gowork`, deployment or broader agent-OS audit is included.

## Capabilities

### New Capabilities

- `voice-request-lifecycle`: Complete captured requests, truthful in-session drafts, explicit destination recovery, ordered admission, correction controls and manual-only delivery retry.

### Modified Capabilities

None: there is no existing voice-routing OpenSpec capability to amend. The implementation already has some of this behavior; the new specification makes its contract explicit.

## Impact

- Voice companion: `extensions/voice_transcripts/src/transport.mjs`, `capture.mjs`, `voice/job-queue.mjs`, `voice/store.mjs`, `control/controller.mjs`, `control/api.mjs` and `index.mjs`, plus focused Node tests and its README.
- Relay: backward-compatible delivery receipt/status support around `POST /api/threads/{id}/spoken`, its database stores, and the existing per-thread run queue; Python tests must cover authorization and duplicate requests.
- Personal character vocabulary, draft display preferences and microphone handling remain in the optional instance extension; reusable receipt/admission behavior belongs in the framework.
- No new service or paid dependency is proposed. Hindsight, broader memory integration, model switching and the separate DeepSeek Stop fix are outside this change.
- The plan is based on `d7439f1`, which includes the locally tested DeepSeek Stop fix; main was `53fa8a7` when inspected. The voice companion is a separate running service and must be checked separately during a later approved try-out.

## Approval and Open Decisions

The user approved local implementation of the missing-sentence repair by selecting A, explicitly without a live restart. The bounded repair design, tasks and verification are recorded in design.md, tasks.md and evidence.md; confirmed preferences remain in owner.json and PLAN.md. Later controls still require their own detailed design and implementation approval. Restart recovery is outside scope.
