# Voice-to-session reliability plan — draft for review

Check: `npm --prefix extensions/voice_transcripts test`

Try: `npm --prefix extensions/voice_transcripts test`

These commands run offline checks from the project root; they do not start a bot, record audio or call a paid model. There is no new localhost page. The missing-sentence repair is now implemented locally and passes regression checks; the later draft controls are still only planned.

## What this plan is for

You say a character name such as Zoro, speak naturally, see what the system heard, and get one complete request into that session. You should not need to repeat a sentence because transcription finished late or wonder whether a failed send started a job anyway.

This is the first focused part of your wider agent-OS review. It does not cover the rest of your session history, Hindsight or automatic self-improvement yet.

## The bug we actually found

- Your first recording chunk lasted 30 seconds.
- The next chunk began only 1.788 seconds later, below your ten-second silence rule.
- Both chunks were successfully transcribed, including the last sentence about visitors viewing the website section.
- The router sent the first chunk while the second was still being transcribed, then ignored the last sentence because it had already released Zoro.
- A second race caused an error while updating the listening message during that send.
- An offline replay reproduced the missing sentence without contacting an agent.

## Your agreed behavior

- **One complete thought:** collect every recognized sentence belonging to the request, in order, and wait for ten seconds of real silence plus unfinished transcription before submitting it.
- **Quiet confirmation:** show Listening to Zoro without speaking an acknowledgment aloud.
- **Inside the session:** show the growing draft in whichever session you addressed, not only in the separate transcript channel.
- **Visible processing:** show when audio is still being processed; appearing in a preview must not start the agent.
- **One request, not a pile of turns:** send the complete instruction once; preserve long text even when a preview or the receiving service has a size limit.
- **Wait when busy:** show Queued and preserve accepted request order without stopping the current job.
- **Edit or cancel:** offer buttons beneath an unsubmitted draft; opening Edit pauses automatic sending, and Cancel prevents later pieces from reviving it.
- **Save is not Send:** after editing, Save keeps your words as a draft until you press the separate Send button; waiting longer does not send it.
- **Unclear names:** preserve an addressed but unresolved request in the existing transcript channel with Choose Session; do not guess the previous or nearest-sounding session.
- **Manual Retry only:** on delivery failure, keep the words with Retry, Edit and Cancel; do not automatically resend when connectivity returns.
- **No duplicate jobs:** retrying the same request must recover any existing acceptance instead of starting it again when a response was lost.
- **Owner only:** other people may appear in the normal transcript but cannot submit or alter your requests.
- **Keep existing routing boundaries:** mentioning another character during one addressed thought must not redirect it; later unaddressed speech must not silently go to the previous session.

## Keep the first fix simple

First address the reproduced problem: collect the request's pending words, wait for ten seconds of actual silence and send the complete text once. Use the existing voice service and delivery path. The related listening-message error is a separate small repair in the same area.

Restart recovery is out of scope at Drew's request: leave existing behavior alone, without a new recovery screen or automatic resending. Do not promise that an unfinished draft survives a restart. This is no longer an open decision or blocker.

Keep the earlier draft-button and display choices recorded for later; do not silently discard them or build them all as part of the missing-sentence fix. Editing-arrival and unclear-name details matter only when their respective controls are built, not now. Use conservative, simple engineering choices and ask only about meaningful changes to Drew's workflow.

## Small work items — local repair done, later controls not approved

Each numbered outcome is intended for one normal session, roughly 15–30 minutes, with a failing test first and a passing check before it is considered done. If an item cannot fit, split it before building; do not silently turn it into a larger task. Only one item is worked on at a time, after your approval.

- [x] **1. Make capture progress visible to routing:** expose the owner's active speech and pending recording chunks through a small tested interface.
  - Check: simulated capture start, split and finish keep the correct pending work visible; no live microphone is required.
  - Main area: the voice transport, queue and their tests.
- [x] **2. Fix the missing final sentence:** use that progress to hold a request until its own pending words finish, without merging a later request separated by real silence.
  - Depends on 1; check the exact 1.788-second incident with a fake clock and delayed transcription, and confirm one complete send.
  - Main area: the request controller and silence tests.
- [x] **3. Fix the listening-message race:** prevent a slow acknowledgment from accessing a request that has already completed.
  - Check: delayed message posting and an immediately due send produce neither an exception nor a duplicate job.
  - Main area: the request controller and its feedback tests.

### Later controls and checks — not required for the first repair

These retain earlier preferences, not approval to build a larger system. Review each only when needed; do not add restart recovery as a dependency.

