Check: `npm --prefix extensions/voice_transcripts test`

Try: `npm --prefix extensions/voice_transcripts test`

## 1. Approved local missing-sentence repair

- [x] 1.1 Write failing offline tests for the reported delayed final sentence, active capture and separate real-pause requests; confirm failures before source edits.
- [x] 1.2 Expose scoped unfinished-job and active-capture metadata and use it to gate automatic sending; verify the regression and existing voice tests pass, including unrelated-speaker/session isolation.
- [x] 1.3 Verify the runtime wiring offline, run the full project quality gate, and save the fix locally without switching or restarting either service; record commands and results.

## 2. Later preferences — shelved, not an execution queue

Drew ended legacy-voice development on 2026-09-27 after the manual join/leave
change. The items below are historical, unimplemented preferences, not pending
approved tasks; do not resume them without a new request. Jester idea saving was
canceled entirely. Separate Jester Voice is explicitly out of scope.

- Shelved: live in-session drafts with accurate queue receipts; previews would never invoke an agent or interrupt busy work.
- Shelved: owner-only Edit, Save-until-Send and Cancel, without timers or late words bypassing owner actions.
- Shelved: manual Retry and unclear-destination choice, without duplicate admission, guessing or automatic resend.
- Shelved: long-request limits and complete documentation for those unbuilt controls.
- The missing-sentence repair's Discord try-out already happened and Drew confirmed it worked; no repeat test is required for that repair.

## How to try it

For a later approved live try-out: say a character name and two sentences with a short pause; confirm both arrive in one request; after a genuine ten-second pause, address a different character and confirm the requests stay separate. These are not claims that the live service has changed.