- [ ] **4. Keep a retryable draft record:** save recognized parts, the chosen session, revision and current status independently from the transcript log.
  - Check: a delivery error does not erase the draft; storing a draft starts no agent.
  - This supports manual delivery controls, not a new restart-recovery workflow.
  - Main area: the optional voice extension's store and controller.
- [ ] **5. Track an accepted request once:** add a reusable delivery receipt so an unchanged retry can be recognized safely.
  - Check: duplicate admission and a lost reply return the same receipt; changing an already accepted request is not treated as a retry.
  - Main area: relay request storage and focused Python tests.
- [ ] **6. Connect receipts to the session queue:** report accepted, queued and started accurately while reusing the existing one-job-at-a-time execution path.
  - Depends on 5; check two requests to a busy session run once each, in order, without interrupting existing work.
  - Main area: the spoken endpoint and shared session delivery path.
- [ ] **7. Display the live draft inside the addressed thread:** update one message with listening, recognized text and processing states.
  - Depends on 4; check bot-authored previews cannot start chat or impersonate an agent handoff, and a failed display update preserves the request.
  - Main area: optional voice UI wiring, controller and tests.
- [ ] **8. Add owner-only Cancel:** stop an unsubmitted draft without cancelling another request or reviving it when a late transcript arrives.
  - Depends on 4 and 7; check double-clicks, other users and late audio.
  - Main area: voice draft interactions and state transitions.
- [ ] **9. Add owner-only Edit:** open the editor with sending paused and protect edited text from delayed transcription updates.
  - Depends on 4 and 7; Save retains the edited draft until the owner presses Send, including after the silence deadline passes.
  - Presentation of speech arriving while the editor is open still needs a decision; never silently overwrite saved text with delayed recognition results.
  - Main area: voice draft interactions and tests.
- [ ] **10. Add manual Retry:** display delivery failure, retain the text and reconcile the receipt only when the owner chooses Retry.
  - Depends on 4–6 and 7; check restored connectivity alone sends nothing and a lost acceptance reply does not create another job.
  - Main area: the voice relay client, draft controls and tests.
- [ ] **11. Add the unclear-name chooser:** show a saved addressed request in the transcript channel, then move its preview to the session the owner chooses.
  - Depends on 4 and 7; blocked on the addressing-detection boundary, not on a new channel decision.
  - Check: ordinary conversation does not generate alerts, and choosing a destination does not itself run an agent.
- [ ] **12. Make long requests explicit and lossless:** prevent preview clipping or endpoint limits from silently removing the end of a request.
  - Check: text above the current 4,000-character endpoint limit is either supported as one complete request or retained with a clear size error and controls.
  - Main area: draft rendering, submission validation and boundary tests.
- [ ] **13. Reconcile the voice documentation:** describe one agreed workflow and remove the contradictory ninety-second continuation description.
  - Check: the README matches the approved draft locations, timing and manual retry policy.
- [ ] **14. Verify one end-to-end offline request:** exercise delayed audio, a busy session, editing/cancellation and a failed delivery through test doubles rather than a paid agent.
  - Depends on the approved implemented items; check the complete final text and the number of agent admissions, not merely successful UI updates.
- [ ] **15. Prepare an approved Discord try-out:** check the Python bot and separate voice-worker code versions, then agree on any switch or restart and a rollback route.
  - Do not activate, restart, start paid agents or publish as a side effect of preparing the plan.
  - Check: both services use the intended tested copies, with no unrelated changes included.

## How to try it

These checks are for a later approved voice-runtime try-out, not the current unchanged live bot. Each is a short independent check; growing drafts, new buttons and queue receipts are not implemented.

1. Address Zoro and speak two short sentences a second apart; both should arrive together after silence and transcription finish.
2. Stop talking for ten seconds; check that the instruction arrives once, not as two agent requests.
3. After the previous request finishes collecting, address Nami with a new request; confirm it goes to Nami rather than joining Zoro's request.

## What has and has not happened

- The approved missing-sentence repair is implemented locally, including the closely related listening-message race and separation of delayed requests after real pauses.
- The formal requirements retain Save-until-Send and the other later preferences; they are not claims that those controls exist. A bounded repair design and task checklist now exist; the wider change is not complete.
- Test-first reproduction failed before the source fix; afterward 145 voice tests and `make verify` (6,067 project tests, formatting, lint and types) passed.
- No live model call, bot/voice-worker restart, deployment, GitHub push or PR was performed. The live voice companion still uses its old copy.
- No existing conversations, audio, personal configuration or unrelated files were changed.
